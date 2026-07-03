#!/usr/bin/env python3
"""Tests for sourceblock.py — tokenizing + linkifying C via the clang index."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sourceblock as sb  # noqa: E402
from index_clang import index_files  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CLANG_ARGS = ["-I", str(FIXTURES), "-std=c11"]


@pytest.fixture(scope="module")
def index():
    return index_files([str(FIXTURES / "impl.c")], args=CLANG_ARGS, root=str(FIXTURES))


@pytest.fixture(scope="module")
def resolver(index):
    return sb.index_resolver(index, lambda s: f"/source/{s['file']}#L{s['line']}")


def _by_text(segments):
    return {s.text: s for s in segments if s.cls in ("ident", "type")}


# ── tokenization ─────────────────────────────────────────────────────────────


class TestTokenize:
    def test_roundtrips_exactly(self):
        code = "int  x = foo(a, b); /* note */"
        segs = sb.tokenize(code)
        assert "".join(s.text for s in segs) == code

    def test_classes(self):
        segs = sb.tokenize("const int x = 3;")
        cls = {s.text: s.cls for s in segs if s.cls != "ws"}
        assert cls["const"] == "keyword"
        assert cls["int"] == "type"  # primitive_type → type class
        assert cls["x"] == "ident"
        assert cls["3"] == "number"
        assert cls[";"] == "punct"

    def test_type_identifier_classified_as_type(self):
        segs = sb.tokenize("widget_t *w;")
        cls = {s.text: s.cls for s in segs if s.cls != "ws"}
        assert cls["widget_t"] == "type"
        assert cls["*"] == "punct"

    def test_strings_and_comments(self):
        segs = sb.tokenize('char *s = "hi"; // c')
        classes = {s.cls for s in segs}
        assert "string" in classes
        assert "comment" in classes


# ── resolution against the clang index ───────────────────────────────────────


class TestResolve:
    def test_function_call_links_to_definition(self, resolver):
        segs = sb.render("point_compare(a, b);", resolver)
        pc = _by_text(segs)["point_compare"]
        assert pc.href == "/source/impl.c#L6"
        assert pc.symbol == "point_compare"

    def test_type_name_links(self, resolver):
        segs = sb.render("struct widget *w = widget_create(o, c);", resolver)
        by = _by_text(segs)
        assert by["widget"].href == "/source/types.h#L23"
        assert by["widget_create"].href == "/source/impl.c#L15"

    def test_unknown_identifier_not_linked(self, resolver):
        segs = sb.render("int total = frobnicate(x);", resolver)
        by = _by_text(segs)
        assert by["frobnicate"].href is None if "frobnicate" in by else True
        # local/unknown names must never get a link
        assert by["total"].href is None

    def test_keywords_never_link(self, resolver):
        segs = sb.render("return sizeof(int);", resolver)
        for s in segs:
            if s.text in ("return", "sizeof", "int"):
                assert s.href is None


class TestDictResolverBuiltinTypedefs:
    """A builtin type (int32_t, size_t) implicitly re-declared as a file-local
    typedef in multiple files must not resolve to an arbitrary use site."""

    def _index(self):
        return {
            "by_name": {
                "int32_t": ["c:a.h@T@int32_t", "c:b.h@T@int32_t"],
                "pairing_cmp_t": ["c:a.h@T@pairing_cmp_t"],
            },
            "symbols": {
                "c:a.h@T@int32_t": {
                    "kind": "typedef",
                    "file": "a.h",
                    "line": 1,
                    "is_definition": True,
                },
                "c:b.h@T@int32_t": {
                    "kind": "typedef",
                    "file": "b.h",
                    "line": 2,
                    "is_definition": True,
                },
                "c:a.h@T@pairing_cmp_t": {
                    "kind": "typedef",
                    "file": "a.h",
                    "line": 3,
                    "is_definition": True,
                },
            },
        }

    def test_multi_file_builtin_typedef_not_linked(self):
        resolve = sb.index_resolver(self._index(), lambda s: f"/source/{s['file']}#L{s['line']}")
        assert resolve("int32_t") is None

    def test_real_single_file_typedef_still_links(self):
        resolve = sb.index_resolver(self._index(), lambda s: f"/source/{s['file']}#L{s['line']}")
        assert resolve("pairing_cmp_t") == "/source/a.h#L3"


# ── MDX emission ─────────────────────────────────────────────────────────────


class TestToMdx:
    def test_emits_component(self, resolver):
        mdx = sb.to_mdx(sb.render("point_compare(a, b);", resolver), title="example")
        assert mdx.startswith("<SourceBlock")
        assert 'title="example"' in mdx
        assert "segments={" in mdx and mdx.rstrip().endswith("/>")

    def test_segments_are_valid_json(self, resolver):
        mdx = sb.to_mdx(sb.render("irql_thing(x);", resolver))
        payload = mdx.split("segments={", 1)[1].rsplit("}", 1)[0]
        data = json.loads(payload)
        assert isinstance(data, list)
        assert all("text" in seg and "cls" in seg for seg in data)
