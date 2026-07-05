#!/usr/bin/env python3
"""Parse a single C header/source file into the pipeline's JSON representation.

Uses tree-sitter to extract structs, enums, typedefs, functions, defines, and
the embedded ``@idea:`` doc-comments (with their metadata + cross-references).
Invoked per-file by ``generate.py`` — ``make_json.py <source> <out.json>`` —
so the parse can be fanned out across processes.
"""

import difflib
import json
import re
import shutil
import sys
from pathlib import Path

from tree_sitter_language_pack import get_parser

FILE_TITLE_RE = re.compile(r"/\*\s*@title:\s*(.+?)\s*\*/", re.IGNORECASE | re.DOTALL)

IDEA_REF_RE = re.compile(r'\]:\s*"([^"]+)"')
IDEA_SIGNATURE_RE = re.compile(r"/\*\s*@idea:(small|big|huge)\s+(.+?)\s*\*/", re.UNICODE)
FILE_RE = re.compile(r"([./\w\-]+?\.(c|h|rs|cpp|txt|md))", re.UNICODE)
FUNC_REF_RE = re.compile(r"`([a-zA-Z_][a-zA-Z0-9_]*)\(\)`", re.UNICODE)
IGNORED_KEYWORDS = {"if", "for", "while", "switch", "return", "sizeof"}
COMMIT_RE = re.compile(r"(?:\*?\s*)commit\s+([0-9a-f]{7,40})", re.IGNORECASE)

IGNORE_DIRS = ["uACPI", "flanterm"]

parser = get_parser("c")


def extract_file_title(text: str):
    m = FILE_TITLE_RE.search(text)
    if not m:
        return None

    title = m.group(1).strip()
    title = re.sub(r"^\*\s*", "", title).strip()
    return title


def print_single_line(*args, **kwargs):
    text = " ".join(str(arg) for arg in args)
    terminal_width = shutil.get_terminal_size((80, 20)).columns
    spaces_to_clear = max(terminal_width - len(text), 0)
    output = "\r" + text + " " * spaces_to_clear

    flush = kwargs.get("flush", True)

    sys.stdout.write(output)
    if flush:
        sys.stdout.flush()


def extract_metadata(md_text: str):
    name = None
    status = None
    author = None

    lines = md_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()

        if re.match(r"^[#/*\s]*(Big|Huge|Small)\s+Idea", line, re.IGNORECASE):
            if i + 1 < len(lines):
                next_line = re.sub(r"^[#/*\s]*", "", lines[i + 1]).strip()
                m = re.match(r"(.+?)(?:\s*\((.+?)\))?$", next_line)
                if m:
                    name = m.group(1).strip()
                    status = m.group(2).strip() if m.group(2) else None
                    i += 1

        elif re.match(r"^[##/*\s]*Credits", line, re.IGNORECASE):
            if i + 1 < len(lines):
                next_line = re.sub(r"^[##/*\s]*", "", lines[i + 1]).strip()
                author = next_line
                i += 1

        i += 1

    return {"name": name, "status": status, "author": author}


def should_ignore_file(path: Path):
    for d in IGNORE_DIRS:
        if d in path.parts:
            return True
    return False


def get_full_return_type(container_node, type_node, declarator_node, code_bytes):
    # Prefer the qualifier-aware walk over the whole declaration; fall back to
    # the bare ``type`` field if the container yields nothing (e.g. odd parses).
    type_str = _leading_type_text(container_node, code_bytes) if container_node else ""
    if not type_str:
        type_str = (
            code_bytes[type_node.start_byte : type_node.end_byte].decode("utf-8").strip()
            if type_node
            else ""
        )

    return _append_pointer_stars(type_str, declarator_node)


def get_typedef_type(container_node, type_node, declarator_node, code_bytes):
    # ``typedef const int foo`` keeps the ``const`` here — the bare ``type`` node
    # would only be ``int``. Stop at the typedef name (a ``type_identifier``)
    # so it isn't swept into the type text.
    type_str = (
        _leading_type_text(container_node, code_bytes, declarator_node) if container_node else ""
    )
    if not type_str:
        type_str = (
            code_bytes[type_node.start_byte : type_node.end_byte].decode("utf-8").strip()
            if type_node
            else ""
        )

    node = declarator_node
    while node:
        if node.type == "pointer_declarator":
            type_str += " *"
            node = node.child_by_field_name("declarator")
        elif node.type == "function_declarator":
            params_node = node.child_by_field_name("parameters")
            params = []
            if params_node:
                for p in params_node.children:
                    if p.type == "parameter_declaration":
                        p_type_node = p.child_by_field_name("type")
                        p_decl_node = p.child_by_field_name("declarator")
                        p_type = _leading_type_text(p, code_bytes) or (
                            code_bytes[p_type_node.start_byte : p_type_node.end_byte]
                            .decode("utf-8")
                            .strip()
                            if p_type_node
                            else ""
                        )
                        p_name = (
                            code_bytes[p_decl_node.start_byte : p_decl_node.end_byte]
                            .decode("utf-8")
                            .strip()
                            if p_decl_node
                            else ""
                        )
                        params.append(f"{p_type} {p_name}".strip())
            type_str += f" ({', '.join(params)})"
            node = node.child_by_field_name("declarator")
        else:
            break

    return type_str


_C_TYPE_KEYWORDS = {
    "struct",
    "union",
    "enum",
    "void",
    "const",
    "volatile",
    "unsigned",
    "signed",
    "static",
    "extern",
    "register",
    "auto",
    "typedef",
    "inline",
    "char",
    "short",
    "int",
    "long",
    "float",
    "double",
}


def _macro_continuation_lines(code_bytes) -> set:
    """Return the set of 1-based line numbers that lie inside a multi-line
    preprocessor directive — a ``#define`` (or any ``#``-directive) and every
    line joined to it by a trailing backslash.

    tree-sitter only parses the first line or two of a complex function-like
    macro body (e.g. a ``({ ... })`` statement expression); its error recovery
    then surfaces the remaining body statements as if they were file-scope
    declarations. Those phantom globals all live on continuation lines, so we
    reject any global whose declaration starts on one.
    """
    text = code_bytes.decode("utf-8", errors="replace")
    lines = set()
    in_directive = False
    for i, line in enumerate(text.split("\n"), start=1):
        starts = line.lstrip().startswith("#")
        if starts or in_directive:
            lines.add(i)
        cont = line.rstrip("\r").endswith("\\")
        if starts:
            in_directive = cont
        elif in_directive:
            in_directive = cont
    return lines


def _looks_like_macro_noise(name, var_type, raw):
    """Reject 'global variables' that are really macro artifacts tree-sitter
    mis-parses (e.g. ``LIMINE_DEPRECATED foo;`` or ``struct LIMINE_MP(info);``).

    Real extern/global declarations have a lowercase/keyword type and a plain
    identifier name; the misparses leave a bare keyword as the name, an
    ALL-CAPS macro as the type, or a ``MACRO(...)`` call in the text.
    """
    if not name or name in _C_TYPE_KEYWORDS:
        return True
    if not re.match(r"^[A-Za-z_]\w*$", name):
        return True
    # A macro invocation like `struct LIMINE_MP(info);`
    if re.search(r"\b[A-Z][A-Z0-9_]{2,}\s*\(", raw or ""):
        return True
    # The "type" is nothing but an ALL-CAPS macro token (e.g. LIMINE_DEPRECATED)
    base = re.sub(r"\b(struct|union|enum|const|volatile|unsigned|signed)\b", "", var_type or "")
    base = base.replace("*", "").strip()
    if base and re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", base):
        return True
    return False


def _declarator_name(declarator_node, code_bytes):
    """Walk a variable declarator (pointer/array/init wrappers) to its bare
    identifier name. Returns None if no identifier is found."""
    node = declarator_node
    while node is not None:
        if node.type == "identifier":
            return node_text(node, code_bytes)
        if node.type in (
            "pointer_declarator",
            "array_declarator",
            "init_declarator",
        ):
            node = node.child_by_field_name("declarator")
            continue
        # Unknown wrapper — fall back to a regex over its text.
        raw = node_text(node, code_bytes) or ""
        m = re.search(r"([A-Za-z_][A-Za-z0-9_]*)", raw)
        return m.group(1) if m else None
    return None


def extract_typedef_name(declarator_node, code_bytes):
    node = declarator_node
    while node:
        if node.type in (
            "pointer_declarator",
            "function_declarator",
            "abstract_pointer_declarator",
        ):
            node = node.child_by_field_name("declarator")
        else:
            break
    if node:
        raw = code_bytes[node.start_byte : node.end_byte].decode("utf-8").strip()
        # tree-sitter sometimes gives us the full (*name) or (*name)(params)
        # wrapper text when it can't resolve the inner identifier as a separate
        # node.  Strip the pointer-declarator syntax to get the bare name.
        m = re.match(r"^\(\*\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)", raw)
        if m:
            return m.group(1)
        return raw
    return None


def extract_function_name_and_params(declarator_node, code_bytes):
    node = declarator_node
    params_node = None
    while True:
        if node.type == "pointer_declarator":
            node = node.child_by_field_name("declarator")
        elif node.type == "function_declarator":
            params_node = node.child_by_field_name("parameters")
            node = node.child_by_field_name("declarator")
        else:
            break

    func_name = (
        code_bytes[node.start_byte : node.end_byte].decode("utf-8").strip() if node else None
    )

    parameters = []
    if params_node:
        for p in params_node.children:
            if p.type == "parameter_declaration":
                p_type_node = p.child_by_field_name("type")
                p_decl_node = p.child_by_field_name("declarator")
                # Qualifier-aware type; fall back to the bare ``type`` field.
                p_type = _leading_type_text(p, code_bytes) or (
                    code_bytes[p_type_node.start_byte : p_type_node.end_byte]
                    .decode("utf-8")
                    .strip()
                    if p_type_node
                    else None
                )
                p_name = (
                    code_bytes[p_decl_node.start_byte : p_decl_node.end_byte]
                    .decode("utf-8")
                    .strip()
                    if p_decl_node
                    else None
                )
                parameters.append({"type": p_type, "name": p_name})

    return func_name, parameters


def is_function_prototype(declarator):
    node = declarator
    while node:
        if node.type == "function_declarator":
            return True
        next_node = None
        for field in ("declarator", "inner", "child"):
            child = node.child_by_field_name(field)
            if child:
                next_node = child
                break
        if not next_node:
            return False
        node = next_node
    return False


def node_text(node, code):
    if not node:
        return None
    text = code[node.start_byte : node.end_byte].decode("utf-8").strip()
    # Collapse newline + any following whitespace into a single space so that
    # multi-line declarators (e.g. function-pointer members split across lines)
    # don't carry raw indentation into the JSON. Runs of spaces are likewise
    # collapsed so that blanked-out regions (e.g. a neutralized enum
    # underlying-type clause) don't leave visible gaps in a type string.
    text = re.sub(r"\n\s*", " ", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text


def node_raw_text(node, code):
    """Like node_text but preserves line breaks (for multi-line macro bodies)."""
    if not node:
        return None
    return code[node.start_byte : node.end_byte].decode("utf-8")


# ---------------------------------------------------------------------------
# Full type-text extraction (qualifier-aware)
# ---------------------------------------------------------------------------
#
# tree-sitter exposes only a single ``type`` field on a declaration/field/param
# node — the *base* type (``int``, ``char``, ``struct foo`` …). Leading
# qualifiers and extra specifiers (``const``, ``volatile``, ``_Atomic``,
# ``restrict``, ``unsigned`` when split out, …) sit as *sibling* nodes, so
# grabbing just the ``type`` field silently drops them. These helpers rebuild
# the full leading type text by walking every specifier/qualifier child up to
# the declarator, so ``const volatile int`` no longer collapses to ``int``.

# Node types that mark the start of the declarator (the name side of a
# declaration) — the type text is everything *before* the first of these.
_DECLARATOR_START = {
    "pointer_declarator",
    "array_declarator",
    "function_declarator",
    "init_declarator",
    "identifier",
    "field_identifier",
    "bitfield_clause",
    "=",
    ";",
    ",",
}

# Leading children that are not part of the *type* proper. ``typedef`` is a bare
# keyword token (not a storage_class_specifier) so it is listed explicitly.
_LEADING_TYPE_SKIP = {"storage_class_specifier", "comment", "typedef"}


def _leading_type_text(container, code, stop_node=None):
    """Join every type specifier/qualifier child of ``container`` up to (but not
    including) the declarator into a single type string.

    Storage-class specifiers (``static``/``extern``/``register``…) are skipped —
    they are captured separately where needed and are not part of the type.
    ``stop_node`` lets callers halt at an explicit declarator whose node type is
    not a declarator token (e.g. a typedef's name is a ``type_identifier``, which
    is otherwise indistinguishable from a base-type reference).
    Returns an empty string if nothing type-like precedes the declarator.
    """
    parts = []
    for ch in container.children:
        if stop_node is not None and ch.start_byte >= stop_node.start_byte:
            break
        if ch.type in _DECLARATOR_START:
            break
        if ch.type in _LEADING_TYPE_SKIP:
            continue
        t = node_text(ch, code)
        if t:
            parts.append(t)
    return " ".join(parts)


def _append_pointer_stars(type_str, declarator_node):
    """Append one ``*`` per pointer-declarator level, matching the historical
    ``get_full_return_type`` behaviour used for globals and return types."""
    ptr_node = declarator_node
    while ptr_node and ptr_node.type == "pointer_declarator":
        type_str += " *"
        ptr_node = ptr_node.child_by_field_name("declarator")
    return type_str


def _bitfield_width(field_node, code):
    """Return the width (as a string) of a struct bitfield member, or None.

    ``unsigned int flags : 3;`` carries a ``bitfield_clause`` child holding the
    ``: width`` — dropped entirely by the plain declarator/type extraction."""
    for ch in field_node.children:
        if ch.type == "bitfield_clause":
            for inner in ch.children:
                if inner.type not in (":",):
                    txt = node_text(inner, code)
                    if txt:
                        return txt
    return None


# C23 lets an enum fix its underlying type: ``enum E : uint8_t { … }``.
# tree-sitter's C grammar doesn't understand the ``: type`` clause; the parse
# error cascades and the *entire* enum is mis-recovered as a function
# definition, so every enumerator is lost. We blank the ``: type`` clause out
# (replacing it with spaces so byte offsets and line numbers are preserved) and
# remember each enum's underlying type, keyed by the byte offset of its ``enum``
# keyword — which is exactly the ``enum_specifier`` node's ``start_byte``.
_ENUM_UNDERLYING_RE = re.compile(rb"\benum\b(?:\s+[A-Za-z_]\w*)?\s*(:[^{;]+?)\s*(?=[{;])")


def _neutralize_enum_underlying(code: bytes):
    underlying = {}
    out = bytearray(code)
    for m in _ENUM_UNDERLYING_RE.finditer(code):
        clause = m.group(1)  # e.g. b": page_flags_t"
        underlying[m.start()] = clause[1:].strip().decode("utf-8", "replace")
        for i in range(m.start(1), m.end(1)):
            if out[i] not in (0x0A, 0x0D):  # keep newlines so line numbers hold
                out[i] = 0x20
    return bytes(out), underlying


# ---------------------------------------------------------------------------
# Nested-aware struct collection
# ---------------------------------------------------------------------------

kind_map = {
    "struct_specifier": "struct",
    "union_specifier": "union",
    "enum_specifier": "enum",
}


def collect_enum_members(body_node, code):
    """Return a list of {name, value, line} dicts from an enum body."""
    members = []
    for idx, child in enumerate(body_node.children):
        if child.type != "enumerator":
            continue
        name_node = child.child_by_field_name("name")
        value_node = child.child_by_field_name("value")
        members.append(
            {
                "name": node_text(name_node, code),
                "value": node_text(value_node, code),
                "line": child.start_point[0] + 1,
                "index": idx,
            }
        )
    return members


def collect_struct_recursive(node, code, seen_ids=None):
    """
    Recursively collect a struct/union node into a dict.

    Nested anonymous composites are stored under the member's "nested" key
    rather than being hoisted to the top-level list.  Named nested composites
    are left as plain type references (they will appear as their own top-level
    declaration elsewhere in the file).
    """
    if seen_ids is None:
        seen_ids = set()

    node_id = id(node)
    if node_id in seen_ids:
        return None
    seen_ids.add(node_id)

    name_node = node.child_by_field_name("name")
    body_node = node.child_by_field_name("body")
    kind = kind_map.get(node.type, "struct")
    name = node_text(name_node, code)

    members = []
    if body_node:
        for idx, field in enumerate(body_node.children):
            if field.type != "field_declaration":
                continue

            type_node = field.child_by_field_name("type")
            decl_node = field.child_by_field_name("declarator")

            m_name = node_text(decl_node, code)
            # Full type text, keeping leading qualifiers (const/volatile/_Atomic…)
            # that the bare ``type`` field drops; fall back to the plain field.
            m_type_text = _leading_type_text(field, code) or node_text(type_node, code)

            member = {
                "name": m_name,
                "type": m_type_text,
                "bitfield": _bitfield_width(field, code),
                "line": field.start_point[0] + 1,
                "index": idx,
                "nested": None,
            }

            # If the type node is itself an anonymous composite, recurse inline
            if type_node and type_node.type in kind_map:
                inner_name_node = type_node.child_by_field_name("name")
                inner_body = type_node.child_by_field_name("body")

                if inner_name_node is None and inner_body is not None:
                    # Truly anonymous — inline it
                    nested = collect_struct_recursive(type_node, code, seen_ids)
                    if nested:
                        inner_kind = kind_map.get(type_node.type, "struct")
                        member["type"] = f"{inner_kind}"  # no name — anonymous
                        member["nested"] = nested

            members.append(member)

    result = {
        "name": name,
        "kind": kind,
        "members": members,
        "line": node.start_point[0] + 1,
    }
    return result


def extract_fn_ptr_info(type_node, declarator_node, code):
    """
    If this typedef declarator describes a function pointer, return a dict:
        {
            "return_type":  str,               # e.g. "void *"
            "parameters":   [{"type": str, "name": str|None}, ...]
        }
    Returns None for non-function-pointer typedefs.

    We walk the declarator chain looking for a function_declarator node.
    The base type node gives us the return type; pointer stars on the way
    down are accumulated into it.
    """
    # Build return type string from the base type + any leading pointer stars
    ret_type = node_text(type_node, code) or ""

    node = declarator_node
    fn_node = None
    # Walk: pointer_declarator* -> (pointer_declarator wrapping function_declarator)
    while node:
        if node.type == "pointer_declarator":
            inner = node.child_by_field_name("declarator")
            if inner and inner.type == "function_declarator":
                # The pointer star here belongs to the return type
                ret_type += " *"
                fn_node = inner
                break
            elif inner and inner.type == "pointer_declarator":
                ret_type += " *"
                node = inner
            else:
                break
        elif node.type == "function_declarator":
            fn_node = node
            break
        else:
            break

    if fn_node is None:
        return None

    params_node = fn_node.child_by_field_name("parameters")
    parameters = []
    if params_node:
        for p in params_node.children:
            if p.type == "parameter_declaration":
                p_decl_node = p.child_by_field_name("declarator")
                p_type = _leading_type_text(p, code) or node_text(
                    p.child_by_field_name("type"), code
                ) or ""
                p_name = node_text(p_decl_node, code)
                # strip pointer stars from declarator into the type
                ptr_node = p_decl_node
                while ptr_node and ptr_node.type == "pointer_declarator":
                    p_type += " *"
                    ptr_node = ptr_node.child_by_field_name("declarator")
                    p_name = node_text(ptr_node, code) if ptr_node else p_name
                parameters.append({"type": p_type.strip(), "name": p_name})
            elif p.type == "variadic_parameter":
                parameters.append({"type": "...", "name": None})

    return {"return_type": ret_type.strip(), "parameters": parameters}


def parse_c_types_and_functions(filename):

    code = Path(filename).read_bytes()
    # Blank out C23 enum underlying-type clauses (``enum E : uint8_t``) so
    # tree-sitter parses the enum body instead of mis-recovering it as a
    # function; remember each underlying type keyed by the enum's start byte.
    code, enum_underlying = _neutralize_enum_underlying(code)
    tree = parser.parse(code)
    root = tree.root_node

    # Lines inside multi-line macro bodies — used to reject phantom globals
    # tree-sitter leaks out of function-like macros (see helper for details).
    macro_lines = _macro_continuation_lines(code)

    functions = []
    structs = []
    enums = []
    typedefs = []
    globals_vars = []
    defines = []

    # Track node ids we have already recorded so we don't double-count
    # struct/enum nodes that appear both as a top-level declaration and
    # as the type inside a typedef.
    recorded_struct_ids = set()
    recorded_enum_ids = set()

    def visit(node):

        if node.type == "function_definition":

            decl = node.child_by_field_name("declarator")
            type_node = node.child_by_field_name("type")

            name, params = extract_function_name_and_params(decl, code)

            functions.append(
                {
                    "name": name,
                    "return_type": node_text(type_node, code),
                    "parameters": params,
                    "line": node.start_point[0] + 1,
                }
            )

        elif node.type == "declaration":
            decl = node.child_by_field_name("declarator")
            type_node = node.child_by_field_name("type")

            if decl and is_function_prototype(decl):
                name, params = extract_function_name_and_params(decl, code)
                ret_type = get_full_return_type(node, type_node, decl, code)

                functions.append(
                    {
                        "name": name,
                        "return_type": ret_type,
                        "parameters": params,
                        "line": node.start_point[0] + 1,
                    }
                )
            elif decl and not (
                type_node
                and type_node.type in ("struct_specifier", "union_specifier", "enum_specifier")
                and type_node.child_by_field_name("body") is not None
            ):
                # A file-scope variable declaration — most often `extern <type>
                # <name>;` in a header. (Function prototypes are handled above;
                # a struct/enum *definition* with a trailing declarator is almost
                # always `struct X { … } __packed;` — an attribute macro, not a
                # variable — so those are excluded by the guard above and the
                # struct is recorded via its own specifier branch.)
                var_name = _declarator_name(decl, code)
                var_type = get_full_return_type(node, type_node, decl, code)
                raw_text = node_text(node, code)
                on_macro_line = (node.start_point[0] + 1) in macro_lines
                if (
                    var_name
                    and not on_macro_line
                    and not _looks_like_macro_noise(var_name, var_type, raw_text)
                ):
                    storage = [
                        node_text(ch, code)
                        for ch in node.children
                        if ch.type == "storage_class_specifier"
                    ]
                    globals_vars.append(
                        {
                            "name": var_name,
                            "type": var_type,
                            "storage": " ".join(s for s in storage if s),
                            "raw_text": raw_text,
                            "line": node.start_point[0] + 1,
                        }
                    )

        elif node.type in ("struct_specifier", "union_specifier"):
            # Only record if it has a body (i.e. is a definition, not a reference)
            body = node.child_by_field_name("body")
            if body is None:
                for child in node.children:
                    visit(child)
                return

            nid = id(node)
            if nid not in recorded_struct_ids:
                recorded_struct_ids.add(nid)
                s = collect_struct_recursive(node, code)
                if s and s["members"]:
                    structs.append(s)

        elif node.type == "enum_specifier":
            # Only record enums that have a body — bare references like
            # `enum FOO` in a return type or parameter must be skipped.
            body = node.child_by_field_name("body")
            if body is None:
                for child in node.children:
                    visit(child)
                return

            nid = id(node)
            if nid not in recorded_enum_ids:
                recorded_enum_ids.add(nid)
                name = node_text(node.child_by_field_name("name"), code)
                members = collect_enum_members(body, code)
                enums.append(
                    {
                        "name": name,
                        "underlying_type": enum_underlying.get(node.start_byte),
                        "members": members,
                        "line": node.start_point[0] + 1,
                    }
                )

        elif node.type == "type_definition":

            decl = node.child_by_field_name("declarator")
            type_node = node.child_by_field_name("type")

            # If the typedef wraps an anonymous/named struct or enum defined
            # right here, make sure that definition is also recorded once.
            if type_node and type_node.type in ("struct_specifier", "union_specifier"):
                body = type_node.child_by_field_name("body")
                if body is not None:
                    nid = id(type_node)
                    if nid not in recorded_struct_ids:
                        recorded_struct_ids.add(nid)
                        s = collect_struct_recursive(type_node, code)
                        if s and s["members"]:
                            structs.append(s)

            if type_node and type_node.type == "enum_specifier":
                body = type_node.child_by_field_name("body")
                if body is not None:
                    nid = id(type_node)
                    if nid not in recorded_enum_ids:
                        recorded_enum_ids.add(nid)
                        ename = node_text(type_node.child_by_field_name("name"), code)
                        emembers = collect_enum_members(body, code)
                        enums.append(
                            {
                                "name": ename,
                                "underlying_type": enum_underlying.get(type_node.start_byte),
                                "members": emembers,
                                "line": type_node.start_point[0] + 1,
                            }
                        )

            typedefs.append(
                {
                    "name": extract_typedef_name(decl, code),
                    "type": get_typedef_type(node, type_node, decl, code),
                    "fn_ptr": extract_fn_ptr_info(type_node, decl, code),
                    "line": node.start_point[0] + 1,
                }
            )

        elif node.type == "preproc_def":
            # Simple #define NAME value
            name_node = node.child_by_field_name("name")
            val_node = node.child_by_field_name("value")
            def_name = node_text(name_node, code)
            if def_name and def_name not in IGNORED_KEYWORDS:
                raw_val = node_text(val_node, code) if val_node else ""
                raw_full = node_raw_text(node, code) or ""
                multiline = "\\" in raw_full or "\
" in (code[node.start_byte : node.end_byte].decode("utf-8"))
                defines.append(
                    {
                        "name": def_name,
                        "params": None,
                        "value": raw_val or "",
                        "raw_text": raw_full,
                        "multiline": multiline,
                        "line": node.start_point[0] + 1,
                    }
                )

        elif node.type == "preproc_function_def":
            # Function-like #define NAME(params...) body
            name_node = node.child_by_field_name("name")
            params_node = node.child_by_field_name("parameters")
            val_node = node.child_by_field_name("value")
            def_name = node_text(name_node, code)
            if def_name and def_name not in IGNORED_KEYWORDS:
                params_raw = node_text(params_node, code) if params_node else "()"
                raw_val = node_text(val_node, code) if val_node else ""
                raw_full = node_raw_text(node, code) or ""
                raw_bytes = code[node.start_byte : node.end_byte].decode("utf-8")
                multiline = "\\" in raw_full or "\
" in raw_bytes
                defines.append(
                    {
                        "name": def_name,
                        "params": params_raw,
                        "value": raw_val or "",
                        "raw_text": raw_full,
                        "multiline": multiline,
                        "line": node.start_point[0] + 1,
                    }
                )

        # Recurse — but don't descend into nodes we've already handled above
        # (struct/enum bodies are handled inside collect_struct_recursive /
        # collect_enum_members, not by the visitor).
        if node.type not in (
            "struct_specifier",
            "union_specifier",
            "enum_specifier",
            "type_definition",
            "function_definition",
            "preproc_def",
            "preproc_function_def",
        ):
            for child in node.children:
                visit(child)

    visit(root)

    return {
        "functions": functions,
        "types": {
            "structs": structs,
            "enums": enums,
            "typedefs": typedefs,
            "globals": globals_vars,
        },
        "defines": defines,
    }


def extract_commits(md_text: str):
    commits = []
    for match in COMMIT_RE.finditer(md_text):
        commits.append({"hash": match.group(1), "start_idx": match.start(), "end_idx": match.end()})
    return commits


def extract_idea_refs(text: str):
    refs = []
    for m in IDEA_REF_RE.finditer(text):
        refs.append({"string": m.group(1), "start_idx": m.start(), "end_idx": m.end()})
    return refs


def extract_audience(md_text: str):
    lines = md_text.splitlines()
    cleaned_lines = []
    audience = None
    skip_next = False

    for i, line in enumerate(lines):
        if skip_next:
            skip_next = False
            continue

        stripped = re.sub(r"^\s*(/\*+|\*+|//+)?\s*", "", line)

        m = re.match(r"^(#{1,6})\s*Audience:?\s*(.*)$", stripped, re.IGNORECASE)
        if m:
            inline_value = m.group(2).strip()
            if inline_value:
                audience = inline_value
            elif i + 1 < len(lines):
                next_line = re.sub(r"^\s*(/\*+|\*+|//+)?\s*", "", lines[i + 1]).strip()
                audience = next_line
                skip_next = True
            continue

        cleaned_lines.append(line)

    return "\n".join(cleaned_lines), audience


IDEA_BODY_HEADING_RE = re.compile(
    r"^[\s/*]*#\s*(Big|Small|Huge)\s+Idea\s*:\s*(.+?)\s*$", re.IGNORECASE
)


# Standard Idea body sections. Authors may add bespoke sections freely — this
# vocabulary is only used to flag *likely typos* (a heading that closely
# resembles a standard one), never to reject unknown section names outright.
KNOWN_IDEA_SECTIONS = {
    "credits",
    "overview",
    "background",
    "summary",
    "api",
    "errors",
    "context",
    "constraints",
    "internals",
    "strategy",
    "rationale",
    "notes",
    "changelog",
    "motivation",
    "design",
    "implementation",
    "usage",
    "examples",
    "caveats",
    "references",
    "todo",
    "bugs",
    "commits",
    "audience",
}

_IDEA_BODY_SIZE_RE = re.compile(r"^#\s*(Big|Small|Huge)\s+Idea\b", re.IGNORECASE)
_IDEA_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$")


def _normalize_idea_name(s):
    """Collapse to alphanumerics/lowercase so cosmetic differences (hyphens,
    spacing, punctuation) don't trip the signature/body name-mismatch check."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _validate_idea(path, start_line, size, name, md_text, metadata):
    """Warn on internal inconsistencies in a parsed idea:

    * the body's declared size (``# Big Idea``) disagreeing with the
      ``@idea:<size>`` signature,
    * the body's name disagreeing with the signature name, and
    * section headings that look like typos of a standard section.

    All soft warnings — the idea is still ingested; this just surfaces authoring
    slips the renderer would otherwise pass through silently.
    """
    # size: signature vs the "# Big/Small/Huge Idea" body heading
    for line in md_text.splitlines():
        bm = _IDEA_BODY_SIZE_RE.match(line.strip())
        if bm:
            body_size = bm.group(1).lower()
            if body_size != size:
                print(
                    f"[make_json] warning: {path}:{start_line}: idea '{name}' "
                    f"signature says @idea:{size} but body heading says "
                    f"'# {bm.group(1)} Idea' — size mismatch",
                    file=sys.stderr,
                )
            break

    # name: signature vs the body name line (captured into metadata)
    body_name = (metadata or {}).get("name")
    if body_name and _normalize_idea_name(body_name) != _normalize_idea_name(name):
        print(
            f"[make_json] warning: {path}:{start_line}: idea name mismatch — "
            f"signature '{name}' vs body '{body_name}'",
            file=sys.stderr,
        )

    # sections: flag likely typos (close to a standard section) but leave
    # genuinely custom sections alone.
    for line in md_text.splitlines():
        sm = _IDEA_SECTION_RE.match(line.strip())
        if not sm:
            continue
        section = sm.group(1).strip().lower()
        if section in KNOWN_IDEA_SECTIONS:
            continue
        close = difflib.get_close_matches(section, KNOWN_IDEA_SECTIONS, n=1, cutoff=0.8)
        if close:
            print(
                f"[make_json] warning: {path}:{start_line}: idea '{name}' has "
                f"section '## {sm.group(1).strip()}' — did you mean "
                f"'## {close[0].title()}'?",
                file=sys.stderr,
            )


def _warn_orphaned_ideas(path, lines, consumed_ranges):
    """Warn about idea bodies with no ``@idea:`` signature (silently dropped).

    This catches the common authoring mistake (e.g. a fully written
    ``# Small Idea: …`` block missing its ``/* @idea:small … */`` marker) that
    the extractor would otherwise skip without a trace.
    """
    for i, line in enumerate(lines):
        m = IDEA_BODY_HEADING_RE.match(line.strip())
        if not m:
            continue
        if any(lo <= i <= hi for lo, hi in consumed_ranges):
            continue  # this body belongs to a matched signature
        size = m.group(1).lower()
        print(
            f"[make_json] warning: {path}:{i + 1}: '{m.group(1)} Idea' body has no "
            f"matching '@idea:{size} …' signature — it will NOT be ingested",
            file=sys.stderr,
        )


def extract_ideas_from_file(path):
    ideas = []

    with open(path, encoding="utf-8") as f:
        lines = f.readlines()

    consumed_ranges = []
    idx = 0
    while idx < len(lines):
        line = lines[idx].strip()
        m = IDEA_SIGNATURE_RE.match(line)
        if m:
            size, name = m.groups()
            start_idx = idx + 1
            content_lines = []

            while start_idx < len(lines):
                content_line = lines[start_idx].rstrip()
                content_lines.append(content_line)
                if content_line.strip().endswith("*/"):
                    start_idx += 1
                    break
                start_idx += 1

            raw_text = "\n".join(content_lines)
            md_text = clean_comment_markers(raw_text)

            remaining_text = "\n".join(lines[start_idx:])

            refs = extract_refs(md_text, remaining_text)

            md_text, audience = extract_audience(md_text)
            metadata = extract_metadata(md_text)
            if audience:
                metadata["audience"] = audience

            refs["bugs"] = extract_bugs(md_text)
            refs["commits"] = extract_commits(md_text)
            refs["idea_refs"] = extract_idea_refs(md_text)

            _validate_idea(path, idx + 1, size, name.strip(), md_text, metadata)

            consumed_ranges.append((idx, start_idx))
            ideas.append(
                {
                    "path": str(path),
                    "name": name.strip(),
                    "size": size,
                    "start_line": idx + 1,
                    "end_line": start_idx,
                    "raw_text": raw_text,
                    "content_md": md_text,
                    "metadata": metadata,
                    "references": refs,
                }
            )

            idx = start_idx
        else:
            idx += 1

    _warn_orphaned_ideas(path, lines, consumed_ranges)
    return ideas


# Unicode non-breaking space — survives remark/MDX rendering unchanged,
# unlike regular spaces which HTML collapses.
NBSP = "\u00a0"


def _preserve_indentation(line: str, prev_blank: bool = True) -> str:
    """
    Normalise leading whitespace on a prose line.

    Markdown trims up to 3 leading spaces on paragraph lines, and turns 4+
    leading spaces into an indented code block — but *only* when the indent
    starts a new block (i.e. the previous line was blank). An indented line that
    merely continues a paragraph can't become a code block; its leading spaces
    are just comment-alignment and markdown collapses them, so we strip them
    (leaving NBSP there would inject stray gaps mid-paragraph — the doc-comment
    hanging-indent bug).

    Only when a deep (>= 4 space) indent begins a fresh block do we fall back to
    NBSP, preserving the intended visual indent without triggering a code block.

    Structural markdown lines (headings/lists/quotes/rules) are left alone —
    remark needs their leading characters to be literal ASCII.
    """
    stripped = line.lstrip()
    if re.match(r"^(#{1,6} |[-*>]\s|\d+\.\s|---|\*\*\*)", stripped):
        return line

    n_leading = len(line) - len(stripped)
    if n_leading < 4 or not prev_blank:
        return stripped

    leading = line[:n_leading].replace("\t", "    ").replace(" ", NBSP)
    return leading + stripped


def clean_comment_markers(raw: str) -> str:
    lines = raw.splitlines()
    cleaned = []
    in_code_block = False
    fence_indent = 0
    prev_blank = True  # start of block behaves like it follows a blank line

    for line in lines:
        # Strip the comment margin (/* * //) FIRST, so fenced code blocks are
        # detected correctly — otherwise the ``` sits behind a "* " margin and
        # is never recognised as a fence.
        if line.lstrip().startswith(("/*", "*", "//")):
            line = re.sub(r"^(\s*/\*+|\s*\*+|\s*//+)\s?", "", line)

        # Drop closing */ and lone bare / marker lines — comment syntax.
        if re.match(r"^\s*\*/\s*$", line):
            continue
        if re.match(r"^\s*/\s*$", line):
            continue

        stripped = line.strip()

        if stripped.startswith("```"):
            if not in_code_block:
                fence_indent = len(line) - len(line.lstrip())
            in_code_block = not in_code_block
            cleaned.append(stripped)  # flush-left fence is always valid
            prev_blank = False
            continue

        if in_code_block:
            # Keep real spaces inside code, but remove the fence's own indent so
            # the body isn't offset. Never NBSP inside a code block.
            i = 0
            while i < fence_indent and i < len(line) and line[i] == " ":
                i += 1
            cleaned.append(line[i:])
            prev_blank = False
            continue

        # Blank comment line — preserve as an empty line for paragraph breaks.
        if not line.strip():
            cleaned.append("")
            prev_blank = True
            continue

        md_header_match = re.match(r"^(\s*#{1,6})\s*(.+?):\s*(.*)$", line)
        if md_header_match:
            hashes, title, rest = md_header_match.groups()
            cleaned.append(f"{hashes.strip()} {title}")
            if rest:
                # rest continues the header line — never a fresh code block.
                cleaned.append(_preserve_indentation(rest, prev_blank=False))
        else:
            cleaned.append(_preserve_indentation(line, prev_blank=prev_blank))
        prev_blank = False

    return "\n".join(cleaned)


def extract_refs(md_text: str, code_text: str):
    import re

    combined_text = md_text + "\n" + code_text
    lines = combined_text.splitlines()

    char_to_line = []
    offset = 0
    for i, line in enumerate(lines, 1):
        for _ in line:
            char_to_line.append(i)
        char_to_line.append(i)
        offset += len(line) + 1

    functions = []
    FUNC_REF_RE = re.compile(r"`([a-zA-Z_][a-zA-Z0-9_]*)\(\)`")
    for match in FUNC_REF_RE.finditer(combined_text):
        name = match.group(1)
        if name not in IGNORED_KEYWORDS:
            start_idx = match.start()
            line_number = char_to_line[start_idx] if start_idx < len(char_to_line) else 1
            functions.append(
                {
                    "name": name,
                    "start_idx": start_idx,
                    "end_idx": match.end(),
                    "line": line_number,
                }
            )

    FILE_RE = re.compile(r"([./\w\-]+?\.(c|h|rs|cpp|txt|md))")
    files = []
    for match in FILE_RE.finditer(combined_text):
        files.append({"name": match.group(1), "start_idx": match.start(), "end_idx": match.end()})

    return {"functions": functions, "files": files}


def write_ideas_to_json(ideas, out_path):
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(ideas, f, indent=2, ensure_ascii=False)


def extract_bugs(md_text: str):
    bugs = []
    lines = md_text.splitlines()
    in_bugs_section = False
    offset = 0

    for line in lines:
        stripped = line.strip()

        if re.match(r"^##{1,6}\s*Bugs", stripped):
            in_bugs_section = True
            offset += len(line) + 1
            continue

        if in_bugs_section and re.match(r"^#{1,6}\s*\w+", stripped):
            in_bugs_section = False

        if in_bugs_section:
            for match in re.finditer(r"#(\d+)", line):
                bugs.append(
                    {
                        "start_idx": offset + match.start(),
                        "end_idx": offset + match.end(),
                        "number": int(match.group(1)),
                    }
                )

        offset += len(line) + 1

    return bugs


def main():
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <input_file> <output_json>")
        sys.exit(1)

    input_file = Path(sys.argv[1])
    output_json = Path(sys.argv[2])

    if should_ignore_file(input_file):
        sys.exit(0)

    if not input_file.is_file():
        print(f"Error: {input_file} does not exist or is not a file.")
        sys.exit(1)

    full_text = Path(input_file).read_text(encoding="utf-8")

    title = extract_file_title(full_text)
    ideas = extract_ideas_from_file(input_file)
    type_info = parse_c_types_and_functions(str(input_file))

    output = {
        "file": str(input_file),
        "title": title,
        "c_parse": type_info,
        "ideas": ideas,
    }

    write_ideas_to_json(output, output_json)


if __name__ == "__main__":
    main()
