#!/usr/bin/env python3
"""End-to-end render test: SourceBlock produces clickable links in a real build.

This exercises the full vertical — sourceblock.py → SourceBlock.astro → HTML —
by generating an MDX page with real, clang-resolved code and running the Astro
build, then asserting the output HTML contains clickable symbol anchors.

It is slow (runs `npm run build`) and needs the site's node_modules, so it is
skipped automatically when those aren't present. Run explicitly with:
    pytest tests/test_sourceblock_render.py -v
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sourceblock as sb  # noqa: E402

SITE = ROOT / "site"
DOCS = SITE / "src" / "content" / "docs"
CLANG_INDEX = ROOT / "clang_index.json"

pytestmark = pytest.mark.skipif(
    not (SITE / "node_modules" / ".bin" / "astro").exists() or not CLANG_INDEX.exists(),
    reason="requires site/node_modules and a built clang_index.json",
)


def _github(sym):
    return f"https://github.com/axvonx/charmos/blob/main/{sym['file']}#L{sym['line']}"


def test_sourceblock_renders_clickable_links():
    index = json.loads(CLANG_INDEX.read_text())
    resolver = sb.index_resolver(index, _github)

    # Pick a symbol that definitely exists in the kernel index.
    name = next(iter(index["by_name"]))
    code = f"{name}(x);"
    page = [
        "---",
        "title: SB Render Test",
        "---",
        "",
        "import SourceBlock from '@components/SourceBlock.astro';",
        "",
        sb.to_mdx(sb.render(code, resolver), title="render test"),
        "",
    ]

    DOCS.mkdir(parents=True, exist_ok=True)
    test_page = DOCS / "sb_render_test.mdx"
    test_page.write_text("\n".join(page))
    try:
        result = subprocess.run(
            ["npm", "run", "build"],
            cwd=SITE,
            capture_output=True,
            text=True,
            timeout=400,
        )
        assert result.returncode == 0, f"astro build failed:\n{result.stderr[-2000:]}"

        html = (SITE / "dist" / "sb_render_test" / "index.html").read_text()
        # The resolved symbol must appear as a clickable anchor into its source.
        assert (
            'class="tok tok-ident tok-link"' in html or "tok-link" in html
        ), "no linkified symbol tokens in output"
        assert "github.com/axvonx/charmos/blob/main/" in html
    finally:
        test_page.unlink(missing_ok=True)
