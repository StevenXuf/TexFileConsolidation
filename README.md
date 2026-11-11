# TexFileConsolidation

`texbundler.sh` builds a paper, merges its section inputs, optionally embeds its
bibliography, and creates a portable ZIP with its local dependencies. It supports
pdfLaTeX, XeLaTeX, and LuaLaTeX, with automatic BibTeX/Biber detection.

## Setup

Install Python **3.8 or newer** and a TeX distribution providing your chosen engine,
`kpsewhich`, and BibTeX/Biber as needed. Keep `texbundler.sh` and
`texbundler.py` together. No additional Python packages or compiled
`flatex` binary are required.

Clone the repository and add its folder to your current shell's PATH:

```bash
git clone https://github.com/StevenXuf/TexFileConsolidation.git
cd TexFileConsolidation
export PATH="$PWD:$PATH"
```

For future Terminal sessions, add `export PATH="/absolute/path/to/TexFileConsolidation:$PATH"`
to `~/.zshrc`, replacing the path with your clone's location. Alternatively, invoke
`bash /path/to/TexFileConsolidation/texbundler.sh` directly.

## Basic usage

For an ordinary pdfLaTeX project:

```bash
cd /path/to/your/latex-project
texbundler.sh paper.tex
```

For a XeLaTeX project:

```bash
texbundler.sh --engine xelatex /path/to/your/latex-project/main.tex
```

For LuaLaTeX:

```bash
texbundler.sh --engine lualatex paper.tex
```

## Choose how to handle the bibliography

Use `--include-bibliography` to embed the compiled bibliography in a
`filecontents*` block before `\documentclass`. This is the default, so existing
commands retain their behavior:

```bash
texbundler.sh --engine xelatex --include-bibliography main.tex
```

Use `--external-bibliography` to omit that generated block and keep the original
bibliography commands, such as `\addbibresource{bibliography.bib}` and
`\printbibliography`. The references still appear in the PDF. The `.bib` files
and any local BibTeX style are included separately in the ZIP:

```bash
texbundler.sh --engine xelatex --external-bibliography main.tex
```

`--no-include-bibliography` is an alias for `--external-bibliography`. The two
modes use the same output names; set different `--output-dir` folders to keep
both versions.

When compiling an extracted external-bibliography package, run the engine once,
then Biber (or BibTeX), then the engine twice. For this project's Biber setup:

```bash
xelatex bundle-main.tex
biber bundle-main
xelatex bundle-main.tex
xelatex bundle-main.tex
```

The bundler itself runs this cycle automatically and handles the macOS Biber
workaround described below. Each ZIP includes instructions for its selected mode.

## Outputs

The tool automatically performs the LaTeX/bibliography build cycle. It builds in
temporary directories and leaves original manuscript sources and their build
artifacts alone. Paths may be relative or absolute and may contain spaces.

Outputs beside the input file (or in `--output-dir /path/to/output`):

- `bundle-paper.tex`: consolidated text with the selected bibliography mode.
- `bundle-paper.pdf`: PDF compiled from the isolated package.
- `bundle-paper.zip`: the source, PDF, required local assets, font licenses,
  build instructions, and a SHA-256 file manifest.
- `bundle-paper-build.log`: build diagnostics, also written on build failures.

The tool replaces these generated outputs on successful reruns. Failed builds do
not replace a previous bundle. Auxiliary files are kept in temporary directories.

## What is preserved and included

- With `--include-bibliography`, BibTeX and BibLaTeX/Biber bibliographies are
  embedded using `filecontents*` and `\jobname.bbl`. A recipient compiles the
  bundle twice without running a bibliography processor or supplying a `.bib`.
  With `--external-bibliography`, bibliography resources remain separate and
  the recipient runs Biber/BibTeX as well as the engine.
- True blank lines, paragraph breaks, escaped percent signs, and the bare `%`
  characters needed to suppress TeX whitespace are preserved. Comment text is
  removed, except in literal bodies and the two required BibLaTeX signature lines.
  Standard verbatim/listing environments, `\verb`, and braced `\url`/`\path`
  content are protected.
- LaTeX's `.fls` dependency recorder collects local classes, styles, graphics,
  data files, and dynamically loaded inputs. Fontspec log records additionally
  collect local font files that XeTeX omits from `.fls`.
- Static `\input` and `\include` section references are flattened. Dynamic or
  inactive input commands are preserved; any dependencies actually read by the
  engine are packaged. `\include` page breaks are retained.
- The package is compiled separately without project-specific `TEXINPUTS`,
  `BIBINPUTS`, or `BSTINPUTS`. Unresolved citations/references and dependencies
  that still point outside the package cause an error.

Share the **ZIP** when moving the project. A standalone `.tex` file cannot contain
binary images or fonts; the ZIP preserves their relative paths. A recipient still
needs a TeX distribution with the paper's standard packages installed. Keep custom
assets under the project folder and refer to them with relative paths. Dependencies
outside the project are reported so they can be moved into it before packaging.
Shell escape is disabled; pre-generate any figures that would require external
commands. This tool does not interpret arbitrary TeX catcode changes or macros.

## Biber on Apple Silicon

Some installed universal Biber launchers fail while extracting their ARM64 binary.
On this specific failure, the tool automatically extracts and uses an ARM64 copy
in a temporary directory using the installed `lipo`. It does not modify TeX Live.
To select a different bibliography executable explicitly:

```bash
texbundler.sh --engine xelatex --biber /path/to/biber paper.tex
texbundler.sh --bibtex /path/to/bibtex paper.tex
```

## Comment filter

The built-in filter can also be used independently:

```bash
python3 /path/to/TexFileConsolidation/texbundler.py --strip-comments < paper.tex > stripped-paper.tex
```
