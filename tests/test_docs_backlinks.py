#!/usr/bin/env python3
"""Tests for the Woboq source→docs backlink injection in generate.py.

Each generated source page in the Woboq browser gets a "Go to docs" button back
to its reference page. The join is built from the assembled MDX (slug + the
`page-source-path` the page documents), which matches the Woboq page's own path.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import generate

# A minimal reference page as make_md emits it: `slug:` frontmatter + the
# page-source-header pill carrying the documented file path.
MDX = """---
title: "IRQLs"
slug: reference/scheduling-and-multitasking/irql
---

<div class="page-source">
  <code class="page-source-path">include/sch/irql.h</code>
</div>

body
"""

# A stripped-down Woboq page: the header holds only the breadcrumb <h1>.
WOBOQ_HTML = (
    "<!doctype html>\n<html>\n<head>\n<title>irql.h</title>\n</head>\n"
    "<body><div id='header'><h1 id='breadcrumb'>"
    "<a href='./'>sch</a>/<a href='irql.h.html'>irql.h</a></h1></div>\n"
    "<hr/><div id='content'></div></body></html>"
)


def _wire(monkeypatch, tmp_path):
    """Point generate's SITE_DOCS / SOURCE_BROWSER_OUT at a scratch tree and lay
    down one reference page + its matching Woboq HTML."""
    site_docs = tmp_path / "docs"
    browser_out = tmp_path / "source"
    monkeypatch.setattr(generate, "SITE_DOCS", site_docs)
    monkeypatch.setattr(generate, "SOURCE_BROWSER_OUT", browser_out)
    monkeypatch.setattr(generate, "USE_COLOR", False)

    mdx = site_docs / "reference" / "Scheduling and Multitasking" / "irql.mdx"
    mdx.parent.mkdir(parents=True)
    mdx.write_text(MDX, encoding="utf-8")

    html = browser_out / generate.SOURCE_BROWSER_PROJECT / "include" / "sch" / "irql.h.html"
    html.parent.mkdir(parents=True)
    html.write_text(WOBOQ_HTML, encoding="utf-8")
    return html


class TestSourceToDocMap:
    def test_maps_source_path_to_doc_url(self, monkeypatch, tmp_path):
        _wire(monkeypatch, tmp_path)
        mapping = generate._build_source_to_doc_map()
        assert mapping == {"include/sch/irql.h": "/reference/scheduling-and-multitasking/irql/"}

    def test_empty_when_no_reference_tree(self, monkeypatch, tmp_path):
        monkeypatch.setattr(generate, "SITE_DOCS", tmp_path / "nope")
        assert generate._build_source_to_doc_map() == {}

    def test_skips_page_without_source_path(self, monkeypatch, tmp_path):
        _wire(monkeypatch, tmp_path)
        # A page with a slug but no page-source-path must not appear.
        orphan = generate.SITE_DOCS / "reference" / "orphan.mdx"
        orphan.write_text("---\nslug: reference/orphan\n---\n", encoding="utf-8")
        mapping = generate._build_source_to_doc_map()
        assert "include/sch/irql.h" in mapping
        assert all("orphan" not in v for v in mapping.values())


class TestInjectDocsBacklinks:
    def test_injects_button_and_style(self, monkeypatch, tmp_path):
        html_path = _wire(monkeypatch, tmp_path)
        generate.inject_docs_backlinks()
        out = html_path.read_text(encoding="utf-8")
        assert 'class="charm-docs-link"' in out
        assert 'href="/reference/scheduling-and-multitasking/irql/"' in out
        assert "Go to docs" in out
        # Button lands inside the header, right after the breadcrumb heading.
        assert '</h1><a class="charm-docs-link"' in out
        # The shared stylesheet is linked (not inlined) and written to disk.
        assert '<link rel="stylesheet" href="/source/charm-docs-link.css"/>' in out
        assert (generate.SOURCE_BROWSER_OUT / generate.DOCS_BACKLINK_CSS_NAME).exists()

    def test_idempotent(self, monkeypatch, tmp_path):
        html_path = _wire(monkeypatch, tmp_path)
        generate.inject_docs_backlinks()
        once = html_path.read_text(encoding="utf-8")
        generate.inject_docs_backlinks()
        twice = html_path.read_text(encoding="utf-8")
        assert once == twice
        assert twice.count("charm-docs-link") == once.count("charm-docs-link")

    def test_skips_when_no_browser(self, monkeypatch, tmp_path):
        # docs exist but the source browser was never built → no-op, no crash.
        site_docs = tmp_path / "docs"
        (site_docs / "reference").mkdir(parents=True)
        monkeypatch.setattr(generate, "SITE_DOCS", site_docs)
        monkeypatch.setattr(generate, "SOURCE_BROWSER_OUT", tmp_path / "source")
        monkeypatch.setattr(generate, "USE_COLOR", False)
        generate.inject_docs_backlinks()  # must not raise
