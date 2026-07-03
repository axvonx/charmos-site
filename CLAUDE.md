# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is the documentation site for **charmOS**, an operating system project. It consists of a Python-based build pipeline that auto-generates API reference documentation from C header files, and an Astro/Starlight static site that serves the result.

## Architecture

The build pipeline has three stages:

1. **`generate.py`** — Orchestrator. Clones the [charmos source repo](https://github.com/axvonx/charmos), runs the parse/compile stages, generates the command-line guide, then **assembles the final Astro content collection** at `site/src/content/docs/`.
2. **`make_json.py`** — Parses each C header/source file using tree-sitter into a JSON representation (structs, enums, typedefs, functions, defines, and embedded "idea" doc-comments with `@idea:` annotations).
3. **`make_md.py`** — Reads all JSON files and compiles them into `.mdx` pages with Starlight frontmatter, cross-linked types, GitHub source links, and Astro component imports (Badge, Card, Aside, Icon, Tabs).
4. **`make_cmdline.py`** — Scans the source for `CMDLINE_ENTRY_DECLARE(...)` sites and emits the `guides/cmdline.mdx` table of boot parameters.

### Content sources & assembly

The final docs tree at `site/src/content/docs/` is **build output — never edit it by hand** (it is gitignored and wiped on every run). It is assembled by `generate.py`'s `assemble_site_content()` from two sources:

- **`content/`** — resident, hand-authored pages. This tree mirrors the final layout 1:1: `content/index.mdx` (splash), `content/reference/index.mdx`, `content/guides/index.mdx`, etc. To add a hand-written guide, drop an `.mdx` file in `content/guides/`.
- **`docs/`** — generated MDX: API reference pages (→ `reference/`) and generated guides like `cmdline.mdx` (→ `guides/`), overlaid on top of the resident pages.

The Astro site lives in `site/` and uses **Starlight** with the **starlight-theme-obsidian** plugin and **Catppuccin** color theme. Config is in `site/astro.config.mjs`; both the Reference and Guides sidebar sections `autogenerate` from their directories, so new pages appear automatically.

## Build Commands

```bash
# Full pipeline: clone charmos repo, parse C sources, generate MDX docs, and
# assemble everything into site/src/content/docs/ (no manual copying needed).
pip install -r requirements.txt
python3 generate.py

# Build / preview the site
cd site
npm install
npm run build      # production build
npm run dev        # dev server
```

## Key Conventions

- Source files are parsed only from `include/` in the charmos repo (the `SOURCE_DIRS` config in `generate.py`). `uACPI` and `flanterm` directories are ignored.
- `@title:` comments in source files set the page title; `@idea:(small|big|huge)` comments define documentation sections with metadata (status, audience, author, credits).
- Directory renaming: `dir_doc_name` files in the source tree control the URL slug for each directory on the doc site.
- `index.mdx` files from the source tree are copied as-is to serve as directory landing pages.
- The CI workflow (`.github/workflows/site.yml`) runs the full pipeline and deploys to GitHub Pages on push to main.
