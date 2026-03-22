#!/usr/bin/env python3

import re
import json
import sys, shutil
from pathlib import Path
import tempfile
import subprocess
from tree_sitter import Language, Parser
from pathlib import Path
from tree_sitter_language_pack import get_parser
from tree_sitter import Parser

FILE_TITLE_RE = re.compile(r"/\*\s*@title:\s*(.+?)\s*\*/", re.IGNORECASE | re.DOTALL)

IDEA_REF_RE = re.compile(r'\]:\s*"([^"]+)"')
IDEA_SIGNATURE_RE = re.compile(
    r"/\*\s*@idea:(small|big|huge)\s+(.+?)\s*\*/", re.UNICODE
)
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


def get_full_return_type(type_node, declarator_node, code_bytes):
    type_str = (
        code_bytes[type_node.start_byte : type_node.end_byte].decode("utf-8").strip()
        if type_node
        else ""
    )

    ptr_node = declarator_node
    while ptr_node and ptr_node.type == "pointer_declarator":
        type_str += " *"
        ptr_node = ptr_node.child_by_field_name("declarator")

    return type_str


def get_typedef_type(type_node, declarator_node, code_bytes):
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
                        p_type = (
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


def extract_typedef_name(declarator_node, code_bytes):
    node = declarator_node
    while node:
        if node.type in ("pointer_declarator", "function_declarator",
                         "abstract_pointer_declarator"):
            node = node.child_by_field_name("declarator")
        else:
            break
    if node:
        raw = code_bytes[node.start_byte : node.end_byte].decode("utf-8").strip()
        # tree-sitter sometimes gives us the full (*name) or (*name)(params)
        # wrapper text when it can't resolve the inner identifier as a separate
        # node.  Strip the pointer-declarator syntax to get the bare name.
        m = re.match(r'^\(\*\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)', raw)
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
        code_bytes[node.start_byte : node.end_byte].decode("utf-8").strip()
        if node
        else None
    )

    parameters = []
    if params_node:
        for p in params_node.children:
            if p.type == "parameter_declaration":
                p_type_node = p.child_by_field_name("type")
                p_decl_node = p.child_by_field_name("declarator")
                p_type = (
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


def extract_function_qualifiers(node, code_bytes):
    qualifiers = []
    for child in node.children:
        if child.type == "storage_class_specifier" or child.type == "type_qualifier":
            text = code_bytes[child.start_byte : child.end_byte].decode("utf-8").strip()
            if text:
                qualifiers.append(text)
        elif child.type == "function_specifier":
            text = code_bytes[child.start_byte : child.end_byte].decode("utf-8").strip()
            if text:
                qualifiers.append(text)
    return qualifiers


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
    # don't carry raw indentation into the JSON.
    text = re.sub(r'\n\s*', ' ', text)
    return text


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
            m_type_text = node_text(type_node, code)

            member = {
                "name": m_name,
                "type": m_type_text,
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
                        member["type"] = f"{inner_kind}"   # no name — anonymous
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
                p_type_node = p.child_by_field_name("type")
                p_decl_node = p.child_by_field_name("declarator")
                p_type = node_text(p_type_node, code) or ""
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
    tree = parser.parse(code)
    root = tree.root_node

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
                ret_type = get_full_return_type(type_node, decl, code)

                functions.append(
                    {
                        "name": name,
                        "return_type": ret_type,
                        "parameters": params,
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
                    {"name": name, "members": members, "line": node.start_point[0] + 1}
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
                                "members": emembers,
                                "line": type_node.start_point[0] + 1,
                            }
                        )

            typedefs.append(
                {
                    "name": extract_typedef_name(decl, code),
                    "type": get_typedef_type(type_node, decl, code),
                    "fn_ptr": extract_fn_ptr_info(type_node, decl, code),
                    "line": node.start_point[0] + 1,
                }
            )

        elif node.type == "preproc_def":
            # Simple #define NAME value
            name_node = node.child_by_field_name("name")
            val_node  = node.child_by_field_name("value")
            def_name  = node_text(name_node, code)
            if def_name and def_name not in IGNORED_KEYWORDS:
                raw_val = node_text(val_node, code) if val_node else ""
                raw_full = node_text(node, code) or ""
                multiline = "\\" in raw_full or "\
" in (code[node.start_byte:node.end_byte].decode("utf-8"))
                defines.append({
                    "name": def_name,
                    "params": None,
                    "value": raw_val or "",
                    "raw_text": raw_full,
                    "multiline": multiline,
                    "line": node.start_point[0] + 1,
                })

        elif node.type == "preproc_function_def":
            # Function-like #define NAME(params...) body
            name_node   = node.child_by_field_name("name")
            params_node = node.child_by_field_name("parameters")
            val_node    = node.child_by_field_name("value")
            def_name    = node_text(name_node, code)
            if def_name and def_name not in IGNORED_KEYWORDS:
                params_raw = node_text(params_node, code) if params_node else "()"
                raw_val  = node_text(val_node, code) if val_node else ""
                raw_full = node_text(node, code) or ""
                raw_bytes = code[node.start_byte:node.end_byte].decode("utf-8")
                multiline = "\\" in raw_full or "\
" in raw_bytes
                defines.append({
                    "name": def_name,
                    "params": params_raw,
                    "value": raw_val or "",
                    "raw_text": raw_full,
                    "multiline": multiline,
                    "line": node.start_point[0] + 1,
                })

        # Recurse — but don't descend into nodes we've already handled above
        # (struct/enum bodies are handled inside collect_struct_recursive /
        # collect_enum_members, not by the visitor).
        if node.type not in ("struct_specifier", "union_specifier", "enum_specifier",
                              "type_definition", "function_definition",
                              "preproc_def", "preproc_function_def"):
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
        commits.append(
            {"hash": match.group(1), "start_idx": match.start(), "end_idx": match.end()}
        )
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


def extract_ideas_from_file(path):
    ideas = []

    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    full_text = "".join(lines)
    idx = 0
    while idx < len(lines):
        line = lines[idx].strip()
        m = IDEA_SIGNATURE_RE.match(line)
        if m:
            size, name = m.groups()
            start_idx = idx + 1
            content_lines = []

            while start_idx < len(lines):
                l = lines[start_idx].rstrip()
                content_lines.append(l)
                if l.strip().endswith("*/"):
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

    return ideas


# Unicode non-breaking space — survives remark/MDX rendering unchanged,
# unlike regular spaces which HTML collapses.
NBSP = "\u00a0"


def _preserve_indentation(line: str) -> str:
    """
    Convert leading regular spaces on a content line to NBSP so that
    intentional indentation in comment prose survives the HTML/markdown
    render pipeline.

    Tabs are converted to 4 NBSP each.  We only touch the leading
    whitespace — interior spacing is left alone so word-wrap still works.

    Lines that are markdown structural elements are left completely
    untouched because remark needs their leading characters to be
    literal ASCII:
      - headings          (# …)
      - list items        (- … / * … / 1. …)
      - blockquotes       (> …)
      - horizontal rules  (--- / ***)
    """
    stripped = line.lstrip()

    # Leave structural markdown lines alone
    if re.match(r"^(#{1,6} |[-*>]\s|\d+\.\s|---|\*\*\*)", stripped):
        return line

    # Count and replace leading whitespace
    n_leading = len(line) - len(stripped)
    if n_leading == 0:
        return line

    leading_raw = line[:n_leading]
    # Convert tabs → 4 spaces first, then spaces → NBSP
    leading_raw = leading_raw.replace("\t", "    ")
    leading_nbsp = leading_raw.replace(" ", NBSP)
    return leading_nbsp + stripped


def clean_comment_markers(raw: str) -> str:
    lines = raw.splitlines()
    cleaned = []
    in_code_block = False

    for l in lines:
        stripped = l.strip()

        if stripped.startswith("```"):
            in_code_block = not in_code_block
            cleaned.append(stripped)
            continue

        if in_code_block:
            # Inside a fenced code block keep the raw line as-is — the
            # renderer handles whitespace inside <code> correctly already.
            cleaned.append(l)
            continue

        line = l
        if (
            line.lstrip().startswith("/*")
            or line.lstrip().startswith("*")
            or line.lstrip().startswith("//")
        ):
            line = re.sub(r"^(\s*/\*+|\s*\*+|\s*//+)\s?", "", line)

        # Drop the closing */ marker and lone bare / lines — comment syntax.
        if re.match(r"^\s*\*/\s*$", line):
            continue
        if re.match(r"^\s*/\s*$", line):
            continue

        # Blank comment line — preserve as empty line for paragraph breaks.
        if not line.strip():
            cleaned.append("")
            continue

        md_header_match = re.match(r"^(\s*#{1,6})\s*(.+?):\s*(.*)$", line)
        if md_header_match:
            hashes, title, rest = md_header_match.groups()
            cleaned.append(f"{hashes} {title}")
            if rest:
                # Preserve indentation on the rest-of-header body line
                cleaned.append(_preserve_indentation(rest))
        else:
            cleaned.append(_preserve_indentation(line))

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
            line_number = (
                char_to_line[start_idx] if start_idx < len(char_to_line) else 1
            )
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
        files.append(
            {"name": match.group(1), "start_idx": match.start(), "end_idx": match.end()}
        )

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
