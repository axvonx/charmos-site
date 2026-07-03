#!/usr/bin/env python3
"""Tests for index_clang.py — the clang-accurate semantic index.

These exercise real libclang parsing against the C fixtures in tests/fixtures/,
which contain functions, a struct with an anonymous nested union + function
pointer member, an enum, typedefs, macros, a global, and cross-file references.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from index_clang import SymbolIndex, index_files  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CLANG_ARGS = ["-I", str(FIXTURES), "-std=c11"]


@pytest.fixture(scope="module")
def idx() -> SymbolIndex:
    return index_files([str(FIXTURES / "impl.c")], args=CLANG_ARGS, root=str(FIXTURES))


@pytest.fixture(scope="module")
def by_name(idx):
    return {s.name: s for s in idx.symbols.values()}


# ── symbol extraction ────────────────────────────────────────────────────────


class TestSymbols:
    def test_functions_resolved_to_definition(self, by_name):
        # Definitions live in impl.c, not the api.h prototypes.
        assert by_name["point_compare"].kind == "function"
        assert by_name["point_compare"].file == "impl.c"
        assert by_name["point_compare"].is_definition is True
        assert by_name["widget_create"].file == "impl.c"

    def test_records_and_typedefs(self, by_name):
        assert by_name["widget"].kind == "struct"
        assert by_name["point"].kind == "struct"
        assert by_name["color"].kind == "enum"
        assert by_name["compare_fn"].kind == "typedef"
        assert by_name["widget_t"].kind == "typedef"

    def test_enum_constants(self, by_name):
        assert by_name["COLOR_RED"].kind == "enum_constant"
        assert by_name["COLOR_BLUE"].kind == "enum_constant"

    def test_macros(self, by_name):
        assert by_name["FIXTURE_MAX"].kind == "macro"
        assert by_name["FIXTURE_SQUARE"].kind == "macro"

    def test_global_variable_indexed(self, by_name):
        assert by_name["widget_registry"].kind == "variable"

    def test_struct_fields_not_indexed(self, by_name):
        # Members and function-locals are noise / name-collision sources.
        assert "x" not in by_name
        assert "y" not in by_name
        assert "w" not in by_name  # function-local static

    def test_anonymous_union_has_no_named_symbol(self, by_name):
        assert not any(n.startswith("union (unnamed") for n in by_name)

    def test_symbol_locations_point_into_headers(self, by_name):
        # Types are declared in types.h even though the TU is impl.c.
        assert by_name["widget"].file == "types.h"
        assert by_name["compare_fn"].file == "types.h"


# ── cross-file reference resolution ──────────────────────────────────────────


class TestReferences:
    def test_type_ref_from_header_resolves_to_definition(self, idx, by_name):
        # api.h:8 uses `struct widget`; it must resolve to the types.h USR.
        widget_usr = by_name["widget"].usr
        api_refs = [r for r in idx.references if r.usr == widget_usr and r.file == "api.h"]
        assert api_refs, "expected api.h to reference struct widget"

    def test_call_expr_resolves_across_file(self, idx, by_name):
        # widget_create() calls point_compare() further down impl.c.
        pc_usr = by_name["point_compare"].usr
        call_refs = [r for r in idx.references if r.usr == pc_usr]
        assert call_refs, "expected a reference to point_compare"

    def test_references_deduplicated(self, idx):
        keys = [(r.usr, r.file, r.line, r.column) for r in idx.references]
        assert len(keys) == len(set(keys))


# ── resolve() name lookup ────────────────────────────────────────────────────


class TestResolve:
    def test_resolve_prefers_definition(self, idx):
        sym = idx.resolve("widget_create")
        assert sym is not None
        assert sym.is_definition is True
        assert sym.file == "impl.c"

    def test_resolve_unknown_returns_none(self, idx):
        assert idx.resolve("does_not_exist") is None


# ── serialization ────────────────────────────────────────────────────────────


class TestSerialization:
    def test_to_dict_shape(self, idx):
        d = idx.to_dict()
        assert set(d) == {"symbols", "by_name", "references"}
        # by_name maps to lists of USRs that exist in symbols.
        for usrs in d["by_name"].values():
            for usr in usrs:
                assert usr in d["symbols"]

    def test_save_roundtrip(self, idx, tmp_path):
        import json

        out = tmp_path / "index.json"
        idx.save(out)
        loaded = json.loads(out.read_text())
        assert loaded["symbols"]
        assert loaded == idx.to_dict()
