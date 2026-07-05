#!/usr/bin/env python3
"""Tests for make_json.py — C source parsing into JSON."""

import os

# Add parent directory to path so we can import the module
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from make_json import (
    clean_comment_markers,
    extract_audience,
    extract_bugs,
    extract_commits,
    extract_file_title,
    extract_idea_refs,
    extract_ideas_from_file,
    extract_metadata,
    node_raw_text,
    node_text,
    parse_c_types_and_functions,
    should_ignore_file,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


NBSP = " "


# ── idea markdown cleaning (stress tests) ────────────────────────────────────


class TestCleanCommentMarkersRendering:
    def _clean(self, lines):
        return clean_comment_markers("\n".join(lines))

    def test_code_fence_behind_comment_margin_survives(self):
        # The fence sits behind a "* " comment margin — it must still be a fence.
        out = self._clean(
            [
                " * ## API:",
                " *   ```c",
                " *   enum irql old = irql_raise(IRQL_DISPATCH_LEVEL);",
                " *   irql_lower(old);",
                " *   ```",
            ]
        )
        assert out.count("```") == 2
        # fence flush-left (valid markdown), code dedented, no NBSP
        assert "\n```c\n" in out
        assert "enum irql old = irql_raise(IRQL_DISPATCH_LEVEL);" in out
        assert NBSP not in out

    def test_wrapped_paragraph_has_no_nbsp_gaps(self):
        out = self._clean(
            [
                " *   IRQLs were introduced as the preemption/interrupt control",
                " *   of this kernel because of the structure and strict rules.",
            ]
        )
        assert NBSP not in out
        # both lines flush-left so markdown joins them into one clean paragraph
        assert out == (
            "IRQLs were introduced as the preemption/interrupt control\n"
            "of this kernel because of the structure and strict rules."
        )

    def test_blank_lines_inside_code_block_preserved(self):
        out = self._clean(
            [
                " *   ```c",
                " *   a();",
                " *",
                " *   b();",
                " *   ```",
            ]
        )
        assert "a();\n\nb();" in out

    def test_list_items_preserved(self):
        out = self._clean([" * ## Notes:", " * - first", " * - second"])
        assert "- first" in out and "- second" in out

    def test_deep_indentation_uses_nbsp_not_code_block(self):
        # 4+ leading spaces would become an indented code block in markdown;
        # keep it as visual indent via NBSP instead.
        out = self._clean([" *      deeply indented note"])
        assert out.startswith(NBSP)

    def test_hanging_indent_continuation_has_no_nbsp(self):
        # A wrapped prose line indented 4 spaces (doc-comment hanging indent)
        # continues the paragraph — it can't start an indented code block, so
        # its leading spaces must be stripped, not turned into NBSP gaps.
        out = self._clean(
            [
                " * IRQLs play a major role in the preemption and interrupt",
                " *     control mechanisms of this kernel. Thus this matters.",
            ]
        )
        assert NBSP not in out
        assert out == (
            "IRQLs play a major role in the preemption and interrupt\n"
            "control mechanisms of this kernel. Thus this matters."
        )

    def test_indented_block_after_blank_still_nbsp(self):
        # After a blank line, a 4-space indent *can* start a code block, so the
        # NBSP guard must still fire there.
        out = self._clean(
            [
                " * intro paragraph",
                " *",
                " *     indented block after a blank line",
            ]
        )
        assert NBSP in out


# ── idea validation (orphaned bodies) ────────────────────────────────────────


class TestIdeaValidation:
    def test_orphaned_idea_body_warns(self, capsys):
        # idea_bad.h has a "# Small Idea:" body but no @idea: signature.
        ideas = extract_ideas_from_file(str(FIXTURES / "idea_bad.h"))
        assert ideas == []  # silently dropped from ingestion …
        err = capsys.readouterr().err
        assert "has no matching '@idea:small" in err  # … but now loudly warned

    def test_well_formed_idea_does_not_warn(self, capsys):
        ideas = extract_ideas_from_file(str(FIXTURES / "idea_good.h"))
        assert len(ideas) == 1
        assert ideas[0]["name"] == "Widget Lifecycle"
        assert capsys.readouterr().err == ""

    def test_size_mismatch_warns(self, capsys):
        # signature @idea:big but body "# Small Idea" → still ingested, warned.
        ideas = extract_ideas_from_file(str(FIXTURES / "idea_mismatch.h"))
        assert len(ideas) == 1
        err = capsys.readouterr().err
        assert "size mismatch" in err

    def test_section_typo_warns_custom_section_does_not(self, capsys):
        extract_ideas_from_file(str(FIXTURES / "idea_mismatch.h"))
        err = capsys.readouterr().err
        # "Overveiw" is close to "Overview" → typo warning
        assert "Overveiw" in err and "Overview" in err
        # "Bootstage Exception" is genuinely custom → no typo warning for it
        assert "Bootstage Exception" not in err

    def test_name_mismatch_warns(self, capsys):
        ideas = extract_ideas_from_file(str(FIXTURES / "idea_namemismatch.h"))
        assert len(ideas) == 1
        err = capsys.readouterr().err
        assert "name mismatch" in err

    def test_cosmetic_name_difference_does_not_warn(self, capsys):
        # "Real-time" vs "Realtime" differ only in punctuation → no false warning.
        from make_json import _normalize_idea_name

        assert _normalize_idea_name("Real-time scheduler") == _normalize_idea_name(
            "Realtime scheduler"
        )

    def test_node_raw_text_preserves_newlines(self):
        code = b"a\n  b"

        class N:
            start_byte, end_byte = 0, len(code)

        assert node_raw_text(N(), code) == "a\n  b"
        assert node_text(N(), code) == "a b"  # the collapsing variant


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


class TestEnumUnderlyingType:
    """C23 ``enum E : underlying_type { … }``. tree-sitter's C grammar doesn't
    understand the ``: type`` clause and mis-recovers the whole enum as a
    function definition, dropping every enumerator — so the parser neutralizes
    the clause before parsing and records the underlying type separately."""

    def test_underlying_type_enum_members_recovered(self):
        code = """\
enum demand_page_flags : page_flags_t {
    DEMAND_PAGE_FLAG_NONE = 1,
    DEMAND_PAGE_FLAG_ZERO_MEMORY = 2,
    DEMAND_PAGE_FLAG_WRITABLE = 1 << 3,
};
"""
        result = parse_code(code)
        enums = result["types"]["enums"]
        assert len(enums) == 1
        assert enums[0]["name"] == "demand_page_flags"
        assert enums[0]["underlying_type"] == "page_flags_t"
        names = [m["name"] for m in enums[0]["members"]]
        assert names == [
            "DEMAND_PAGE_FLAG_NONE",
            "DEMAND_PAGE_FLAG_ZERO_MEMORY",
            "DEMAND_PAGE_FLAG_WRITABLE",
        ]
        # The enum must NOT leak out as a phantom function.
        assert not any(f["name"] == "page_flags_t" for f in result["functions"])

    def test_underlying_type_values_preserved(self):
        code = "enum e : unsigned int { A = 1, B = 1 << 4 };"
        result = parse_code(code)
        members = result["types"]["enums"][0]["members"]
        assert result["types"]["enums"][0]["underlying_type"] == "unsigned int"
        assert members[0]["value"] == "1"
        assert members[1]["value"] == "1 << 4"

    def test_plain_enum_has_null_underlying(self):
        code = "enum color { RED, GREEN };"
        result = parse_code(code)
        assert result["types"]["enums"][0]["underlying_type"] is None

    def test_typedef_enum_with_underlying_type(self):
        code = "typedef enum status : uint8_t { OK, ERR } status_t;"
        result = parse_code(code)
        enums = result["types"]["enums"]
        assert len(enums) == 1
        assert enums[0]["name"] == "status"
        assert enums[0]["underlying_type"] == "uint8_t"
        assert [m["name"] for m in enums[0]["members"]] == ["OK", "ERR"]
        assert any(t["name"] == "status_t" for t in result["types"]["typedefs"])

    def test_underlying_type_preserves_line_numbers(self):
        # The neutralization must not shift byte offsets / line numbers.
        code = "\n\nenum e : int {\n    A = 1,\n};\n"
        result = parse_code(code)
        assert result["types"]["enums"][0]["line"] == 3
        assert result["types"]["enums"][0]["members"][0]["line"] == 4

    def test_ternary_in_enum_value_not_mistaken_for_underlying(self):
        # A ``:`` inside an enumerator value must not be treated as an underlying
        # type clause (there is no ``: type`` between the name and ``{``).
        code = "enum e { A = 1 ? 2 : 3, B };"
        result = parse_code(code)
        enums = result["types"]["enums"]
        assert enums[0]["underlying_type"] is None
        assert [m["name"] for m in enums[0]["members"]] == ["A", "B"]


# ── Struct member qualifiers / bitfields ─────────────────────────────────────


class TestStructMemberBitfields:
    def test_bitfield_width_captured(self):
        code = "struct s { unsigned int a : 3; unsigned int b : 5; };"
        result = parse_code(code)
        members = result["types"]["structs"][0]["members"]
        assert members[0]["name"] == "a"
        assert members[0]["type"] == "unsigned int"
        assert members[0]["bitfield"] == "3"
        assert members[1]["bitfield"] == "5"

    def test_non_bitfield_member_has_null_bitfield(self):
        code = "struct s { int x; };"
        result = parse_code(code)
        assert result["types"]["structs"][0]["members"][0]["bitfield"] is None

    def test_anonymous_bitfield_padding(self):
        code = "struct s { unsigned int a : 1; unsigned int : 7; };"
        result = parse_code(code)
        members = result["types"]["structs"][0]["members"]
        # Both the named field and the anonymous padding carry their widths.
        widths = [m["bitfield"] for m in members]
        assert "1" in widths
        assert "7" in widths


class TestStructMemberQualifiers:
    """Leading type qualifiers/specifiers sit as sibling nodes of the bare
    ``type`` field; grabbing only that field silently drops them."""

    def test_const_pointer_member_keeps_const(self):
        code = "struct s { const char *name; };"
        result = parse_code(code)
        m = result["types"]["structs"][0]["members"][0]
        assert m["type"] == "const char"

    def test_const_volatile_restrict_member(self):
        code = "struct s { const volatile int * restrict p; };"
        result = parse_code(code)
        m = result["types"]["structs"][0]["members"][0]
        assert m["type"] == "const volatile int"

    def test_atomic_qualifier_member(self):
        code = "struct s { int _Atomic counter; };"
        result = parse_code(code)
        m = result["types"]["structs"][0]["members"][0]
        assert "_Atomic" in m["type"]
        assert m["name"] == "counter"

    def test_plain_member_type_unchanged(self):
        code = "struct s { unsigned long len; };"
        result = parse_code(code)
        m = result["types"]["structs"][0]["members"][0]
        assert m["type"] == "unsigned long"
        assert m["name"] == "len"


class TestQualifiersElsewhere:
    """Qualifier-awareness also applies to globals, typedefs, and parameters."""

    def test_typedef_keeps_const(self):
        code = "typedef const int ci_t;"
        result = parse_code(code)
        t = result["types"]["typedefs"][0]
        assert t["name"] == "ci_t"
        assert t["type"] == "const int"

    def test_global_keeps_qualifiers(self):
        code = "extern const int * restrict gp;"
        result = parse_code(code)
        g = result["types"]["globals"][0]
        assert g["name"] == "gp"
        assert "const int" in g["type"]

    def test_prototype_param_keeps_const(self):
        code = "void f(const int *p, volatile unsigned long x);"
        result = parse_code(code)
        params = result["functions"][0]["parameters"]
        assert params[0]["type"] == "const int"
        assert params[1]["type"] == "volatile unsigned long"

    def test_prototype_return_type_keeps_const(self):
        code = "const char *version(void);"
        result = parse_code(code)
        assert result["functions"][0]["return_type"] == "const char *"

    def test_fn_ptr_typedef_param_keeps_const(self):
        code = "typedef int (*cmp)(const void *a, const void *b);"
        result = parse_code(code)
        fn = result["types"]["typedefs"][0]["fn_ptr"]
        assert fn is not None
        assert fn["parameters"][0]["type"] == "const void *"
        assert fn["parameters"][1]["type"] == "const void *"


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
        with tempfile.NamedTemporaryFile(mode="w", suffix=".h", delete=False) as f:
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
        with tempfile.NamedTemporaryFile(mode="w", suffix=".h", delete=False) as f:
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


class TestGlobalVariables:
    def test_extern_and_file_scope_variables(self):
        code = """\
extern int global_counter;
extern struct spinlock big_lock;
static const char *g_name;
int uninit_global;
extern unsigned long flags[4];
void do_thing(int x);
struct foo { int a; };
"""
        result = parse_code(code)
        globs = {g["name"]: g for g in result["types"]["globals"]}
        # all five variables captured …
        assert set(globs) == {
            "global_counter",
            "big_lock",
            "g_name",
            "uninit_global",
            "flags",
        }
        # … with storage class + type preserved (type feeds reference links)
        assert globs["global_counter"]["storage"] == "extern"
        assert globs["big_lock"]["type"] == "struct spinlock"
        assert globs["uninit_global"]["storage"] == ""
        # prototypes and struct defs are NOT misclassified as variables
        assert [f["name"] for f in result["functions"]] == ["do_thing"]
        assert [s["name"] for s in result["types"]["structs"]] == ["foo"]

    def test_no_false_globals_from_typedefs(self):
        # A typedef is not a variable declaration.
        result = parse_code("typedef int myint;\nextern myint counter;")
        names = {g["name"] for g in result["types"]["globals"]}
        assert names == {"counter"}

    def test_macro_decorated_declarations_are_rejected(self):
        # Macro-annotated / macro-call declarations tree-sitter mis-parses must
        # not leak in as bogus "variables".
        code = """\
extern int real_global;
LIMINE_DEPRECATED some_deprecated;
struct LIMINE_MP(mp_info);
LIMINE_IGNORE_START struct foo { int x; };
"""
        result = parse_code(code)
        names = {g["name"] for g in result["types"]["globals"]}
        assert names == {"real_global"}

    def test_macro_body_declarations_are_not_globals(self):
        # tree-sitter only parses the first line or two of a complex
        # function-like macro body, then leaks the remaining statements out as
        # file-scope declarations. Those (e.g. the `auto __x = y;` inside
        # hashmap_insert) must not be recorded as globals.
        code = """\
extern int real_global;

#define hashmap_insert(map, key, key_length)                                   \\
    ({                                                                         \\
        auto __key = key;                                                      \\
        auto __key_len = key_length;                                           \\
        auto __map = map;                                                      \\
        uint64_t __hash = hash(__key, __key_len);                             \\
        uint64_t __index = __hash % __map->capacity;                          \\
    })

extern int another_global;
"""
        result = parse_code(code)
        names = {g["name"] for g in result["types"]["globals"]}
        assert names == {"real_global", "another_global"}
        for phantom in ("__key", "__key_len", "__map", "__hash", "__index"):
            assert phantom not in names

    def test_struct_with_trailing_attribute_macro_not_a_global(self):
        # `struct X { … } __packed;` — the trailing macro is an attribute, not a
        # variable; the struct itself is still recorded.
        code = """\
extern char __skernel[];
struct packed_thing { int a; } __packed;
extern struct info hpet_base;
"""
        result = parse_code(code)
        names = {g["name"] for g in result["types"]["globals"]}
        assert names == {"__skernel", "hpet_base"}
        assert "__packed" not in names
        assert [s["name"] for s in result["types"]["structs"]] == ["packed_thing"]
