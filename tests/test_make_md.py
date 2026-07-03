#!/usr/bin/env python3
"""Tests for make_md.py — JSON-to-MDX documentation compilation."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from make_md import (
    _dir_name_to_slug,
    assemble_page_text,
    build_type_table,
    convert_blockquotes_to_asides,
    convert_h2_to_header_with_icon,
    extract_mdx_title,
    format_enum_as_c_code,
    format_function_signature,
    format_struct_as_c_code,
    generate_github_link_safe,
    link_bugs_in_md,
    link_commits_in_md,
    link_files_in_md,
    link_functions_in_md,
    merge_changelog_and_notes,
    normalize_type_name,
    status_to_badge,
)

# ── convert_blockquotes_to_asides ────────────────────────────────────────────


class TestConvertBlockquotesToAsides:
    def test_basic_blockquote(self):
        md = "> Some note text"
        result = convert_blockquotes_to_asides(md)
        assert '<Aside type="note">' in result
        assert "Some note text" in result
        assert "</Aside>" in result

    def test_warning_aside(self):
        md = "> warning This is dangerous"
        result = convert_blockquotes_to_asides(md)
        assert '<Aside type="caution">' in result
        assert "This is dangerous" in result

    def test_tip_aside(self):
        md = "> tip Use this approach"
        result = convert_blockquotes_to_asides(md)
        assert '<Aside type="tip">' in result

    def test_danger_aside(self):
        md = "> danger Do not do this"
        result = convert_blockquotes_to_asides(md)
        assert '<Aside type="danger">' in result

    def test_multiline_blockquote(self):
        md = "> First line\n> Second line"
        result = convert_blockquotes_to_asides(md)
        assert "First line" in result
        assert "Second line" in result

    def test_non_blockquote_preserved(self):
        md = "Normal text\n\nMore text"
        result = convert_blockquotes_to_asides(md)
        assert result == md

    def test_h2_breaks_aside(self):
        md = "> start\n## Header\ntext"
        result = convert_blockquotes_to_asides(md)
        assert "## Header" in result


# ── convert_h2_to_header_with_icon ──────────────────────────────────────────


class TestConvertH2ToHeaderWithIcon:
    def test_known_header_gets_icon(self):
        md = "## Overview"
        result = convert_h2_to_header_with_icon(md)
        assert "Icon" in result
        assert "star" in result

    def test_unknown_header_no_icon(self):
        md = "## Custom Section"
        result = convert_h2_to_header_with_icon(md)
        assert "Icon" not in result
        assert "## Custom Section" in result

    def test_all_known_headers(self):
        headers = [
            "overview",
            "background",
            "summary",
            "errors",
            "context",
            "constraints",
            "internals",
            "strategy",
            "notes",
            "changelog",
            "rationale",
            "api",
        ]
        for h in headers:
            result = convert_h2_to_header_with_icon(f"## {h.capitalize()}")
            assert "Icon" in result, f"Expected icon for header '{h}'"


# ── normalize_type_name ──────────────────────────────────────────────────────


class TestNormalizeTypeName:
    def test_strips_const(self):
        assert normalize_type_name("const int") == "int"

    def test_strips_pointer(self):
        assert normalize_type_name("int *") == "int"

    def test_strips_array(self):
        assert normalize_type_name("int[]") == "int"

    def test_collapses_whitespace(self):
        assert normalize_type_name("struct   foo") == "struct foo"

    def test_lowercases(self):
        assert normalize_type_name("MyType") == "mytype"


# ── link_functions_in_md ─────────────────────────────────────────────────────


class TestLinkFunctionsInMd:
    def test_links_known_function(self):
        functions = {"foo": "https://example.com/foo"}
        result = link_functions_in_md("Call `foo()` here", functions)
        assert "[`foo()`](https://example.com/foo)" in result

    def test_unknown_function_unchanged(self):
        result = link_functions_in_md("Call `bar()` here", {})
        assert result == "Call `bar()` here"


# ── link_files_in_md ─────────────────────────────────────────────────────────


class TestLinkFilesInMd:
    def test_links_known_file(self):
        files = {"sched.h": "https://example.com/sched.h"}
        result = link_files_in_md("See `sched.h` for details", files)
        assert "[`sched.h`](https://example.com/sched.h)" in result

    def test_unknown_file_unchanged(self):
        result = link_files_in_md("See `unknown.h` for details", {})
        assert result == "See `unknown.h` for details"


# ── link_commits_in_md ───────────────────────────────────────────────────────


class TestLinkCommitsInMd:
    def test_links_commit(self):
        result = link_commits_in_md("See commit abc1234")
        assert "[commit abc1234]" in result
        assert "github.com/axvonx/charmos/commit/abc1234" in result


# ── link_bugs_in_md ──────────────────────────────────────────────────────────


class TestLinkBugsInMd:
    def test_links_bug(self):
        result = link_bugs_in_md("Fixed #42")
        assert "[#42]" in result
        assert "/42" in result


# ── extract_mdx_title ────────────────────────────────────────────────────────


class TestExtractMdxTitle:
    def test_big_idea_inline(self):
        md = "# Big Idea: My Feature\n\nSome body text"
        title, body = extract_mdx_title(md)
        assert "Big Idea: My Feature" in title
        assert "Some body text" in body

    def test_big_idea_next_line(self):
        md = "# Big Idea\nMy Feature\n\nBody"
        title, body = extract_mdx_title(md)
        assert "Big Idea: My Feature" in title

    def test_credits_extracted(self):
        md = "# Small Idea: Widget\n## Credits\nJohn Doe\n\nBody text"
        title, body = extract_mdx_title(md)
        assert "John Doe" in title
        assert "Credits" not in body

    def test_fallback_untitled(self):
        md = "Just some regular markdown"
        title, body = extract_mdx_title(md)
        assert "Untitled" in title


# ── status_to_badge ──────────────────────────────────────────────────────────


class TestStatusToBadge:
    def test_stable(self):
        result = status_to_badge("STABLE")
        assert 'variant="success"' in result
        assert "Stable" in result

    def test_deprecated(self):
        result = status_to_badge("DEPRECATED")
        assert 'variant="danger"' in result

    def test_unknown_defaults_to_tip(self):
        result = status_to_badge("UNKNOWN")
        assert 'variant="tip"' in result


# ── merge_changelog_and_notes ────────────────────────────────────────────────


class TestMergeChangelogAndNotes:
    def test_merges_both_sections(self):
        md = "## Changelog\n- v1.0\n- v2.0\n\n## Notes\nSome notes here\n"
        result = merge_changelog_and_notes(md)
        assert "<Tabs>" in result
        assert "Changelog" in result
        assert "Notes" in result

    def test_no_merge_when_missing(self):
        md = "## Changelog\n- v1.0\n\n## Other\nStuff\n"
        result = merge_changelog_and_notes(md)
        assert "<Tabs>" not in result


# ── assemble_page_text ───────────────────────────────────────────────────────


class TestAssemblePageText:
    def test_frontmatter_and_imports(self):
        out = assemble_page_text("My Title", "gummi", "stable", None, "BODY")
        assert out.startswith('---\ntitle: "My Title"\n')
        assert 'author: "gummi"' in out
        assert "import { Badge } from '@astrojs/starlight/components';" in out
        assert out.rstrip().endswith("BODY")
        # No sidebar badge when badge is None.
        assert "sidebar:" not in out

    def test_sidebar_badge_included(self):
        out = assemble_page_text("T", "a", "s", ("Stable", "success"), "B")
        assert "sidebar:\n  badge:\n    text: Stable\n    variant: success" in out
        # Badge lives inside the frontmatter block (before the closing ---).
        head = out.split("\n\n", 1)[0]
        assert head.count("---") == 2 and "variant: success" in head

    def test_frontmatter_is_valid_single_block(self):
        out = assemble_page_text("T", "a", "s", None, "B")
        # Exactly one opening and one closing fence, imports after.
        assert out.split("\n\n")[0] == '---\ntitle: "T"\nauthor: "a"\nstatus: "s"\n---'


# ── generate_github_link_safe ────────────────────────────────────────────────


class TestGenerateGithubLink:
    def test_basic_link(self):
        url = generate_github_link_safe("charmos/include/sched.h", 42)
        assert "github.com/axvonx/charmos" in url
        assert "#L42" in url
        assert "/blob/main/" in url

    def test_no_line(self):
        url = generate_github_link_safe("charmos/include/sched.h")
        assert "#L" not in url


# ── format_enum_as_c_code ────────────────────────────────────────────────────


class TestFormatEnum:
    def test_basic_enum(self):
        data = {"file": "test.h"}
        e = {
            "name": "color",
            "members": [
                {"name": "RED", "value": "0"},
                {"name": "GREEN", "value": "1"},
            ],
        }
        result = format_enum_as_c_code(data, e, {})
        assert "```c" in result
        assert "enum color" in result
        assert "RED = 0" in result


# ── format_struct_as_c_code ──────────────────────────────────────────────────


class TestFormatStruct:
    def test_basic_struct(self):
        data = {"file": "test.h"}
        s = {
            "name": "point",
            "kind": "struct",
            "size": None,
            "line": 1,
            "members": [
                {"name": "x", "type": "int", "nested": None, "offset": None},
                {"name": "y", "type": "int", "nested": None, "offset": None},
            ],
        }
        result = format_struct_as_c_code(data, s, {})
        assert "```c" in result
        assert "struct point" in result
        assert "int" in result


# ── format_function_signature ────────────────────────────────────────────────


class TestFormatFunctionSignature:
    def test_basic_function(self):
        data = {"file": "test.h"}
        f = {
            "name": "add",
            "return_type": "int",
            "parameters": [
                {"type": "int", "name": "a"},
                {"type": "int", "name": "b"},
            ],
            "qualifiers": [],
            "line": 10,
        }
        result = format_function_signature(data, f, {})
        assert "```c" in result
        assert "int add(int a, int b);" in result


# ── build_type_table ─────────────────────────────────────────────────────────


class TestBuildTypeTable:
    def test_builds_from_structs(self):
        c_parse_map = {
            "test.h": {
                "types": {
                    "structs": [{"name": "foo", "kind": "struct", "line": 5}],
                    "enums": [],
                    "typedefs": [],
                },
                "functions": [],
            }
        }
        table = build_type_table(c_parse_map)
        assert "struct foo" in table
        assert table["struct foo"]["line"] == 5

    def test_builds_from_enums(self):
        c_parse_map = {
            "test.h": {
                "types": {
                    "structs": [],
                    "enums": [{"name": "bar", "line": 10}],
                    "typedefs": [],
                },
                "functions": [],
            }
        }
        table = build_type_table(c_parse_map)
        assert "enum bar" in table


# ── _dir_name_to_slug ────────────────────────────────────────────────────────


class TestDirNameToSlug:
    def test_basic_slug(self):
        assert _dir_name_to_slug("Scheduling and Multitasking") == "scheduling-and-multitasking"

    def test_special_chars(self):
        assert _dir_name_to_slug("I/O & Memory") == "i-o-memory"
