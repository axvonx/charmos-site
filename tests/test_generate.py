#!/usr/bin/env python3
"""Tests for generate.py — pipeline helpers."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import generate  # noqa: E402


class TestOrphanHeaderEntries:
    def test_uncovered_headers_become_tus_with_real_flags(self, tmp_path):
        inc = tmp_path / "include" / "mem"
        inc.mkdir(parents=True)
        (inc / "used.h").write_text("")
        (inc / "orphan.h").write_text("")
        src = str(tmp_path / "kernel" / "a.c")
        entries = [{"directory": "/build", "file": src, "arguments": ["cc", "-Iinclude", "-c", src]}]

        out = generate._orphan_header_entries(entries, tmp_path, lambda rel: rel == "include/mem/used.h")

        orphan = str(inc / "orphan.h")
        assert out == [
            {"directory": "/build", "file": orphan, "arguments": ["cc", "-Iinclude", "-c", orphan]}
        ]

    def test_no_c_template_no_entries(self, tmp_path):
        assert generate._orphan_header_entries([], tmp_path, lambda rel: False) == []
