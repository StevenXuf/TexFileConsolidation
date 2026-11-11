# TexFileConsolidation

Consolidate LaTeX sections, optionally embed the bibliography, and package the
source, PDF, and local assets. Supports pdfLaTeX, XeLaTeX, and LuaLaTeX with
BibTeX/Biber detection.

## Setup

Requires Bash, Python 3.8+, and a TeX distribution with your chosen engine,
`kpsewhich`, and BibTeX/Biber as needed. No extra Python packages are required.

```bash
git clone https://github.com/StevenXuf/TexFileConsolidation.git
cd TexFileConsolidation
```

Keep `texbundler.sh` and `texbundler.py` together.

## Usage

Run from the repository folder:

```bash
# pdfLaTeX with an embedded bibliography (defaults)
bash texbundler.sh /path/to/main.tex

# XeLaTeX with a separate .bib file
bash texbundler.sh --engine xelatex --external-bibliography /path/to/main.tex
```

From another folder, use `bash /path/to/TexFileConsolidation/texbundler.sh ...`.
Quote paths containing spaces.

| Flag | Effect |
| --- | --- |
| `--engine ENGINE` | `pdflatex` (default), `xelatex`, or `lualatex`. |
| `--include-bibliography` | Embed references in a `filecontents*` block; default. |
| `--external-bibliography` | Keep bibliography commands and package `.bib` files separately. Alias: `--no-include-bibliography`. |
| `--output-dir /path/to/output` | Save elsewhere; default: beside the input file. |
| `--biber /path/to/biber` | Select a Biber executable. |
| `--bibtex /path/to/bibtex` | Select a BibTeX executable. |

Both bibliography modes retain the references in the PDF. Embedded mode needs
only LaTeX to rebuild; external mode also needs Biber/BibTeX. The tool performs
the build cycle automatically and handles the known Apple Silicon Biber launcher
failure using a temporary ARM64 copy.

## Outputs

For `main.tex`:

- `bundle-main.tex`: consolidated source.
- `bundle-main.pdf`: verified PDF.
- `bundle-main.zip`: source, PDF, required local assets, font licenses, build
  instructions, and a SHA-256 manifest.
- `bundle-main-build.log`: diagnostics.

Share the ZIP to include images, fonts, and custom classes. Recipients still
need the standard TeX packages used by the paper. Keep local dependencies under
the project folder and use relative paths.

Original files remain unchanged. Successful reruns replace generated outputs;
use different output folders to retain both bibliography modes. The tool
preserves paragraph breaks, whitespace-sensitive `%` characters, and literal
content while removing comment text. Each package is compiled in isolation.
Shell escape is disabled.

## Strip comments only

```bash
bash texbundler.sh --strip-comments < main.tex > stripped-main.tex
```
