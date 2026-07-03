#!/usr/bin/env python3
"""Tests for the make_md ↔ SourceBlock integration (fence vs. component)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import make_md  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_renderer():
    """Each test controls the module-global renderer; restore it afterwards."""
    saved = make_md._CODE_RENDERER
    make_md._CODE_RENDERER = None
    yield
    make_md._CODE_RENDERER = saved


class TestFenceOrSourceBlock:
    def test_fallback_is_plain_fence(self):
        out = make_md.fence_or_sourceblock("int foo(void);")
        assert out == "```c\nint foo(void);\n```"

    def test_uses_renderer_when_present(self):
        make_md._CODE_RENDERER = lambda code, def_name=None, def_href=None: f"<SB:{code}>"
        assert make_md.fence_or_sourceblock("int x;") == "<SB:int x;>"

    def test_definition_name_gets_source_href(self):
        # The construct's own name is pointed at its source definition, while
        # references resolve through the index.
        seen = {}

        def fake(code, def_name=None, def_href=None):
            seen.update(code=code, def_name=def_name, def_href=def_href)
            return "<SB>"

        make_md._CODE_RENDERER = fake
        make_md.fence_or_sourceblock("struct t {};", def_name="t", def_href="/source/x.h.html#5")
        assert seen == {
            "code": "struct t {};",
            "def_name": "t",
            "def_href": "/source/x.h.html#5",
        }


class TestCodeRendererPolicy:
    def test_documented_typedef_links_to_reference_page(self):
        # doc_table keys a typedef by its bare name → internal anchor.
        doc_table = {"thread_t": "/reference/threads/thread#type-alias-thread_t"}
        anchor = make_md.symbol_target(
            {"name": "thread_t", "kind": "typedef", "file": "include/x.h", "line": 5},
            doc_table,
        )
        assert anchor == "/reference/threads/thread#type-alias-thread_t"

    def test_documented_struct_links_by_kind(self):
        # A struct symbol (bare name "thread") must match the "struct thread" key.
        doc_table = {"struct thread": "/reference/threads/thread#struct-thread"}
        anchor = make_md.symbol_target(
            {"name": "thread", "kind": "struct", "file": "include/x.h", "line": 5},
            doc_table,
        )
        assert anchor == "/reference/threads/thread#struct-thread"

    def test_undocumented_symbol_links_to_source(self):
        url = make_md.symbol_target(
            {"name": "irql_raise", "file": "kernel/sch/irql.c", "line": 38}, {}
        )
        assert url == f"{make_md.SOURCE_REPO_URL}/kernel/sch/irql.c#L38"

    def test_source_browser_target_when_enabled(self, monkeypatch):
        monkeypatch.setattr(make_md, "SOURCE_BROWSER_BASE", "/source/charmos")
        url = make_md.symbol_target(
            {"name": "irql_raise", "file": "kernel/sch/irql.c", "line": 38}, {}
        )
        assert url == "/source/charmos/kernel/sch/irql.c.html#38"
