#!/usr/bin/env python3
"""Compile the per-file JSON (from make_json.py) into Starlight ``.mdx`` pages.

Reads every JSON file in ``json_output/`` and emits cross-linked reference pages
with Starlight frontmatter, GitHub/source-browser links, and Astro component
imports (Badge, Card, Aside, Icon, Tabs). Code is rendered via the linkified
``<SourceBlock>`` component (see sourceblock.py) so each symbol is clickable.
"""

import json
import os
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

from docmodel import Composite, Enum, Field, Function, Module, Typedef, Variable

SOURCE_REPO_URL = "https://github.com/axvonx/charmos/blob/main"
BUG_URL_BASE = "https://github.com/axvonx/charmos/issues"
DOCS_ROOT = Path("./docs")

IGNORED_KEYWORDS = {"if", "for", "while", "switch", "return", "sizeof"}

ASIDE_MAP = {
    "note": "note",
    "warning": "caution",
    "danger": "danger",
    "tip": "tip",
    "success": "success",
}
DEFAULT_ASIDE_TYPE = "note"

STATUS_BADGE_MAPPING = {
    "STABLE": "success",
    "UNSTABLE": "caution",
    "LEGACY": "note",
    "DEPRECATED": "danger",
    "EXPERIMENTAL": "caution",
}


STATUS_CARD_MAP = {
    "STABLE": ("approve-check-circle", "green"),
    "UNSTABLE": ("warning", "yellow"),
    "LEGACY": ("information", "purple"),
    "DEPRECATED": ("error", "red"),
    "EXPERIMENTAL": ("warning", "yellow"),
}

HEADER_ICON_MAP = {
    "overview": ("star", "goldenrod"),
    "background": ("open-book", "brown"),
    "summary": ("document", "gray"),
    "errors": ("error", "red"),
    "context": ("magnifier", "blue"),
    "constraints": ("warning", "yellow"),
    "internals": ("setting", "gray"),
    "strategy": ("puzzle", "green"),
    "notes": ("pencil", "white"),
    "changelog": ("bars", "white"),
    "rationale": ("rocket", "orange"),
    "api": ("laptop", "blue"),
}


def convert_h2_to_header_with_icon(md: str) -> str:
    lines = md.split("\n")
    result = []

    for line in lines:
        m = re.match(r"^##\s+(.*)", line)
        if m:
            title = m.group(1).strip()
            title_lower = title.lower()

            if title_lower in HEADER_ICON_MAP:
                icon_name, icon_color = HEADER_ICON_MAP[title_lower]
                line = (
                    f"## {title} "
                    f'<span style="display:inline-block; vertical-align:middle; margin-left:0.25rem; position:relative">'
                    f'<Icon name="{icon_name}" color="{icon_color}" style="width:1.2em; height:1.2em;" />'
                    f"</span>\n"
                )
            else:
                line = f"## {title}\n"

        result.append(line)

    return "\n".join(result)


def convert_blockquotes_to_asides(md: str) -> str:
    lines = md.split("\n")
    result = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]

        # Not the start of an aside
        if not line.strip().startswith(">"):
            result.append(line)
            i += 1
            continue

        # BEGIN capturing aside
        aside_lines = []

        while i < n:
            current = lines[i]

            # 1) End if blank line
            if not current.strip():
                break

            # 2) End if H2 header (e.g. "## Something")
            if re.match(r"^##\s", current):
                break

            aside_lines.append(current)
            i += 1

        # Remove '>' prefix where present
        stripped = []
        for line in aside_lines:
            if line.strip().startswith(">"):
                stripped.append(re.sub(r"^\s*>\s?", "", line))
            else:
                stripped.append(line)

        # Determine aside type
        first_line = stripped[0]
        parts = first_line.split()
        if parts and parts[0].lower() in ASIDE_MAP:
            aside_type = ASIDE_MAP[parts[0].lower()]
            stripped[0] = " ".join(parts[1:]) if len(parts) > 1 else ""
        else:
            aside_type = DEFAULT_ASIDE_TYPE

        aside_content = "\n".join(stripped).strip()

        aside_block = f'<Aside type="{aside_type}">\n' f"{aside_content}\n" f"</Aside>"
        result.append(aside_block)

        # If we ended on a blank line → preserve it
        if i < n and not lines[i].strip():
            result.append("")  # keep the blank line
            i += 1

        # If we ended because of an H1 header, do NOT consume it.
        # We want the next loop iteration to process it correctly.

    return "\n".join(result)


def print_single_line(*args, progress: float | None = None, **kwargs):
    text = " ".join(str(arg) for arg in args)
    terminal_width = shutil.get_terminal_size((80, 20)).columns

    if progress is not None:
        progress_str = f"{int(progress * 100)}%"
        space_to_progress = max(terminal_width - len(text) - len(progress_str), 1)
        output = "\r" + text + " " * space_to_progress + progress_str
    else:
        spaces_to_clear = max(terminal_width - len(text), 0)
        output = "\r" + text + " " * spaces_to_clear

    flush = kwargs.get("flush", True)
    sys.stdout.write(output)
    if flush:
        sys.stdout.flush()


def make_docs_path(idea_path: str, md_root: Path = DOCS_ROOT) -> Path:
    src_path = Path(idea_path)
    try:
        relative_path = src_path.relative_to("charmos/include")
    except ValueError:
        relative_path = src_path

    md_file = relative_path.with_suffix(".mdx").name
    md_path = md_root / md_file
    md_path.parent.mkdir(parents=True, exist_ok=True)
    return md_path


def normalize_type_name(type_str: str) -> str:
    type_str = type_str.strip()
    type_str = re.sub(r"\bconst\b", "", type_str)
    type_str = type_str.replace("*", "")
    type_str = re.sub(r"\s+", " ", type_str)
    type_str = type_str.replace("[]", "")
    return type_str.strip().lower()


# Root path under which all generated reference pages live on the doc site.
REFERENCE_PREFIX = "/reference"

# Source tree root where dir_doc_name files live.
SOURCE_INCLUDE_ROOT = Path("charmos/include")


def _dir_name_to_slug(name: str) -> str:
    """
    Convert a human-readable dir_doc_name label to a URL slug: lowercase, with
    runs of non-alphanumerics collapsed to single hyphens (e.g. "Scheduling and
    Multitasking" -> "scheduling-and-multitasking"). This is the authoritative
    slug we emit as frontmatter — no longer a prediction of how Starlight would
    slugify a renamed directory.
    """
    import re as _re

    slug = name.strip().lower()
    slug = _re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug


def build_dir_label_map(src_root: Path = SOURCE_INCLUDE_ROOT) -> dict:
    """
    Walk the source include tree and collect every ``dir_doc_name`` file into
    a map from directory path (relative to src_root, as a tuple of segments)
    to its human-readable *label* — the verbatim file contents.

    e.g.  ("sch",) -> "Scheduling and Multitasking"

    This is the single source of truth for both the doc-site directory name
    (which Starlight uses verbatim as the sidebar group label) and the URL
    slug (the label, slugified). Replaces the old two-step "rename the dir on
    disk, then re-predict Starlight's slug" coupling.
    """
    label_map = {}
    if not src_root.exists():
        return label_map
    for name_file in src_root.rglob("dir_doc_name"):
        new_name = name_file.read_text(encoding="utf-8").strip()
        if not new_name:
            continue
        try:
            rel = name_file.parent.relative_to(src_root)
        except ValueError:
            continue
        label_map[rel.parts] = new_name
    return label_map


def _resolve_dir_segments(rel_dir: Path, label_map: dict) -> list[str]:
    """Resolve each segment of ``rel_dir`` through ``label_map`` (greedy from
    the root down, so nested renames compose). Unmapped segments pass through
    unchanged."""
    parts = rel_dir.parts  # e.g. ("sch", "irq")
    result = []
    for i, part in enumerate(parts):
        prefix = tuple(parts[: i + 1])
        result.append(label_map.get(prefix, part))
    return result


def dir_label_path(rel_dir: Path, label_map: dict) -> str:
    """Output directory path using human labels — this is the directory name
    on the doc site, which Starlight autogenerate uses verbatim as the sidebar
    group label (e.g. ``sch/irq`` -> ``Scheduling and Multitasking/irq``)."""
    return "/".join(_resolve_dir_segments(rel_dir, label_map))


def dir_slug_path(rel_dir: Path, label_map: dict) -> str:
    """URL slug path (each resolved label slugified). Emitted as explicit
    ``slug:`` frontmatter so URLs never depend on Starlight's own
    slugification (e.g. ``sch`` -> ``scheduling-and-multitasking``)."""
    return "/".join(_dir_name_to_slug(s) for s in _resolve_dir_segments(rel_dir, label_map))


def doc_page_url(source_file: str, label_map: dict) -> str | None:
    """Root-relative doc-page URL for a documented source file (no anchor), or
    None if the file lives outside the reference tree. This is the single place
    that maps ``charmos/include/<dir>/<stem>.h`` → ``/reference/<slug>/<stem>/``,
    so prose/footnote *references* to a documented file link to its doc page
    (policy: references → docs) instead of GitHub source."""
    try:
        rel = Path(source_file).relative_to("charmos/include")
    except ValueError:
        return None
    slug_dir = dir_slug_path(rel.parent, label_map)
    stem = rel.stem
    return f"{REFERENCE_PREFIX}/{slug_dir}/{stem}/" if slug_dir else f"{REFERENCE_PREFIX}/{stem}/"


def type_anchor(kind: str, name: str) -> str:
    """Anchor id for a documented construct.

    Shared by both build_type_doc_table (link target) and the <SourceBlock> id
    (link destination), so cross-page references always resolve — no more
    predicting how Starlight would slugify a heading.
    """
    return f"{kind}-{name.lower()}"


def build_type_doc_table(
    c_parse_map: dict, docs_root: Path, src_root: Path = SOURCE_INCLUDE_ROOT
) -> dict:
    """
    Build a mapping from normalised type keys to doc-site URLs.

    URLs are root-relative, under REFERENCE_PREFIX, with directory segments
    renamed according to any dir_doc_name files present in the source tree.

    e.g.  "struct rt_scheduler"
          -> /reference/scheduling-and-multitasking/rt_sched#struct-rt-scheduler
    """
    label_map = build_dir_label_map(src_root)
    doc_table = {}

    for file_path, c_parse in c_parse_map.items():
        src_path = Path(file_path)
        try:
            relative_path = src_path.relative_to("charmos/include")
        except ValueError:
            relative_path = src_path

        mdx_stem = relative_path.stem  # e.g. "rt_sched"
        mdx_dir = relative_path.parent  # e.g. Path("sch")

        # Slugify directory segments, then prepend the reference prefix. This is
        # the SAME slug make_md writes as `slug:` frontmatter, so link targets
        # and page URLs are guaranteed consistent (one source of truth).
        slug_dir = dir_slug_path(mdx_dir, label_map)
        if slug_dir:
            doc_base = f"{REFERENCE_PREFIX}/{slug_dir}/{mdx_stem}/"
        else:
            doc_base = f"{REFERENCE_PREFIX}/{mdx_stem}/"

        types = c_parse.get("types", {})

        for s in types.get("structs", []):
            name = s.get("name")
            if not name:
                continue
            # Key by the actual kind (struct/union) so symbol_target can match a
            # union reference; the anchor also uses the real kind.
            kind = (s.get("kind") or "struct").lower()
            doc_table[f"{kind} {name}".lower()] = doc_base + "#" + type_anchor(kind, name)

        for e in types.get("enums", []):
            name = e.get("name")
            if not name:
                continue
            doc_table[f"enum {name}".lower()] = doc_base + "#" + type_anchor("enum", name)

        for t in types.get("typedefs", []):
            name = t.get("name")
            if not name:
                continue
            # Each typedef gets its own anchor based on its name so multiple
            # typedefs on one page link to the right one.
            doc_table[name.lower()] = doc_base + "#" + type_anchor("type-alias", name)

        # Functions are documented on their page too, so cross-page calls can
        # link internally (and feed the site graph) rather than to source.
        for fn in c_parse.get("functions", []):
            name = fn.get("name")
            if not name:
                continue
            doc_table.setdefault(name.lower(), doc_base + "#" + type_anchor("function", name))

        # Macros and global variables likewise get doc anchors, so *references*
        # to them elsewhere resolve to their doc page (their own definition still
        # points at source — see the def-name override in the code renderer).
        for d in c_parse.get("defines", []):
            name = d.get("name")
            if not name:
                continue
            doc_table.setdefault(name.lower(), doc_base + "#" + type_anchor("macro", name))

        for g in c_parse.get("types", {}).get("globals", []):
            name = g.get("name")
            if not name:
                continue
            doc_table.setdefault(name.lower(), doc_base + "#" + type_anchor("variable", name))

    return doc_table


def generate_github_link_safe(file_path: str, line: int | None = None) -> str:
    src_path = Path(file_path)
    try:
        relative_path = src_path.relative_to("charmos")
    except ValueError:
        relative_path = src_path

    url = f"{SOURCE_REPO_URL}/{relative_path.as_posix()}"
    if line is not None:
        url += f"#L{line}"

    url = re.sub(r"/blob/main/charmos/", "/blob/main/", url)
    return url


def load_json_dir(json_dir: Path):
    all_ideas = []
    c_parse_map = {}
    for path in json_dir.glob("*.json"):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
            ideas = data.get("ideas", [])
            all_ideas.extend(ideas)
            c_parse_map[data.get("file")] = data.get("c_parse", {})
    return all_ideas, c_parse_map


def link_functions_in_md(md_text: str, functions_map: dict):
    FUNC_RE = re.compile(r"`([a-zA-Z_][a-zA-Z0-9_]*)\(\)`")

    def replacer(match):
        fn = match.group(1)
        url = functions_map.get(fn)
        if url:
            return f"[`{fn}()`]({url})"
        return match.group(0)

    return FUNC_RE.sub(replacer, md_text)


def link_files_in_md(md_text: str, files_map: dict):
    FILE_RE = re.compile(r"`([\w./-]+\.(c|h|rs|cpp|txt|md))`")

    def replacer(match):
        file = match.group(1)
        url = files_map.get(file)
        if url:
            return f"[`{file}`]({url})"
        return match.group(0)

    return FILE_RE.sub(replacer, md_text)


def link_commits_in_md(md_text: str):
    COMMIT_RE = re.compile(r"commit\s+([0-9a-f]{7,40})", re.IGNORECASE)

    def replacer(match):
        h = match.group(1)
        url = f"https://github.com/axvonx/charmos/commit/{h}"
        return f"[commit {h}]({url})"

    return COMMIT_RE.sub(replacer, md_text)


def link_bugs_in_md(md_text: str):
    BUG_RE = re.compile(r"#(\d+)")

    def replacer(match):
        bug_number = match.group(1)
        url = f"{BUG_URL_BASE}/{bug_number}"
        return f"[#{bug_number}]({url})"

    return BUG_RE.sub(replacer, md_text)


def extract_mdx_title(md_text: str):
    lines = md_text.splitlines()
    cleaned_lines = []
    idea_type = None
    idea_name = None
    credits = None

    idx = 0
    while idx < len(lines):
        line = lines[idx].strip()

        m = re.match(r"^#\s*(Big|Small|Huge)\s+Idea\s*:\s*(.+)$", line, re.IGNORECASE)
        if m:
            idea_type, idea_name = m.groups()
            idx += 1
            continue

        m2 = re.match(r"^#\s*(Big|Small|Huge)\s+Idea\s*$", line, re.IGNORECASE)
        if m2 and idx + 1 < len(lines):
            idea_type = m2.group(1)
            next_line = lines[idx + 1].strip()
            if next_line:
                idea_name = next_line
                idx += 2
                continue

        if re.match(r"^##\s*Credits\s*$", line, re.IGNORECASE) and idx + 1 < len(lines):
            credits_line = lines[idx + 1].strip()
            if credits_line:
                credits = credits_line
                idx += 2
                if idx < len(lines) and lines[idx].strip() == "":
                    idx += 1
                continue

        cleaned_lines.append(lines[idx])
        idx += 1

    if not idea_type or not idea_name:
        idea_type = "Idea"
        idea_name = "Untitled"

    mdx_title_lines = [f"# {idea_type.capitalize()} Idea: {idea_name}"]
    if credits:
        mdx_title_lines.append(f"**Credits:** {credits}")

    mdx_title = "\n".join(mdx_title_lines)
    cleaned_body = "\n".join(cleaned_lines).strip()
    return mdx_title, cleaned_body


def build_json_title_index(json_dir: Path):
    index = {}
    for jf in json_dir.glob("*.json"):
        with open(jf, encoding="utf-8") as f:
            data = json.load(f)

        title = data.get("title")
        src_file = data.get("file")

        if title and src_file:
            title_lower = title.strip().lower()
            index[title_lower] = src_file

    return index


def embed_idea_refs_in_md(md_text: str, idea, json_title_index=None, label_map=None):
    refs = idea.get("references", {}).get("idea_refs", [])
    if not refs or not json_title_index:
        return md_text

    for ref in refs:
        ref_string = ref["string"]
        ref_lower = ref_string.lower()

        if ref_lower in json_title_index:
            json_src = json_title_index[ref_lower]
            # A footnote cross-link ("APCs", "DPCs", …) is a reference → link to
            # the target's doc page, not its GitHub source. This also makes the
            # idea page an internal link source, feeding the site graph.
            link_url = (doc_page_url(json_src, label_map) if label_map else None) or (
                generate_github_link_safe(json_src)
            )
            link_md = f"[{ref_string}]({link_url})"
            md_text = re.sub(re.escape(ref_string), link_md, md_text)

    return md_text


def build_global_function_table(c_parse_map: dict, doc_table: dict | None = None):
    """Map function name → URL used when the name is mentioned in idea prose.

    A prose mention is a *reference*, so it links to the function's doc page
    (policy: references → docs) when documented — which also makes the idea page
    an internal link source, so the theme's backlinks panel/site graph surface
    "which ideas reference this API". Falls back to the GitHub source link when
    the function has no doc anchor.
    """
    func_table = {}
    for file_path, c_parse in c_parse_map.items():
        for f in c_parse.get("functions", []):
            name = f.get("name")
            if not name or name in func_table:
                continue
            doc_url = doc_table.get(name.lower()) if doc_table else None
            func_table[name] = doc_url or generate_github_link_safe(file_path, f.get("line"))
    return func_table


def append_defines_to_md(md_lines, module: Module):
    if not module.macros:
        return md_lines

    file_path = module.file

    md_lines.append("\n## Macros\n")

    for d in module.macros:
        name = d.name or ""
        params = d.params  # None for object-like, str "(a,b)" for fn-like
        raw_text = (d.raw_text or "").strip()

        if d.multiline and raw_text:
            # Preserve the real multi-line layout; only tidy the source-alignment
            # whitespace that padded each line-continuation backslash to column N.
            code = re.sub(r"[ \t]+\\(\r?\n)", r" \\\1", raw_text)
        else:
            value = re.sub(r"\s+", " ", (d.value or "")).strip()
            sig = (name + params) if params is not None else name
            code = "#define " + sig + (f" {value}" if value else "")

        # Item heading → TOC entry (rewritten to "{name}" + hidden by the rehype
        # plugin); SourceBlock renders the macro with its name linked + body kept.
        md_lines.append(f"### macro {name}\n")
        md_lines.append(
            fence_or_sourceblock(code, def_name=name, def_href=source_def_href(file_path, d.line))
        )
        md_lines.append("")

    md_lines.append("\n---\n")
    return md_lines


def status_to_badge(status: str) -> str:
    status = status.upper().strip()
    variant = STATUS_BADGE_MAPPING.get(status, "tip")  # default to tip
    return f'<Badge text="{status.capitalize()}" variant="{variant}" />'


def _render_struct_body(members: list[Field], indent: int, col_width: int) -> list:
    """
    Recursively render struct/union members as plain C code lines.
    Nested anonymous composites are rendered inline with increased indentation.
    """
    pad = " " * indent
    lines = []
    for m in members:
        m_type = (m.type or "").strip()
        m_name = (m.name or "").strip().replace("\n", "")
        m_offset = m.offset
        nested = m.nested
        offset_comment = f"  //0x{m_offset:x}" if m_offset is not None else ""

        if nested:
            inner_members = nested.members
            inner_col = min(
                max((len((im.type or "").strip()) for im in inner_members), default=8) + 2,
                40,
            )
            lines.append(f"{pad}{nested.kind} {{")
            lines.extend(_render_struct_body(inner_members, indent + 4, inner_col))
            closing_name = f" {m_name}" if m_name else ""
            lines.append(f"{pad}}}{closing_name};{offset_comment}")
        else:
            type_padding = " " * max(col_width - len(m_type), 1)
            lines.append(f"{pad}{m_type}{type_padding}{m_name};{offset_comment}")

    return lines


def format_struct_as_c_code(s: Composite, file: str | None) -> str:
    """
    Render a struct/union as a <SourceBlock> whose members and member types are
    clickable (each linking to its on-site reference page). Nested anonymous
    composites are inlined. The cross-page anchor is carried by a CSS-hidden
    heading emitted alongside the block.
    """
    name = s.name or "?"
    kind = s.kind or "struct"
    size = s.size
    members = s.members

    size_comment = f"//0x{size:x} bytes  " if size is not None else ""

    top_level_types = [m for m in members if not m.nested]
    col_width = 16
    if top_level_types:
        col_width = min(max(len((m.type or "").strip()) for m in top_level_types) + 2, 40)

    code_lines = [f"{size_comment}{kind} {name} {{"]
    code_lines.extend(_render_struct_body(members, indent=4, col_width=col_width))
    code_lines.append("};")

    return fence_or_sourceblock(
        "\n".join(code_lines),
        def_name=name,
        def_href=source_def_href(file, s.line),
    )


def format_enum_as_c_code(e: Enum, file: str | None) -> str:
    """Render an enum as a clickable <SourceBlock>; its name links to source."""
    name = e.name or "?"

    code_lines = [f"enum {name} {{"]
    for m in e.members:
        m_name = (m.name or "").strip()
        value_str = f" = {m.value}" if m.value is not None else ""
        code_lines.append(f"    {m_name}{value_str},")
    code_lines.append("};")

    return fence_or_sourceblock(
        "\n".join(code_lines),
        def_name=name,
        def_href=source_def_href(file, e.line),
    )


def format_typedef_fn_ptr(t: Typedef, file: str | None) -> str:
    """Render a typedef as a clickable <SourceBlock> with anchor + definition link."""
    fn_ptr = t.fn_ptr
    alias = t.name or "?"

    if not fn_ptr:
        raw_type = (t.type or "").strip()
        code = "typedef " + raw_type + " " + alias + ";"
    else:
        ret_type = (fn_ptr.return_type or "void").strip()
        param_strs = []
        for p in fn_ptr.parameters:
            p_type = (p.type or "").strip()
            p_name = p.name
            param_strs.append((p_type + " " + p_name).strip() if p_name else p_type)
        code = "typedef " + ret_type + " (*" + alias + ")(" + ", ".join(param_strs) + ");"

    return fence_or_sourceblock(code, def_name=alias, def_href=source_def_href(file, t.line))


# ── SourceBlock code rendering ────────────────────────────────────────────────
#
# When a clang-accurate symbol index is available, C code blocks are rendered as
# <SourceBlock> components whose identifiers link to their definitions (see
# sourceblock.py). Otherwise we fall back to a plain fenced ```c block, so the
# docs still build without the index.

CLANG_INDEX_PATH = Path("clang_index.json")

# Per-run renderer: callable(c_code, title) -> mdx string. None ⇒ fallback fence.
_CODE_RENDERER = None


def fence_or_sourceblock(c_code, def_name=None, def_href=None):
    """Render C as a <SourceBlock> when an index is loaded, else a ```c fence.

    References inside the block link to their on-site reference page; the
    construct's own name (``def_name``) is instead pointed at its source
    definition (``def_href``) so "definition → code browser, references → docs".
    The cross-page anchor for the construct is provided by a (CSS-hidden)
    markdown heading emitted alongside this block.
    """
    if _CODE_RENDERER is not None:
        return _CODE_RENDERER(c_code, def_name, def_href)
    return "```c\n" + c_code + "\n```"


# Authored ```c fenced blocks inside idea prose (flush-left after
# clean_comment_markers dedents them). Matched as whole blocks so the body is
# handed to the linkifier verbatim.
_C_FENCE_RE = re.compile(r"(?ms)^```c[ \t]*\n(.*?)\n```[ \t]*$")


def linkify_code_fences(md: str) -> str:
    """Turn authored ```c fenced blocks into linkified <SourceBlock>s, so example
    code in ideas gets the same click-to-definition as generated code. Every
    identifier resolves as a *reference* (no def override), matching how a
    hand-written example uses the API. No-op without a clang index (the fence is
    left as-is so it still renders as a plain code block)."""
    if _CODE_RENDERER is None:
        return md
    return _C_FENCE_RE.sub(lambda m: fence_or_sourceblock(m.group(1)), md)


# When a Woboq source browser has been generated, symbols link into it
# (/source/charmos/<file>.html#<line>); otherwise they fall back to GitHub blob.
SOURCE_BROWSER_BASE = os.environ.get("CHARMOS_SOURCE_BROWSER")


def source_def_href(data_file, line):
    """Link a construct's definition to the code browser (or GitHub blob)."""
    if not data_file:
        return None
    if SOURCE_BROWSER_BASE:
        rel = data_file[len("charmos/") :] if data_file.startswith("charmos/") else data_file
        return f"{SOURCE_BROWSER_BASE}/{rel}.html#{line}"
    return generate_github_link_safe(data_file, line)


def source_browser_file_href(data_file):
    """File-level code-browser URL (no line anchor), or None if unavailable."""
    if not (SOURCE_BROWSER_BASE and data_file):
        return None
    rel = data_file[len("charmos/") :] if data_file.startswith("charmos/") else data_file
    return f"{SOURCE_BROWSER_BASE}/{rel}.html"


def page_source_header(data_file):
    """The row at the top of a reference page: the file path plus 'View source'
    (code browser) and 'View on GitHub' action links."""
    rel = data_file[len("charmos/") :] if data_file.startswith("charmos/") else data_file
    github_url = generate_github_link_safe(data_file)
    source_url = source_browser_file_href(data_file)

    links = []
    if source_url:
        links.append(
            f'<a class="page-source-link" href="{source_url}">'
            f'<Icon name="seti:c" size="1em" /> View source</a>'
        )
    links.append(
        f'<a class="page-source-link" href="{github_url}">'
        f'<Icon name="github" size="1em" /> View on GitHub</a>'
    )
    return (
        '<div class="page-source">\n'
        f'  <code class="page-source-path">{rel}</code>\n'
        f'  <span class="page-source-actions">{"".join(links)}</span>\n'
        "</div>\n"
    )


def _doc_table_keys(kind: str, name: str):
    """Candidate doc_table keys for a clang symbol, most specific first.

    doc_table keys structs/unions as ``"<kind> <name>"``, enums as
    ``"enum <name>"``, and typedefs/functions by bare name — so the lookup has
    to reconstruct the key from the symbol's clang kind.
    """
    if kind in ("struct", "union", "class"):
        yield f"{kind} {name}"
        yield f"struct {name}"  # unions were historically keyed under "struct"
    elif kind == "enum":
        yield f"enum {name}"
    yield name  # typedef / function / macro / fallthrough


def symbol_target(sym, doc_table):
    """Where a resolved symbol should link.

    Documented symbols link to their reference page on this site (which also
    feeds the site graph); only undocumented ones fall back to source — the
    clang source browser when available, else GitHub blob.
    """
    name = sym["name"].lower()
    kind = (sym.get("kind") or "").lower()
    for key in _doc_table_keys(kind, name):
        if key in doc_table:
            return doc_table[key]
    if SOURCE_BROWSER_BASE:
        return f"{SOURCE_BROWSER_BASE}/{sym['file']}.html#{sym['line']}"
    return f"{SOURCE_REPO_URL}/{sym['file']}#L{sym['line']}"


def _make_code_renderer(doc_table):
    """Build a SourceBlock renderer backed by the clang index, or None."""
    if not CLANG_INDEX_PATH.exists():
        return None
    try:
        import json as _json

        import sourceblock

        index = _json.loads(CLANG_INDEX_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None

    resolver = sourceblock.index_resolver(index, lambda sym: symbol_target(sym, doc_table))

    def render(c_code, def_name=None, def_href=None):
        # References (member types, parameter types, called symbols) resolve
        # through the index to their on-site reference page. The construct's OWN
        # name is the *definition*, so it's pointed at the code browser instead
        # (def_href) — "definition → source, references → docs".
        segs = sourceblock.render(c_code, resolver)
        if def_name and def_href:
            for s in segs:
                if s.text == def_name and s.cls in ("ident", "type"):
                    s.href, s.symbol = def_href, def_name
                    break
        return sourceblock.to_mdx(segs)

    return render


_STARLIGHT_IMPORTS = [
    "import { Badge } from '@astrojs/starlight/components';",
    "import { Card } from '@astrojs/starlight/components';",
    "import { Aside } from '@astrojs/starlight/components';",
    "import { Icon } from '@astrojs/starlight/components';",
    "import { Tabs, TabItem } from '@astrojs/starlight/components';",
]


def assemble_page_text(title, author, status, badge, body, slug=None):
    """Compose a page's MDX deterministically: frontmatter, imports, body.

    Replaces the old insert_string_at_line line-number splicing, which broke
    whenever the frontmatter length changed (e.g. when a sidebar badge was
    added). ``badge`` is None or a (text, variant) tuple. ``slug`` is the
    explicit Starlight URL slug (root-relative, no leading slash) or None.
    """
    fm = ["---", f'title: "{title}"']
    if slug:
        fm.append(f"slug: {slug}")
    fm += [f'author: "{author}"', f'status: "{status}"']
    if badge is not None:
        text, variant = badge
        fm += ["sidebar:", "  badge:", f"    text: {text}", f"    variant: {variant}"]
    fm.append("---")

    imports = list(_STARLIGHT_IMPORTS)
    if _CODE_RENDERER is not None:
        imports.append("import SourceBlock from '@components/SourceBlock.astro';")

    return "\n".join(fm) + "\n\n" + "\n".join(imports) + "\n\n" + body


def generate_docs(json_dir: Path):
    global _CODE_RENDERER
    ideas, c_parse_map = load_json_dir(json_dir)
    doc_table = build_type_doc_table(c_parse_map, DOCS_ROOT)
    _CODE_RENDERER = _make_code_renderer(doc_table)
    # Single source of truth for directory labels + URL slugs (see
    # build_dir_label_map). Pages are written into label-named directories and
    # carry an explicit `slug:`, so there is no separate on-disk rename pass.
    label_map = build_dir_label_map()

    # Step 1: Group ideas by their source file

    functions_map = {}
    files_map = {}
    ideas_by_file = defaultdict(list)
    json_files = list(json_dir.glob("*.json"))
    total_files = len(json_files)
    json_title_index = build_json_title_index(json_dir)

    for idea in ideas:
        src_file = idea["path"]
        ideas_by_file[src_file].append(idea)

    functions_map = build_global_function_table(c_parse_map, doc_table)

    # Map a referenced file's basename → its documented source path, so a file
    # mention in prose links to that file's doc page (references → docs) rather
    # than GitHub. Falls back to a GitHub link for undocumented files.
    source_by_name = {}
    for src in c_parse_map:
        source_by_name.setdefault(Path(src).name, src)

    # Second pass: build function/file links
    for idea in ideas:
        for f in idea.get("references", {}).get("files", []):
            fname = f["name"]
            src = source_by_name.get(fname) or source_by_name.get(Path(fname).name)
            files_map[fname] = (doc_page_url(src, label_map) if src else None) or (
                generate_github_link_safe(fname)
            )

    # Step 2: For each JSON file, write the Markdown with ideas on top
    for i, json_file in enumerate(json_dir.glob("*.json"), start=1):
        with open(json_file, encoding="utf-8") as f:
            data = json.load(f)

        json_title = data.get("title")

        source_path = Path(data["file"])
        in_reference = True
        try:
            relative_path = source_path.relative_to("charmos/include")
        except ValueError:
            relative_path = source_path
            in_reference = False

        stem = relative_path.stem
        # Write into the label-named directory (Starlight uses it verbatim as
        # the sidebar group label) and emit the matching slugified URL as
        # explicit `slug:` frontmatter — one computation, no disk rename.
        label_dir = dir_label_path(relative_path.parent, label_map)
        slug_dir = dir_slug_path(relative_path.parent, label_map)
        out_parent = DOCS_ROOT / label_dir if label_dir else DOCS_ROOT
        md_out_path = out_parent / (stem + ".mdx")
        md_out_path.parent.mkdir(parents=True, exist_ok=True)

        if in_reference:
            slug_prefix = REFERENCE_PREFIX.strip("/")
            page_slug = f"{slug_prefix}/{slug_dir}/{stem}" if slug_dir else f"{slug_prefix}/{stem}"
        else:
            page_slug = None

        # Gather ideas for this file
        file_ideas = ideas_by_file.get(str(source_path), [])

        # First priority: file-level title from JSON
        if json_title:
            title = json_title
            author = "Unknown"
            status = "unknown"

        # Second priority: first idea in the file
        elif file_ideas:
            first_idea = file_ideas[0]
            title = first_idea.get("name", md_out_path.stem)
            author = first_idea.get("author", "Unknown")
            status = first_idea.get("status", "unknown")

        # Fallback: filename
        else:
            title = md_out_path.stem
            author = "Unknown"
            status = "unknown"

        # Snapshot the frontmatter author/status before the idea loop mutates
        # the ``author``/``status`` locals (they get reused for each idea Card).
        fm_author, fm_status = author, status

        combined_lines = []

        only_one = len(file_ideas) == 1
        page_badge = None

        # The file path + source/GitHub links come first, right under the page
        # title, ahead of the ideas and the API reference sections.
        combined_lines.append(page_source_header(data["file"]))

        for idea in file_ideas:
            md_text = idea["content_md"]
            mdx_title, md_body = extract_mdx_title(md_text)
            # Convert authored ```c blocks to linkified SourceBlocks first, so the
            # later inline-link transforms see an opaque component, not raw code.
            md_body = linkify_code_fences(md_body)
            md_body = link_functions_in_md(md_body, functions_map)
            md_body = link_files_in_md(md_body, files_map)
            md_body = link_bugs_in_md(md_body)
            md_body = link_commits_in_md(md_body)
            md_body = merge_changelog_and_notes(md_body)
            md_body = embed_idea_refs_in_md(md_body, idea, json_title_index, label_map)
            md_body = convert_blockquotes_to_asides(md_body)
            md_body = convert_h2_to_header_with_icon(md_body)

            idea_name = idea["name"]
            combined_lines.append(f"# {idea['size'].capitalize()} Idea: {idea_name}\n")
            metadata = idea.get("metadata", {})
            author = metadata.get("author", "Unknown")
            status = metadata.get("status", "unknown")

            status_upper = status.upper().strip()

            card_icon, card_color = STATUS_CARD_MAP.get(status_upper, ("star", "gray"))
            audience = metadata.get("audience", "General")
            author = metadata.get("author", "Unknown")

            badge_md = status_to_badge(status)
            variant = STATUS_BADGE_MAPPING.get(status, "tip")
            if only_one:
                page_badge = (status.capitalize(), variant)

            card_md = (
                f'<Card title="{idea_name}" icon="{card_icon}" color="{card_color}">\n'
                f"{badge_md}  \n"
                f"**Audience:** {audience}  \n"
                f"**Author:** {author}\n"
                f"</Card>\n"
            )

            combined_lines.append(card_md)
            combined_lines.append(md_body)

        def collect_markdown_lines(module: Module):
            lines = []

            # Constructs are grouped under a visible "## {Category}" section.
            # Each construct gets an *item* heading "### {kind} {name}" that the
            # rehype plugin (astro.config.mjs) rewrites to display just "{name}",
            # give it a deterministic "{kind}-{name}" id, and hide in the body —
            # so the right-hand TOC shows clean names nested under each section
            # while the body shows only the code blocks.
            def emit_section(title, items, item_kind, render_one):
                if not items:
                    return
                lines.append(f"## {title}\n")
                for it in items:
                    lines.append(f"### {item_kind(it)} {it.name}\n")
                    lines.append(render_one(it))
                    lines.append("\n")

            emit_section(
                "Structs",
                module.structs,
                lambda s: (s.kind or "struct").lower(),
                lambda s: format_struct_as_c_code(s, module.file),
            )
            emit_section(
                "Unions",
                module.unions,
                lambda s: "union",
                lambda s: format_struct_as_c_code(s, module.file),
            )
            emit_section(
                "Enums",
                module.enums,
                lambda e: "enum",
                lambda e: format_enum_as_c_code(e, module.file),
            )
            emit_section(
                "Type Aliases",
                module.typedefs,
                lambda t: "type alias",
                lambda t: format_typedef_fn_ptr(t, module.file),
            )
            emit_section(
                "Functions",
                module.functions,
                lambda f: "function",
                lambda f: format_function_signature(f, module.file),
            )
            emit_section(
                "Variables",
                module.variables,
                lambda g: "variable",
                lambda g: format_global_as_c_code(g, module.file),
            )

            return lines

        module = Module.from_json(data)
        file_md_lines = collect_markdown_lines(module)
        combined_lines.extend(file_md_lines)
        combined_lines = append_defines_to_md(combined_lines, module)

        # Write combined Markdown to single file
        body = "\n".join(combined_lines)
        text = assemble_page_text(title, fm_author, fm_status, page_badge, body, slug=page_slug)
        md_out_path.write_text(text, encoding="utf-8")
        print_single_line(
            "compiled JSON " + str(json_dir) + " → " + str(md_out_path), progress=i / total_files
        )


def format_function_signature(f: Function, file: str | None) -> str:
    """Render a function signature as a <SourceBlock>.

    Parameter/return types link to their reference pages; the function's own
    name links to its source definition (code browser).
    """
    name = f.name or "?"
    ret_type = (f.return_type or "void").strip()
    quals = f.qualifiers

    qual_prefix = (" ".join(quals) + " ") if quals else ""
    param_strs = []
    for p in f.parameters:
        p_type = (p.type or "").strip()
        p_name = p.name
        param_strs.append((p_type + " " + p_name).strip() if p_name else p_type)
    sig = qual_prefix + ret_type + " " + name + "(" + ", ".join(param_strs) + ");"

    return fence_or_sourceblock(sig, def_name=name, def_href=source_def_href(file, f.line))


def format_global_as_c_code(g: Variable, file: str | None) -> str:
    """Render a global/extern variable declaration as a <SourceBlock>.

    Its type links to the type's reference page; the variable name links to its
    source definition.
    """
    name = g.name or "?"
    raw = (g.raw_text or "").strip()
    if not raw:
        storage = (g.storage or "").strip()
        var_type = (g.type or "").strip()
        raw = " ".join(x for x in (storage, var_type, name) if x) + ";"

    return fence_or_sourceblock(raw, def_name=name, def_href=source_def_href(file, g.line))


def merge_changelog_and_notes(markdown: str) -> str:
    section_re = re.compile(r"(?:^|\n)##\s*(Changelog|Notes)\s*\n(.*?)(?=\n##\s|\Z)", re.DOTALL)

    sections = dict(section_re.findall(markdown))

    changelog = sections.get("Changelog")
    notes = sections.get("Notes")

    if changelog is None or notes is None:
        return markdown

    changelog = "\n".join([line.strip() for line in changelog.splitlines()])
    notes = notes.strip()

    changelog_block = f"```text\n{changelog}\n```"

    merged_section = (
        "\n<Tabs>\n"
        f'  <TabItem label="Changelog">\n\n{changelog_block}\n\n  </TabItem>\n'
        f'  <TabItem label="Notes">\n\n{notes}\n\n  </TabItem>\n'
        "</Tabs>\n"
    )

    markdown = section_re.sub("", markdown)

    markdown = markdown.rstrip() + "\n\n" + merged_section + "\n"

    return markdown


def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <json_dir>")
        sys.exit(1)

    json_dir = Path(sys.argv[1])
    if not json_dir.is_dir():
        print(f"Error: {json_dir} is not a directory")
        sys.exit(1)

    generate_docs(json_dir)


if __name__ == "__main__":
    main()
