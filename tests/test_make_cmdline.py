#!/usr/bin/env python3
"""Tests for make_cmdline.py — CMDLINE_ENTRY_DECLARE extraction → MDX."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from make_cmdline import (
    collect,
    find_decls,
    parse_entry,
    render_mdx,
    render_row,
    split_top_level,
    unquote,
)

SAMPLE = """
#include <cmdline.h>

/* Not this one: */
#define CMDLINE_ENTRY_DECLARE(n, ...) something(n, __VA_ARGS__)

CMDLINE_ENTRY_DECLARE(root,
                      .desc = "Root filesystem partition to mount at boot",
                      .arg = "<device>", .default_val = NULL,
                      .value = &global.root_partition, .required = true);

CMDLINE_ENTRY_DECLARE(mem,
                      .desc = "Cap on physical memory the allocator will use",
                      .arg = "<hex bytes>",
                      .callback = vmm_mem_cmdline_callback,
                      .default_val = "0x700000000000", .required = false,
                      .value = NULL);
"""


class TestSplitTopLevel:
    def test_ignores_nested_and_strings(self):
        parts = split_top_level('a, .x = f(1, 2), .y = "has, comma"')
        assert parts == ["a", ".x = f(1, 2)", '.y = "has, comma"']


class TestUnquote:
    def test_strips_quotes(self):
        assert unquote('"hi"') == "hi"

    def test_leaves_bare(self):
        assert unquote("NULL") == "NULL"


class TestFindDecls:
    def test_skips_the_macro_definition(self):
        names = [parse_entry(a)["name"] for a, _ in find_decls(SAMPLE)]
        assert names == ["root", "mem"]  # no phantom "n" from the #define


class TestParseEntry:
    def test_full_entry(self):
        args = next(a for a, _ in find_decls(SAMPLE))  # root
        e = parse_entry(args)
        assert e["name"] == "root"
        assert e["arg"] == "<device>"
        assert e["desc"] == "Root filesystem partition to mount at boot"
        assert e["default_val"] is None  # NULL → None
        assert e["required"] is True

    def test_optional_with_default(self):
        args = [a for a, _ in find_decls(SAMPLE)][1]  # mem
        e = parse_entry(args)
        assert e["required"] is False
        assert e["default_val"] == "0x700000000000"


class TestCollect:
    def test_line_numbers_and_paths(self, tmp_path):
        f = tmp_path / "kernel" / "cmdline.c"
        f.parent.mkdir()
        f.write_text(SAMPLE)
        entries = collect(tmp_path)
        by_name = {e["name"]: e for e in entries}
        assert set(by_name) == {"root", "mem"}
        assert by_name["root"]["src_file"] == "kernel/cmdline.c"
        assert (
            by_name["root"]["src_line"]
            == SAMPLE[: SAMPLE.index("CMDLINE_ENTRY_DECLARE(root")].count("\n") + 1
        )


class TestRender:
    def test_row_escapes_and_links(self):
        e = {
            "name": "mem",
            "arg": "<hex>",
            "default_val": "0x1",
            "required": False,
            "desc": "a | b",
            "src_file": "kernel/mem/vmm.c",
            "src_line": 53,
        }
        row = render_row(e)
        assert "[`mem`](https://github.com/axvonx/charmos/blob/main/"
        assert "vmm.c#L53" in row
        assert "a \\| b" in row  # pipe escaped for the table

    def test_mdx_has_table_and_runtime_note(self):
        entries = [
            parse_entry(a) | {"src_file": "x.c", "src_line": 1} for a, _ in find_decls(SAMPLE)
        ]
        mdx = render_mdx(entries)
        assert 'title: "Command-Line Options"' in mdx
        assert "| Option | Value | Default | Required | Description |" in mdx
        assert "`help`" in mdx

    def test_mdx_handles_empty(self):
        mdx = render_mdx([])
        assert "No command-line options were found" in mdx
