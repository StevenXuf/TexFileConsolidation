#!/usr/bin/env python3
"""Build, consolidate, and package a LaTeX project using recorded dependencies."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET


LITERAL_ENVS = {'verbatim', 'verbatim*', 'Verbatim', 'Verbatim*', 'BVerbatim',
                'LVerbatim', 'lstlisting', 'minted', 'comment',
                'filecontents', 'filecontents*'}
CONTROL = re.compile(r'\\[a-zA-Z@]+|\\[\s\S]|%')


def tokens(source):
    """Yield spans, treating literal bodies and inline verbatim as opaque."""
    pos = 0
    while match := CONTROL.search(source, pos):
        start, end = match.span()
        if start > pos:
            yield pos, start, 'text'
        token = match.group()
        kind = 'command'
        if token == '%':
            newline = source.find('\n', end)
            end = len(source) if newline < 0 else newline + 1
            kind = 'comment'
        elif token in (r'\verb', r'\lstinline'):
            if source[end:end+1] == '*':
                end += 1
            if end < len(source) and not source[end].isspace():
                close = source.find(source[end], end + 1)
                if close >= 0:
                    end, kind = close + 1, 'literal'
        elif token == r'\begin':
            env = re.match(r'\s*\{([^{}]+)\}', source[end:])
            if env and env[1] in LITERAL_ENVS:
                closing = '\\end{' + env[1] + '}'
                close = source.find(closing, end + env.end())
                if close < 0:
                    raise ValueError(f'Unclosed literal environment: {env[1]}')
                end, kind = close + len(closing), 'literal'
        elif token in (r'\url', r'\nolinkurl', r'\path'):
            if source[end:end+1] == '{':
                close = source.find('}', end + 1)
                if close >= 0:
                    end, kind = close + 1, 'literal'
        yield start, end, kind
        pos = end
    if pos < len(source):
        yield pos, len(source), 'text'


def strip_comments(source):
    output = []
    for start, end, kind in tokens(source):
        if kind != 'comment':
            output.append(source[start:end])
        else:
            prefix = source[source.rfind('\n', 0, start) + 1:start]
            if prefix.strip():
                # A bare percent is TeX syntax: deleting it inserts whitespace.
                output.append('%\n' if source[end-1:end] == '\n' else '%')
    return ''.join(output)


def argument(source, offset, optional=False):
    pos = offset
    while pos < len(source) and source[pos].isspace():
        pos += 1
    if optional and source[pos:pos+1] == '[':
        close = source.find(']', pos + 1)
        if close < 0:
            raise ValueError('Unclosed optional argument')
        return argument(source, close + 1)
    if source[pos:pos+1] != '{':
        return None
    depth, start = 1, pos + 1
    pos = start
    while pos < len(source):
        if source[pos] == '\\':
            pos += 2
            continue
        if source[pos] == '{':
            depth += 1
        elif source[pos] == '}':
            depth -= 1
            if not depth:
                return source[start:pos], pos + 1
        pos += 1
    raise ValueError('Unclosed braced argument')


class Flattener:
    def __init__(self, root, recorded, include_bibliography=True):
        self.root, self.recorded = root, recorded
        self.inlined = set()
        self.include_bibliography = include_bibliography

    def flatten(self, path, stack=()):
        path = path.resolve()
        if path in stack:
            raise ValueError(f'Cyclic LaTeX input: {path}')
        self.inlined.add(path)
        source = strip_comments(path.read_text(encoding='utf-8-sig'))
        result, cursor = [], 0
        for start, end, kind in tokens(source):
            if start < cursor or kind != 'command':
                continue
            command = source[start:end]
            if command not in (r'\input', r'\include', r'\addbibresource'):
                continue
            arg = argument(source, end, optional=command == r'\addbibresource')
            if not arg and command == r'\input':
                bare = re.match(r'\s+([^\s{}%]+)', source[end:])
                if bare:
                    arg = bare[1], end + bare.end()
            if not arg:
                continue
            filename, stop = arg
            if command == r'\addbibresource':
                if not self.include_bibliography:
                    continue
                replacement = ''
            else:
                filename = filename.strip().strip('"')
                # Leave dynamic/conditional inputs intact; the recorder supplies
                # their dependencies instead of guessing TeX macro expansion.
                if any(char in filename for char in '\\#$'):
                    continue
                child = self.root / filename
                if not child.suffix:
                    child = child.with_suffix('.tex')
                if child.resolve() not in self.recorded:
                    continue
                if not child.is_file():
                    raise ValueError(f'Missing LaTeX input: {child}')
                replacement = self.flatten(child, (*stack, path))
                if command == r'\include':
                    replacement = '\\clearpage\n' + replacement + '\n\\clearpage\n'
                elif replacement.endswith('\n') and source[stop:stop+1] == '\n':
                    replacement += '%'
            result.extend((source[cursor:start], replacement))
            cursor = stop
        result.append(source[cursor:])
        return ''.join(result)


def inside(path, folder):
    try:
        path.relative_to(folder)
        return True
    except ValueError:
        return False


def recorded_inputs(fls, cwd):
    paths = set()
    for line in fls.read_text(errors='replace').splitlines():
        if line.startswith('PWD '):
            cwd = Path(line[4:])
        elif line.startswith('INPUT '):
            path = Path(line[6:])
            if not path.is_absolute():
                path = cwd / path
            if path.is_file():
                # Keep symlink names: these are the paths opened by TeX.
                paths.add(Path(os.path.abspath(path)))
    return paths


def font_inputs(log, cwd):
    """XeTeX omits fontspec's font binaries from the .fls recorder."""
    text = re.sub(r'\n\(fontspec\)\s*', '', log.read_text(errors='replace'))
    text = text.replace('\n', '')
    paths = set()
    for name in re.findall(r'\[([^\[\]]+\.(?:ttf|otf|ttc))\]', text, re.I):
        path = Path(name)
        if not path.is_absolute():
            path = cwd / path
        if path.is_file():
            paths.add(Path(os.path.abspath(path)))
    return paths


class Builder:
    def __init__(self, args, log):
        self.args, self.log = args, log
        self.cache = None

    def run(self, argv, cwd, env=None, check=True):
        self.log.append('$ ' + ' '.join(map(str, argv)) + '\n')
        proc = subprocess.run(list(map(str, argv)), cwd=cwd, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, errors='replace')
        self.log.append(proc.stdout)
        if check and proc.returncode:
            raise ValueError(f'Command failed ({proc.returncode}): {argv[0]}\n'
                             + '\n'.join(proc.stdout.splitlines()[-20:]))
        return proc

    def latex(self, source, cwd, output=None, clean=False):
        argv = [self.args.engine, '-interaction=nonstopmode', '-halt-on-error',
                '-no-shell-escape', '-recorder']
        if output:
            argv.append(f'-output-directory={output}')
        env = os.environ.copy()
        env['max_print_line'] = '10000'
        if self.args.engine == 'lualatex':
            self.cache.mkdir(parents=True, exist_ok=True)
            env['TEXMFVAR'] = str(self.cache)
            env['TEXMFCACHE'] = str(self.cache)
        if clean:
            for key in ('TEXINPUTS', 'BIBINPUTS', 'BSTINPUTS'):
                env.pop(key, None)
        self.run([*argv, source], cwd, env)

    def bibliography(self, source, output, temp, clean=False):
        job = source.stem
        env = os.environ.copy()
        if clean:
            for key in ('TEXINPUTS', 'BIBINPUTS', 'BSTINPUTS'):
                env.pop(key, None)
        if (output / f'{job}.bcf').exists():
            backend = 'biber'
            executable = shutil.which(self.args.biber)
            if not executable:
                raise ValueError(f'Biber executable not found: {self.args.biber}')
            executable = str(Path(executable).resolve())
            argv = [executable, '--input-directory', output,
                    '--output-directory', output, job]
            result = self.run(argv, source.parent, env, check=False)
            if result.returncode and 'extracting arm64 binary with lipo failed' in result.stdout:
                if sys.platform != 'darwin' or platform.machine() != 'arm64':
                    raise ValueError(result.stdout)
                lipo = Path('/Library/Developer/CommandLineTools/usr/bin/lipo')
                if not lipo.exists():
                    lipo = shutil.which('lipo')
                if not lipo:
                    raise ValueError('Biber needs a working lipo executable on this Mac.')
                print('Using a temporary ARM64 Biber copy (macOS launcher workaround).', flush=True)
                thin = temp / 'biber-arm64'
                if not thin.exists():
                    self.run([lipo, executable, '-thin', 'arm64', '-output', thin], source.parent)
                    thin.chmod(0o700)
                self.run([thin, *argv[1:]], source.parent, env)
            elif result.returncode:
                raise ValueError('Biber failed:\n' + '\n'.join(result.stdout.splitlines()[-20:]))
        elif any(r'\bibdata{' in aux.read_text(errors='replace') for aux in output.rglob('*.aux')):
            backend = 'bibtex'
            executable = shutil.which(self.args.bibtex)
            if not executable:
                raise ValueError(f'BibTeX executable not found: {self.args.bibtex}')
            for key in ('BIBINPUTS', 'BSTINPUTS'):
                env[key] = str(source.parent) + os.pathsep + env.get(key, '')
            self.run([Path(executable).resolve(), job], output, env)
        else:
            backend = 'none'
        return backend


def tex_roots():
    roots = set()
    for name in ('TEXMFROOT', 'TEXMFDIST', 'TEXMFLOCAL', 'TEXMFSYSCONFIG', 'TEXMFSYSVAR',
                 'TEXMFVAR', 'TEXMFCONFIG'):
        value = subprocess.check_output(['kpsewhich', f'-var-value={name}'], text=True).strip()
        if value:
            roots.add(Path(value).expanduser().resolve())
    return roots


def embedded_bibliography(path):
    if not path.exists():
        return ''
    raw = path.read_text(encoding='utf-8')
    if r'\end{filecontents*}' in raw:
        raise ValueError('Bibliography contains a filecontents terminator.')
    if raw.startswith('% $ biblatex auxiliary file $'):
        lines = raw.splitlines(keepends=True)
        body = ''.join(lines[:2]) + strip_comments(''.join(lines[2:]))
    else:
        body = strip_comments(raw)
    return ('\\begin{filecontents*}[overwrite]{\\jobname.bbl}\n'
            + body.rstrip() + '\n\\end{filecontents*}\n\n')


def bibliography_inputs(source, output, backend):
    """Collect the external resources a recipient's bibliography run needs."""
    resources = set()
    if backend == 'biber':
        control = ET.parse(output / f'{source.stem}.bcf')
        for item in control.iter():
            if item.tag.rsplit('}', 1)[-1] == 'datasource' and item.text:
                name = item.text.strip()
                if item.get('glob') == 'true':
                    import glob
                    resources.update(Path(path) for path in glob.glob(str(source.parent / name)))
                else:
                    resources.add(source.parent / name)
    elif backend == 'bibtex':
        for aux in output.rglob('*.aux'):
            text = aux.read_text(errors='replace')
            for group in re.findall(r'\\bibdata\{([^{}]+)\}', text):
                for name in group.split(','):
                    name = name.strip()
                    name = name if name.endswith('.bib') else name + '.bib'
                    # BibLaTeX's BibTeX backend can generate a job-specific .bib.
                    if (output / name).is_file() and not (source.parent / name).is_file():
                        continue
                    resources.add(source.parent / name)
            for name in re.findall(r'\\bibstyle\{([^{}]+)\}', text):
                style = source.parent / (name + '.bst')
                if style.is_file():
                    resources.add(style)
    for path in resources:
        if not path.is_file():
            raise ValueError(f'Cannot package external bibliography resource: {path}')
    return resources


def bundle(args, log):
    source = Path(args.input).expanduser().resolve(strict=True)
    if source.suffix != '.tex':
        raise ValueError('The input must be a .tex file.')
    root = source.parent
    destination = Path(args.output_dir).expanduser().resolve() if args.output_dir else root
    destination.mkdir(parents=True, exist_ok=True)
    stem = 'bundle-' + source.stem
    builder = Builder(args, log)
    with tempfile.TemporaryDirectory(prefix='texbundler-') as tmp:
        temp = Path(tmp).resolve()
        builder.cache = temp / 'tex-cache'
        original = temp / 'original'
        original.mkdir()
        for path in root.rglob('*.tex'):
            (original / path.relative_to(root).parent).mkdir(parents=True, exist_ok=True)
        print(f'Building {source.name} with {args.engine}...', flush=True)
        builder.latex(source.name, root, original)
        backend = builder.bibliography(source, original, temp)
        builder.latex(source.name, root, original)
        builder.latex(source.name, root, original)
        inputs = recorded_inputs(original / f'{source.stem}.fls', root)
        inputs.update(font_inputs(original / f'{source.stem}.log', root))
        if not args.include_bibliography:
            inputs.update(bibliography_inputs(source, original, backend))
        flattener = Flattener(root, {path.resolve() for path in inputs}, args.include_bibliography)
        body = flattener.flatten(source)
        bbl = original / f'{source.stem}.bbl'
        if backend != 'none' and (not bbl.exists() or not bbl.stat().st_size):
            raise ValueError('The bibliography build did not produce a nonempty .bbl file.')
        consolidated = (embedded_bibliography(bbl) if args.include_bibliography else '') + body
        package = temp / stem
        package.mkdir()
        entry = package / f'{stem}.tex'
        entry.write_text(consolidated, encoding='utf-8')
        systems = tex_roots()
        assets = []
        for path in sorted(inputs):
            resolved = path.resolve()
            if inside(resolved, temp) or resolved in flattener.inlined:
                continue
            if any(inside(resolved, system) for system in systems):
                continue
            if str(path) == '/dev/null':
                continue
            if not inside(path, root):
                raise ValueError(f'Dependency is outside the project: {path}\n'
                                 'Place it under the project folder and use a relative path.')
            relative = path.relative_to(root)
            target = package / relative
            if target == entry:
                raise ValueError(f'Output name conflicts with a dependency: {relative}')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            assets.append(relative.as_posix())
        # Carry font licenses, not unrelated font binaries.
        for asset in list(assets):
            if Path(asset).suffix.lower() not in ('.ttf', '.otf', '.woff', '.woff2'):
                continue
            for parent in (root / asset).parents:
                if not inside(parent, root) or parent == root:
                    break
                for name in ('OFL.txt', 'LICENSE.txt', 'LICENSE', 'COPYING'):
                    license_file = parent / name
                    if license_file.is_file():
                        relative = license_file.relative_to(root)
                        target = package / relative
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(license_file, target)
                        if relative.as_posix() not in assets:
                            assets.append(relative.as_posix())
        print(f'Checking the isolated bundle ({len(assets)} supporting files, {backend})...', flush=True)
        builder.latex(entry.name, package, clean=True)
        if not args.include_bibliography:
            builder.bibliography(entry, package, temp, clean=True)
            builder.latex(entry.name, package, clean=True)
        builder.latex(entry.name, package, clean=True)
        final_log = entry.with_suffix('.log').read_text(errors='replace')
        if 'Label(s) may have changed' in final_log or 'Please rerun LaTeX' in final_log:
            builder.latex(entry.name, package, clean=True)
            final_log = entry.with_suffix('.log').read_text(errors='replace')
        if re.search(r'LaTeX Warning: (?:Citation|Reference|There were undefined)', final_log):
            raise ValueError('The isolated bundle has unresolved citations or references. See the build log.')
        clean_inputs = recorded_inputs(entry.with_suffix('.fls'), package)
        clean_inputs.update(font_inputs(entry.with_suffix('.log'), package))
        for path in clean_inputs:
            if inside(path.resolve(), package) or any(inside(path.resolve(), system) for system in systems):
                continue
            if inside(path.resolve(), builder.cache):
                continue
            raise ValueError(f'The isolated build still reads an external dependency: {path}')
        files = [entry.name, f'{stem}.pdf', *sorted(assets)]
        manifest = {'entrypoint': entry.name, 'engine': args.engine, 'backend': backend,
                    'bibliography': 'embedded' if args.include_bibliography else 'external',
                    'inlined_sources': sorted(str(path.relative_to(root)) for path in flattener.inlined),
                    'files': {name: hashlib.sha256((package / name).read_bytes()).hexdigest()
                              for name in files}}
        (package / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        latex_command = f'{args.engine} -interaction=nonstopmode -halt-on-error {entry.name}\n'
        commands = latex_command
        if not args.include_bibliography and backend != 'none':
            commands += f'{backend} {stem}\n' + latex_command
        commands += latex_command
        if backend == 'none':
            bibliography_note = 'This document does not require a bibliography processor.\n'
        elif args.include_bibliography:
            bibliography_note = ('The compiled bibliography is embedded; Biber/BibTeX and the original '
                                 'bibliography files are not needed.\n')
        else:
            bibliography_note = ('The bibliography is external: keep the included .bib files with the '
                                 'source and run the bibliography processor as shown above.\n')
        (package / 'BUILD.md').write_text(
            '# Build this consolidated paper\n\nFrom this folder run:\n\n```bash\n'
            + commands + '```\n\n'
            'Install a TeX distribution with the packages required by this paper. '
            'Local assets are included; the original section files are not needed.\n\n'
            + bibliography_note)
        archive = temp / f'{stem}.zip'
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as output:
            for name in [*files, 'manifest.json', 'BUILD.md']:
                output.write(package / name, f'{stem}/{name}')
        for path in (entry, entry.with_suffix('.pdf'), archive):
            staging = destination / (path.name + '.tmp')
            shutil.copy2(path, staging)
            os.replace(staging, destination / path.name)
        print(f'Created {destination / (stem + ".tex")}\n'
              f'Created {destination / (stem + ".pdf")}\n'
              f'Created {destination / (stem + ".zip")}', flush=True)
        return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', nargs='?', help='Main .tex file (relative or absolute path)')
    parser.add_argument('--engine', choices=('pdflatex', 'xelatex', 'lualatex'), default='pdflatex')
    parser.add_argument('--biber', default='biber', help='Biber executable or path')
    parser.add_argument('--bibtex', default='bibtex', help='BibTeX executable or path')
    parser.add_argument('--output-dir', help='Output folder (default: beside the main source)')
    bibliography = parser.add_mutually_exclusive_group()
    bibliography.add_argument('--include-bibliography', dest='include_bibliography',
                              action='store_true', help='Embed the compiled bibliography (default)')
    bibliography.add_argument('--external-bibliography', '--no-include-bibliography',
                              dest='include_bibliography', action='store_false',
                              help='Keep bibliography resources as separate files; do not add filecontents')
    parser.set_defaults(include_bibliography=True)
    parser.add_argument('--strip-comments', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.strip_comments:
        sys.stdout.write(strip_comments(sys.stdin.read()))
        return
    if not args.input:
        parser.error('a main .tex file is required')
    log = []
    source = Path(args.input).expanduser().resolve()
    destination = Path(args.output_dir).expanduser().resolve() if args.output_dir else source.parent
    try:
        bundle(args, log)
    except (OSError, ValueError, ET.ParseError, subprocess.SubprocessError) as error:
        print(f'texbundler: {error}', file=sys.stderr)
        raise SystemExit(1)
    finally:
        if log and destination.is_dir():
            (destination / ('bundle-' + source.stem + '-build.log')).write_text(''.join(log))


if __name__ == '__main__':
    main()
