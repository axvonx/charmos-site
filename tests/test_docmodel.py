#!/usr/bin/env python3
"""Tests for docmodel.py — the typed IR loaded from make_json's JSON."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from docmodel import Composite, Function, Macro, Module, Typedef


class TestConstructFromDict:
    def test_composite_and_nested(self):
        c = Composite.from_dict(
            {
                "name": "outer",
                "kind": "struct",
                "size": 16,
                "line": 3,
                "members": [
                    {"name": "a", "type": "int", "offset": 0},
                    {
                        "name": "u",
                        "type": "",
                        "nested": {
                            "kind": "union",
                            "members": [{"name": "x", "type": "int"}],
                        },
                    },
                ],
            }
        )
        assert c.name == "outer" and c.kind == "struct" and c.size == 16
        assert c.members[0].name == "a" and c.members[0].offset == 0
        # Anonymous nested composite is materialized as a Composite.
        assert c.members[1].nested.kind == "union"
        assert c.members[1].nested.members[0].name == "x"

    def test_typedef_fn_ptr(self):
        t = Typedef.from_dict(
            {
                "name": "cmp_t",
                "fn_ptr": {
                    "return_type": "int",
                    "parameters": [{"type": "void *", "name": "a"}],
                },
                "line": 7,
            }
        )
        assert t.fn_ptr is not None
        assert t.fn_ptr.return_type == "int"
        assert t.fn_ptr.parameters[0].type == "void *"

    def test_function_defaults(self):
        f = Function.from_dict({"name": "f"})
        assert f.return_type == "" and f.parameters == [] and f.qualifiers == []

    def test_macro_object_vs_function_like(self):
        obj = Macro.from_dict({"name": "N", "value": "1"})
        fn = Macro.from_dict({"name": "M", "params": "(a, b)", "value": "a"})
        assert obj.params is None
        assert fn.params == "(a, b)"


class TestModuleFromJson:
    def _data(self):
        return {
            "file": "charmos/include/x.h",
            "title": "X",
            "c_parse": {
                "functions": [{"name": "do_it"}, {"name": ""}],
                "defines": [{"name": "MAX"}, {"name": "MIN"}],
                "types": {
                    "structs": [
                        {"name": "thing", "kind": "struct"},
                        {"name": "onion", "kind": "union"},
                        {"name": "none", "kind": "struct"},  # literal 'none' → dropped
                        {"name": "", "kind": "struct"},  # unnamed → dropped
                    ],
                    "enums": [{"name": "color"}, {"name": "None"}],
                    "typedefs": [{"name": "handle_t"}, {"name": ""}],
                    "globals": [{"name": "g_flag"}, {"name": ""}],
                },
            },
        }

    def test_split_and_filter(self):
        m = Module.from_json(self._data())
        assert m.file == "charmos/include/x.h" and m.title == "X"
        # struct/union split by kind; 'none' + unnamed dropped
        assert [s.name for s in m.structs] == ["thing"]
        assert [u.name for u in m.unions] == ["onion"]
        # 'None' enum (case-insensitive 'none') dropped
        assert [e.name for e in m.enums] == ["color"]
        # unnamed function/typedef/global dropped
        assert [f.name for f in m.functions] == ["do_it"]
        assert [t.name for t in m.typedefs] == ["handle_t"]
        assert [v.name for v in m.variables] == ["g_flag"]
        # macros are NOT name-filtered (rendered as-is)
        assert [mac.name for mac in m.macros] == ["MAX", "MIN"]

    def test_empty_json_is_safe(self):
        m = Module.from_json({})
        assert m.file == "" and m.structs == [] and m.macros == []
