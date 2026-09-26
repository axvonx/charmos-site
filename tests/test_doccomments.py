#!/usr/bin/env python3
"""Tests for doccomments.py — comment text → markdown."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from doccomments import comment_to_markdown  # noqa: E402


class TestDiagrams:
    def test_blank_lines_do_not_split_a_diagram(self):
        md = comment_to_markdown(
            "/* ┌────┐\n"
            " * │ a  │\n"
            " * └────┘\n"
            " *\n"
            " *  │\n"
            " *  ▼\n"
            " *\n"
            " * ┌────┐\n"
            " * │ b  │\n"
            " * └────┘\n"
            " */"
        )
        assert md.count("```") == 2
        # Dedented as a whole: the arrow keeps its column under the box.
        assert "\n │\n ▼\n" in md

    def test_prose_between_diagrams_splits_them(self):
        md = comment_to_markdown(
            "/* ┌─┐\n * └─┘\n *\n * Then:\n *\n * ┌─┐\n * └─┘\n */"
        )
        assert md.count("```text") == 2
        assert "Then:" in md
