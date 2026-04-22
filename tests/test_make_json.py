#!/usr/bin/env python3
"""Tests for make_json.py — C source parsing into JSON."""

import tempfile
import os
import pytest
from pathlib import Path

# Add parent directory to path so we can import the module
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from make_json import (
    parse_c_types_and_functions,
    extract_file_title,
    extract_metadata,
    extract_ideas_from_file,
    extract_commits,
    extract_idea_refs,
    extract_audience,
    extract_bugs,
    clean_comment_markers,
    should_ignore_file,
    extract_typedef_name,
    is_function_prototype,
    node_text,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def parse_code(code: str) -> dict:
    """Write code to a temp file, parse it, and return the result."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".h", delete=False) as f:
        f.write(code)
        tmp = f.name
    try:
        return parse_c_types_and_functions(tmp)
    finally:
        os.unlink(tmp)


# ── Function parsing ─────────────────────────────────────────────────────────

class TestFunctionDefinitions:
    def test_simple_function_definition(self):
        result = parse_code("int add(int a, int b) { return a + b; }")
        funcs = result["functions"]
        assert len(funcs) == 1
        assert funcs[0]["name"] == "add"
        assert funcs[0]["return_type"] == "int"
        assert len(funcs[0]["parameters"]) == 2
        assert funcs[0]["parameters"][0] == {"type": "int", "name": "a"}
        assert funcs[0]["parameters"][1] == {"type": "int", "name": "b"}

    def test_void_function_no_params(self):
        result = parse_code("void do_nothing(void) { }")
        funcs = result["functions"]
        assert len(funcs) == 1
        assert funcs[0]["name"] == "do_nothing"
        assert funcs[0]["return_type"] == "void"

    def test_pointer_return_type(self):
        result = parse_code("char *get_name(int id) { return 0; }")
        funcs = result["functions"]
        assert len(funcs) == 1
        assert funcs[0]["name"] == "get_name"
        assert "char" in funcs[0]["return_type"]


class TestFunctionPrototypes:
    """Tests for function prototypes (declarations without bodies)."""

    def test_simple_prototype(self):
        result = parse_code("int add(int a, int b);")
        funcs = result["functions"]
        assert len(funcs) == 1
        assert funcs[0]["name"] == "add"
        assert funcs[0]["return_type"] == "int"
        assert len(funcs[0]["parameters"]) == 2

    def test_prototype_with_custom_types(self):
        """The exact bug scenario from the issue."""
        code = """\
uacpi_iteration_decision acpi_print_ctx(void *ctx, uacpi_namespace_node *node,
                                        uacpi_u32 node_depth);
"""
        result = parse_code(code)
        funcs = result["functions"]
        assert len(funcs) == 1
        assert funcs[0]["name"] == "acpi_print_ctx"
        assert funcs[0]["return_type"] == "uacpi_iteration_decision"
        assert len(funcs[0]["parameters"]) == 3

    def test_prototype_with_pragma_and_include(self):
        """Full header file context as reported in the bug."""
        code = """\
/* @title: Print Utilities */
#include <uacpi/namespace.h>
#pragma once
uacpi_iteration_decision acpi_print_ctx(void *ctx, uacpi_namespace_node *node,
                                        uacpi_u32 node_depth);
"""
        result = parse_code(code)
        funcs = result["functions"]
        assert len(funcs) == 1
        assert funcs[0]["name"] == "acpi_print_ctx"

    def test_multiple_prototypes(self):
        code = """\
void init(void);
int get_count(void);
char *get_name(int id);
"""
        result = parse_code(code)
        funcs = result["functions"]
        assert len(funcs) == 3
        names = [f["name"] for f in funcs]
        assert "init" in names
        assert "get_count" in names
        assert "get_name" in names

    def test_prototype_pointer_return(self):
        code = "void *kmalloc(size_t size);"
        result = parse_code(code)
        funcs = result["functions"]
        assert len(funcs) == 1
        assert funcs[0]["name"] == "kmalloc"

    def test_prototype_no_duplicate_with_definition(self):
        """If both a prototype and definition exist, both should be captured."""
        code = """\
int foo(int x);
int foo(int x) { return x; }
"""
        result = parse_code(code)
        funcs = result["functions"]
        # Both the prototype and definition are captured
        assert len(funcs) == 2
        assert all(f["name"] == "foo" for f in funcs)


# ── Struct parsing ───────────────────────────────────────────────────────────

class TestStructParsing:
    def test_simple_struct(self):
        code = "struct point { int x; int y; };"
        result = parse_code(code)
        structs = result["types"]["structs"]
        assert len(structs) == 1
        assert structs[0]["name"] == "point"
        assert structs[0]["kind"] == "struct"
        assert len(structs[0]["members"]) == 2

    def test_union(self):
        code = "union data { int i; float f; };"
        result = parse_code(code)
        structs = result["types"]["structs"]
        assert len(structs) == 1
        assert structs[0]["kind"] == "union"

    def test_nested_anonymous_struct(self):
        code = """\
struct outer {
    struct {
        int a;
        int b;
    } inner;
};
"""
        result = parse_code(code)
        structs = result["types"]["structs"]
        assert len(structs) == 1
        assert structs[0]["name"] == "outer"
        members = structs[0]["members"]
        assert len(members) == 1
        assert members[0]["nested"] is not None

    def test_bare_struct_reference_not_recorded(self):
        """A struct used as a type without a body should not be recorded."""
        code = "void foo(struct bar *b) { }"
        result = parse_code(code)
        structs = result["types"]["structs"]
        assert len(structs) == 0


# ── Enum parsing ─────────────────────────────────────────────────────────────

class TestEnumParsing:
    def test_simple_enum(self):
        code = "enum color { RED, GREEN, BLUE };"
        result = parse_code(code)
        enums = result["types"]["enums"]
        assert len(enums) == 1
        assert enums[0]["name"] == "color"
        members = enums[0]["members"]
        names = [m["name"] for m in members]
        assert "RED" in names
        assert "GREEN" in names
        assert "BLUE" in names

    def test_enum_with_values(self):
        code = "enum flags { A = 1, B = 2, C = 4 };"
        result = parse_code(code)
        enums = result["types"]["enums"]
        members = enums[0]["members"]
        assert members[0]["value"] == "1"
        assert members[1]["value"] == "2"

    def test_bare_enum_reference_not_recorded(self):
        code = "enum color get_color(void) { }"
        result = parse_code(code)
        enums = result["types"]["enums"]
        assert len(enums) == 0


# ── Typedef parsing ──────────────────────────────────────────────────────────

class TestTypedefParsing:
    def test_simple_typedef(self):
        code = "typedef unsigned int uint32_t;"
        result = parse_code(code)
        typedefs = result["types"]["typedefs"]
        assert len(typedefs) == 1
        assert typedefs[0]["name"] == "uint32_t"

    def test_typedef_struct(self):
        code = "typedef struct { int x; int y; } point_t;"
        result = parse_code(code)
        typedefs = result["types"]["typedefs"]
        assert any(t["name"] == "point_t" for t in typedefs)
        # The anonymous struct should also be recorded
        structs = result["types"]["structs"]
        assert len(structs) == 1

    def test_typedef_function_pointer(self):
        code = "typedef void (*callback_fn)(int status);"
        result = parse_code(code)
        typedefs = result["types"]["typedefs"]
        assert len(typedefs) == 1
        assert typedefs[0]["name"] == "callback_fn"
        assert typedefs[0]["fn_ptr"] is not None
        assert typedefs[0]["fn_ptr"]["return_type"] == "void"

    def test_typedef_enum_also_recorded(self):
        code = "typedef enum { A, B, C } my_enum_t;"
        result = parse_code(code)
        typedefs = result["types"]["typedefs"]
        enums = result["types"]["enums"]
        assert any(t["name"] == "my_enum_t" for t in typedefs)
        assert len(enums) == 1


# ── #define parsing ──────────────────────────────────────────────────────────

class TestDefines:
    def test_simple_define(self):
        code = "#define MAX_SIZE 1024"
        result = parse_code(code)
        defines = result["defines"]
        assert len(defines) == 1
        assert defines[0]["name"] == "MAX_SIZE"
        assert defines[0]["value"] == "1024"
        assert defines[0]["params"] is None

    def test_function_like_define(self):
        code = "#define ADD(a, b) ((a) + (b))"
        result = parse_code(code)
        defines = result["defines"]
        assert len(defines) == 1
        assert defines[0]["name"] == "ADD"
        assert defines[0]["params"] is not None

    def test_ignored_keyword_not_captured(self):
        """Keywords in IGNORED_KEYWORDS should not be captured as defines."""
        code = "#define if something"
        result = parse_code(code)
        defines = result["defines"]
        assert len(defines) == 0


# ── extract_file_title ───────────────────────────────────────────────────────

class TestExtractFileTitle:
    def test_basic_title(self):
        assert extract_file_title("/* @title: My File */") == "My File"

    def test_title_with_star_prefix(self):
        assert extract_file_title("/* @title: * My File */") == "My File"

    def test_no_title(self):
        assert extract_file_title("int x = 5;") is None

    def test_case_insensitive(self):
        assert extract_file_title("/* @Title: Foo */") == "Foo"


# ── extract_metadata ────────────────────────────────────────────────────────

class TestExtractMetadata:
    def test_idea_with_status(self):
        md = "# Big Idea\nMy Thing (STABLE)\n"
        meta = extract_metadata(md)
        assert meta["name"] == "My Thing"
        assert meta["status"] == "STABLE"

    def test_idea_without_status(self):
        md = "# Small Idea\nWidget\n"
        meta = extract_metadata(md)
        assert meta["name"] == "Widget"
        assert meta["status"] is None

    def test_credits(self):
        md = "## Credits\nJohn Doe\n"
        meta = extract_metadata(md)
        assert meta["author"] == "John Doe"


# ── extract_audience ─────────────────────────────────────────────────────────

class TestExtractAudience:
    def test_audience_header(self):
        md = "# Audience\nKernel developers\nSome content"
        cleaned, audience = extract_audience(md)
        assert audience == "Kernel developers"
        assert "Kernel developers" not in cleaned

    def test_no_audience(self):
        md = "Just some text"
        cleaned, audience = extract_audience(md)
        assert audience is None
        assert cleaned == md


# ── extract_commits ──────────────────────────────────────────────────────────

class TestExtractCommits:
    def test_finds_commits(self):
        md = "commit abc1234\ncommit 1234567890abcdef"
        commits = extract_commits(md)
        assert len(commits) == 2
        assert commits[0]["hash"] == "abc1234"

    def test_no_commits(self):
        assert extract_commits("no commits here") == []


# ── extract_idea_refs ────────────────────────────────────────────────────────

class TestExtractIdeaRefs:
    def test_finds_refs(self):
        text = ']: "some idea ref"'
        refs = extract_idea_refs(text)
        assert len(refs) == 1
        assert refs[0]["string"] == "some idea ref"


# ── extract_bugs ─────────────────────────────────────────────────────────────

class TestExtractBugs:
    def test_finds_bugs_in_section(self):
        md = "## Bugs\nFixed #42 and #100\n## Other"
        bugs = extract_bugs(md)
        assert len(bugs) == 2
        numbers = [b["number"] for b in bugs]
        assert 42 in numbers
        assert 100 in numbers

    def test_no_bugs_outside_section(self):
        md = "## Summary\nSee #42\n"
        bugs = extract_bugs(md)
        assert len(bugs) == 0


# ── clean_comment_markers ────────────────────────────────────────────────────

class TestCleanCommentMarkers:
    def test_strips_c_comment_markers(self):
        raw = "* This is a comment\n * with markers\n */"
        cleaned = clean_comment_markers(raw)
        assert "This is a comment" in cleaned
        assert "*/" not in cleaned

    def test_preserves_code_blocks(self):
        raw = "* text\n```c\nint x = 5;\n```\n* more text"
        cleaned = clean_comment_markers(raw)
        assert "int x = 5;" in cleaned

    def test_strips_double_slash(self):
        raw = "// A comment\n// Another"
        cleaned = clean_comment_markers(raw)
        assert cleaned.strip().startswith("A comment")


# ── should_ignore_file ───────────────────────────────────────────────────────

class TestShouldIgnoreFile:
    def test_ignores_uacpi(self):
        assert should_ignore_file(Path("include/uACPI/test.h")) is True

    def test_ignores_flanterm(self):
        assert should_ignore_file(Path("src/flanterm/term.c")) is True

    def test_allows_normal_path(self):
        assert should_ignore_file(Path("include/kernel/sched.h")) is False


# ── extract_ideas_from_file ──────────────────────────────────────────────────

class TestExtractIdeas:
    def test_extracts_idea(self):
        code = """\
/* @idea:big My Idea */
/* # Big Idea
 * My Idea
 *
 * ## Overview
 * Some description here.
 */
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".h", delete=False
        ) as f:
            f.write(code)
            tmp = f.name
        try:
            ideas = extract_ideas_from_file(tmp)
            assert len(ideas) == 1
            assert ideas[0]["name"] == "My Idea"
            assert ideas[0]["size"] == "big"
        finally:
            os.unlink(tmp)

    def test_no_ideas(self):
        code = "int x = 5;\n"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".h", delete=False
        ) as f:
            f.write(code)
            tmp = f.name
        try:
            ideas = extract_ideas_from_file(tmp)
            assert len(ideas) == 0
        finally:
            os.unlink(tmp)


# ── Mixed content: prototypes + structs + ideas ──────────────────────────────

class TestMixedContent:
    def test_header_with_everything(self):
        """A realistic header with prototypes, structs, enums, typedefs, defines."""
        code = """\
/* @title: Test Header */
#pragma once

#define VERSION 1

typedef unsigned long size_t;

enum status { OK = 0, ERR = 1 };

struct config {
    int flags;
    size_t size;
};

typedef void (*handler_fn)(int code);

void init(struct config *cfg);
int process(void *data, size_t len);
struct config *get_default(void);
"""
        result = parse_code(code)
        assert len(result["functions"]) == 3
        func_names = [f["name"] for f in result["functions"]]
        assert "init" in func_names
        assert "process" in func_names
        assert "get_default" in func_names

        assert len(result["types"]["structs"]) == 1
        assert len(result["types"]["enums"]) == 1
        assert len(result["types"]["typedefs"]) == 2
        assert len(result["defines"]) == 1
