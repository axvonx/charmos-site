#!/usr/bin/env python3
import json
import sys
import re, shutil
from pathlib import Path
from collections import defaultdict

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

LIGHTS = """<ul class=\"lightrope\">
  <li></li>
  <li></li>
  <li></li>
  <li></li>
  <li></li>
  <li></li>
  <li></li>
  <li></li>
  <li></li>
  <li></li>
  <li></li>
  <li></li>
  <li></li>
  <li></li>
  <li></li>
  <li></li>
  <li></li>
  <li></li>
  <li></li>
  <li></li>
  <li></li>
  <li></li>
  <li></li>
  <li></li>
  <li></li>
  <li></li>
  <li></li>
  <li></li>
  <li></li>
  <li></li>
  <li></li>
  <li></li>
  <li></li>
  <li></li>
</ul>"""

STATUS_CARD_MAP = {
    "STABLE":       ("approve-check-circle", "green"),
    "UNSTABLE":     ("warning", "yellow"),
    "LEGACY":       ("information", "purple"),
    "DEPRECATED":   ("error", "red"),
    "EXPERIMENTAL": ("warning", "yellow"),
}

HEADER_ICON_MAP = {
    "overview":      ("star", "goldenrod"),
    "background":    ("open-book", "brown"),
    "summary":       ("document", "gray"),
    "errors":        ("error", "red"),
    "context":       ("magnifier", "blue"),
    "constraints":   ("warning", "yellow"),
    "internals":     ("setting", "gray"),
    "strategy":      ("puzzle", "green"),
    "notes":         ("pencil", "white"),
    "changelog":     ("bars", "white"),
    "rationale":     ("rocket", "orange"),
    "api":     ("laptop", "blue"),
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
                    f'## {title} '
                    f'<span style="display:inline-block; vertical-align:middle; margin-left:0.25rem; position:relative">'
                    f'<Icon name="{icon_name}" color="{icon_color}" style="width:1.2em; height:1.2em;" />'
                    f'</span>\n'
                )
            else:
                line = f'## {title}\n'

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
        for l in aside_lines:
            if l.strip().startswith(">"):
                stripped.append(re.sub(r"^\s*>\s?", "", l))
            else:
                stripped.append(l)

        # Determine aside type
        first_line = stripped[0]
        parts = first_line.split()
        if parts and parts[0].lower() in ASIDE_MAP:
            aside_type = ASIDE_MAP[parts[0].lower()]
            stripped[0] = " ".join(parts[1:]) if len(parts) > 1 else ""
        else:
            aside_type = DEFAULT_ASIDE_TYPE

        aside_content = "\n".join(stripped).strip()

        aside_block = (
            f'<Aside type="{aside_type}">\n'
            f"{aside_content}\n"
            f"</Aside>"
        )
        result.append(aside_block)

        # If we ended on a blank line → preserve it
        if i < n and not lines[i].strip():
            result.append("")  # keep the blank line
            i += 1

        # If we ended because of an H1 header, do NOT consume it.
        # We want the next loop iteration to process it correctly.

    return "\n".join(result)


def print_single_line(*args, progress: float = None, **kwargs):
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
    type_str = re.sub(r'\bconst\b', '', type_str)
    type_str = type_str.replace('*', '')
    type_str = re.sub(r'\s+', ' ', type_str)
    type_str = type_str.replace('[]', '')
    return type_str.strip().lower()

def link_type(type_str: str, type_table: dict, always_tick) -> str:
    norm = normalize_type_name(type_str)
    entry = type_table.get(norm)
    if not entry:
        if always_tick:
            return f"`{type_str}`"
        else:
            return f" {type_str} " 

    url = generate_github_link_safe(entry["file"], entry["line"])
    return f"[`{type_str}`]({url})"



# Root path under which all generated reference pages live on the doc site.
REFERENCE_PREFIX = "/reference"

# Source tree root where dir_doc_name files live.
SOURCE_INCLUDE_ROOT = Path("charmos/include")


def _dir_name_to_slug(name: str) -> str:
    """
    Convert a human-readable dir_doc_name value to the URL slug that
    Astro/Starlight will use after the rename pass.
    Mirrors what rename_directories_from_namefiles() does to the filesystem:
    the directory is renamed to the literal string in the file, and Starlight
    then lowercases it and replaces spaces/special chars with hyphens.
    """
    import re as _re
    slug = name.strip().lower()
    slug = _re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug


def build_dir_rename_map(src_root: Path = SOURCE_INCLUDE_ROOT) -> dict:
    """
    Walk the source include tree and collect every dir_doc_name file.
    Returns a dict mapping each directory path (relative to src_root,
    as a tuple of path segments) to the slug that directory will have
    on the doc site after the rename pass.

    e.g.  ("sch",) -> "scheduling-and-multitasking"
    """
    rename_map = {}
    if not src_root.exists():
        return rename_map
    for name_file in src_root.rglob("dir_doc_name"):
        parent = name_file.parent
        new_name = name_file.read_text(encoding="utf-8").strip()
        if not new_name:
            continue
        try:
            rel = parent.relative_to(src_root)
        except ValueError:
            continue
        # Store mapping from each segment tuple to the renamed final segment
        # We only rename the *leaf* directory named by the file; ancestors
        # are resolved recursively when we build the full URL below.
        rename_map[rel.parts] = _dir_name_to_slug(new_name)
    return rename_map


def _apply_rename_map(rel_dir: Path, rename_map: dict) -> str:
    """
    Given a path like Path("sch/irq"), resolve each component through the
    rename_map and return the resulting URL segment string.

    rename_map keys are tuples of path segments relative to include root.
    We resolve greedily from the root downward so nested renames compose
    correctly.
    """
    parts = rel_dir.parts  # e.g. ("sch", "irq")
    result = []
    for i, part in enumerate(parts):
        prefix = tuple(parts[: i + 1])
        if prefix in rename_map:
            result.append(rename_map[prefix])
        else:
            result.append(part)
    return "/".join(result)


def build_type_doc_table(c_parse_map: dict, docs_root: Path,
                         src_root: Path = SOURCE_INCLUDE_ROOT) -> dict:
    """
    Build a mapping from normalised type keys to doc-site URLs.

    URLs are root-relative, under REFERENCE_PREFIX, with directory segments
    renamed according to any dir_doc_name files present in the source tree.

    e.g.  "struct rt_scheduler"
          -> /reference/scheduling-and-multitasking/rt_sched#struct-rt-scheduler
    """
    rename_map = build_dir_rename_map(src_root)
    doc_table  = {}

    for file_path, c_parse in c_parse_map.items():
        src_path = Path(file_path)
        try:
            relative_path = src_path.relative_to("charmos/include")
        except ValueError:
            relative_path = src_path

        mdx_stem = relative_path.stem          # e.g. "rt_sched"
        mdx_dir  = relative_path.parent        # e.g. Path("sch")

        # Apply directory renames, then prepend the reference prefix
        renamed_dir = _apply_rename_map(mdx_dir, rename_map)
        if renamed_dir:
            doc_base = f"{REFERENCE_PREFIX}/{renamed_dir}/{mdx_stem}/"
        else:
            doc_base = f"{REFERENCE_PREFIX}/{mdx_stem}/"

        types = c_parse.get("types", {})

        for s in types.get("structs", []):
            name = s.get("name")
            if not name:
                continue
            kind   = (s.get("kind") or "struct").lower()
            anchor = f"#{kind}-{name.lower()}"
            doc_table[f"struct {name}".lower()] = doc_base + anchor

        for e in types.get("enums", []):
            name = e.get("name")
            if not name:
                continue
            anchor = f"#enum-{name.lower()}"
            doc_table[f"enum {name}".lower()] = doc_base + anchor

        for t in types.get("typedefs", []):
            name = t.get("name")
            if not name:
                continue
            # Each typedef gets its own anchor based on its name so multiple
            # typedefs on one page link to the right one.
            # Starlight slugifies "type alias `name`" as "type-alias-name"
            # github-slugger keeps underscores, strips backticks, spaces -> hyphens
            anchor = f"#type-alias-{name.lower()}"
            doc_table[name.lower()] = doc_base + anchor

    return doc_table


def link_type_doc(type_str: str, type_table: dict, doc_table: dict, always_tick: bool) -> str:
    """
    Like link_type() but prefers the doc-site URL from doc_table over the
    GitHub source link.  Falls back to the GitHub link if the type is in
    type_table but not doc_table, and to a plain/ticked string if unknown.
    """
    norm  = normalize_type_name(type_str)
    doc_url = doc_table.get(norm)
    if doc_url:
        return f"[`{type_str}`]({doc_url})"
    # fall back to GitHub link if we know the type but have no doc page
    entry = type_table.get(norm)
    if entry:
        url = generate_github_link_safe(entry["file"], entry["line"])
        return f"[`{type_str}`]({url})"
    if always_tick:
        return f"`{type_str}`"
    return f" {type_str} "


def build_type_table(c_parse_map: dict, ignored_types=None):
    if ignored_types is None:
        ignored_types = set()

    type_table = {}

    for file_path, c_parse in c_parse_map.items():
        types = c_parse.get("types", {})

        # Structs
        for s in types.get("structs", []):
            name = s.get("name")
            if not name or name in ignored_types:
                continue
            full_name = f"struct {name}"
            type_table[full_name.lower()] = {
                "name": name,
                "full_name": full_name,
                "file": file_path,
                "line": s.get("line"),
                "start_byte": s.get("start_byte"),
                "end_byte": s.get("end_byte"),
                "kind": "struct"
            }

        # Enums
        for e in types.get("enums", []):
            name = e.get("name")
            if not name or name in ignored_types:
                continue
            full_name = f"enum {name}"
            type_table[full_name.lower()] = {
                "name": name,
                "full_name": full_name,
                "file": file_path,
                "line": e.get("line"),
                "start_byte": e.get("start_byte"),
                "end_byte": e.get("end_byte"),
                "kind": "enum"
            }

        # Typedefs
        for t in types.get("typedefs", []):
            name = t.get("name")
            if not name or name in ignored_types:
                continue
            full_name = name
            type_table[full_name.lower()] = {
                "name": name,
                "full_name": full_name,
                "file": file_path,
                "line": t.get("line"),
                "start_byte": t.get("start_byte"),
                "end_byte": t.get("end_byte"),
                "kind": "typedef",
                "type_str": t.get("type"),
                "fn_ptr": t.get("fn_ptr"),   # needed for signature-based fn-ptr matching
            }

    return type_table


def generate_github_link_safe(file_path: str, line: int = None) -> str:
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
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            ideas = data.get("ideas", [])
            all_ideas.extend(ideas)
            c_parse_map[data.get("file")] = data.get("c_parse", {})
    return all_ideas, c_parse_map

def link_functions_in_md(md_text: str, functions_map: dict):
    FUNC_RE = re.compile(r'`([a-zA-Z_][a-zA-Z0-9_]*)\(\)`')
    def replacer(match):
        fn = match.group(1)
        url = functions_map.get(fn)
        if url:
            return f"[`{fn}()`]({url})"
        return match.group(0)
    return FUNC_RE.sub(replacer, md_text)

def link_files_in_md(md_text: str, files_map: dict):
    FILE_RE = re.compile(r'`([\w./-]+\.(c|h|rs|cpp|txt|md))`')
    def replacer(match):
        file = match.group(1)
        url = files_map.get(file)
        if url:
            return f"[`{file}`]({url})"
        return match.group(0)
    return FILE_RE.sub(replacer, md_text)

def link_commits_in_md(md_text: str):
    COMMIT_RE = re.compile(r'commit\s+([0-9a-f]{7,40})', re.IGNORECASE)
    def replacer(match):
        h = match.group(1)
        url = f"https://github.com/axvonx/charmos/commit/{h}"
        return f"[commit {h}]({url})"
    return COMMIT_RE.sub(replacer, md_text)

def link_bugs_in_md(md_text: str):
    BUG_RE = re.compile(r'#(\d+)')
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

        m = re.match(r'^#\s*(Big|Small|Huge)\s+Idea\s*:\s*(.+)$', line, re.IGNORECASE)
        if m:
            idea_type, idea_name = m.groups()
            idx += 1
            continue

        m2 = re.match(r'^#\s*(Big|Small|Huge)\s+Idea\s*$', line, re.IGNORECASE)
        if m2 and idx + 1 < len(lines):
            idea_type = m2.group(1)
            next_line = lines[idx + 1].strip()
            if next_line:
                idea_name = next_line
                idx += 2
                continue

        if re.match(r'^##\s*Credits\s*$', line, re.IGNORECASE) and idx + 1 < len(lines):
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
        with open(jf, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        title = data.get("title")
        src_file = data.get("file")

        if title and src_file:
            title_lower = title.strip().lower()
            index[title_lower] = src_file

    return index

def embed_idea_refs_in_md(md_text: str, idea, idea_doc_paths, json_title_index=None):
    refs = idea.get("references", {}).get("idea_refs", [])
    if not refs:
        return md_text


    for ref in refs:

        ref_string = ref["string"]

        ref_lower = ref_string.lower()


        target_path = None
        for name, path in idea_doc_paths.items():
            if ref_lower in name:
                target_path = path
                break

        if not target_path and json_title_index:
            if ref_lower in json_title_index:
                json_src = json_title_index[ref_lower]
                link_url = generate_github_link_safe(json_src)

                link_md = f'[{ref_string}]({link_url})'


                pattern = re.escape(ref_string)
                md_text = re.sub(pattern, link_md, md_text)
                continue

        if not target_path:
            continue


        link_url = generate_github_link_safe(target_path)

        link_md = f'[{ref_string}]({link_url})'

        pattern = re.escape(ref_string)
        md_text = re.sub(pattern, link_md, md_text)

    return md_text


def build_global_function_table(c_parse_map: dict):
    func_table = {}
    for file_path, c_parse in c_parse_map.items():
        for f in c_parse.get("functions", []):
            name = f.get("name")
            if not name:
                continue
            if name not in func_table:
                url = generate_github_link_safe(file_path, f.get("line"))
                func_table[name] = url
    return func_table


def append_defines_to_md(md_lines, json_data):
    defines = json_data.get("c_parse", {}).get("defines", [])
    if not defines:
        return md_lines

    md_lines.append("\n### Defines\n")
    file_path = json_data.get("file")

    for d in defines:
        name      = d.get("name") or ""
        params    = d.get("params")     # None for object-like, str like "(a,b)" for fn-like
        value     = d.get("value") or ""
        raw_text  = d.get("raw_text") or ""
        multiline = d.get("multiline", False)
        line      = d.get("line")
        url       = generate_github_link_safe(file_path, line)

        # Heading: #### `NAME` or #### `NAME(params)`
        sig = (name + params) if params is not None else name
        md_lines.append("#### [" + "`" + sig + "`" + "](" + url + ")")
        md_lines.append("")

        if multiline:
            # Multi-line macro — show the full raw definition in a fenced block
            md_lines.append("```c")
            md_lines.append(raw_text)
            md_lines.append("```")
            md_lines.append("")
        elif value:
            # Single-line with a value — inline code
            md_lines.append("`" + value + "`")
            md_lines.append("")
        # Bare sentinel define (no value) — heading alone is sufficient

    md_lines.append("\n---\n")
    return md_lines

def status_to_badge(status: str) -> str:
    status = status.upper().strip()
    variant = STATUS_BADGE_MAPPING.get(status, "tip")  # default to tip
    return f'<Badge text="{status.capitalize()}" variant="{variant}" />'


def _render_struct_body(members: list, indent: int, col_width: int) -> list:
    """
    Recursively render struct/union members as plain C code lines.
    Nested anonymous composites are rendered inline with increased indentation.
    """
    pad = " " * indent
    lines = []
    for m in members:
        m_type = (m.get("type") or "").strip()
        m_name = (m.get("name") or "").strip().replace("\n", "")
        m_offset = m.get("offset")
        nested = m.get("nested")
        offset_comment = f"  //0x{m_offset:x}" if m_offset is not None else ""

        if nested:
            nested_kind = nested.get("kind", "struct")
            inner_members = nested.get("members", [])
            inner_col = min(
                max((len((im.get("type") or "").strip()) for im in inner_members), default=8) + 2,
                40,
            )
            lines.append(f"{pad}{nested_kind} {{")
            lines.extend(_render_struct_body(inner_members, indent + 4, inner_col))
            closing_name = f" {m_name}" if m_name else ""
            lines.append(f"{pad}}}{closing_name};{offset_comment}")
        else:
            type_padding = " " * max(col_width - len(m_type), 1)
            lines.append(f"{pad}{m_type}{type_padding}{m_name};{offset_comment}")

    return lines


# Matches the fn-ptr declarator wrapper the C parser emits for struct members:
#   (*member_name)(params...)
# Capture group 1 = bare member name, group 2 = raw param list (may be empty).
_FN_PTR_MEMBER_RE = re.compile(
    r'^\(\*\s*[A-Za-z_][A-Za-z0-9_]*\s*\)'   # (*name)
    r'\s*\(([^)]*)\)'                             # (params) — group 1
)


def _extract_fn_ptr_signature(m_type: str, m_name: str):
    """
    If m_name looks like a fn-ptr declarator (*foo)(...), extract a
    normalised (return_type, (param_type, ...)) signature tuple.
    Returns None if m_name is not a fn-ptr declarator.
    """
    match = _FN_PTR_MEMBER_RE.match(m_name.strip())
    if not match:
        return None
    ret_norm = normalize_type_name(m_type)
    params_raw = match.group(1)
    param_norms = []
    for p in params_raw.split(','):
        p = p.strip()
        if not p or p == 'void':
            continue
        # Strip trailing parameter name (last plain identifier) to leave type
        p_type = re.sub(r'\b([A-Za-z_][A-Za-z0-9_]*)\s*$', '', p).strip() or p
        param_norms.append(normalize_type_name(p_type))
    return (ret_norm, tuple(param_norms))


def _build_fn_sig_index(type_table: dict) -> dict:
    """
    Build a (ret_norm, (param_norms...)) -> typedef_key index from every
    typedef entry in type_table that has fn_ptr metadata.
    Used by strategy 3 of _resolve_member_typedef.
    """
    idx = {}
    for key, entry in type_table.items():
        fp = entry.get("type_str")  # plain typedef — no sig
        fn_ptr = entry.get("fn_ptr") if isinstance(entry, dict) else None
        # fn_ptr info is stored on the type_table entry if present
        if not isinstance(entry, dict):
            continue
        fn_ptr = entry.get("fn_ptr")
        if not fn_ptr:
            continue
        ret = normalize_type_name(fn_ptr.get("return_type") or "void")
        params = tuple(
            normalize_type_name((p.get("type") or "").strip())
            for p in (fn_ptr.get("parameters") or [])
            if (p.get("type") or "").strip() not in ("", "void")
        )
        idx[(ret, params)] = key
    return idx


# Module-level cache so we only build the sig index once per type_table object.
_fn_sig_index_cache: tuple = (None, None)   # (type_table_id, index)


def _get_fn_sig_index(type_table: dict) -> dict:
    global _fn_sig_index_cache
    tid = id(type_table)
    if _fn_sig_index_cache[0] != tid:
        _fn_sig_index_cache = (tid, _build_fn_sig_index(type_table))
    return _fn_sig_index_cache[1]


def _resolve_member_typedef(m_type: str, m_name: str, type_table: dict) -> tuple:
    """
    Try to resolve a struct member to a typedef entry in type_table.

    Strategy 1 — direct type name:
        normalise m_type and look it up.  Handles  `my_fn_t handler;`

    Strategy 2 — name match:
        if m_name is (*typedef_name) or (*typedef_name)(...), extract the
        bare name and look it up as a typedef.  Handles the less-common case
        where the member name happens to equal the typedef name.

    Strategy 3 — signature match:
        parse the fn-ptr signature from m_type + m_name and compare against
        every typedef's fn_ptr metadata.  This is the reliable path for
        `void (*on_tick)(struct foo *);` where "on_tick" is an arbitrary
        member name unrelated to the typedef name "on_tick_fn".

    Returns (norm_key, entry) if found, else (None, None).
    """
    # Strategy 1 — normalised type string
    norm = normalize_type_name(m_type)
    entry = type_table.get(norm)
    if entry:
        return norm, entry

    # Strategies 2 & 3 only apply when m_name looks like (*foo) or (*foo)(...)
    fn_ptr_match = re.match(r'^\(\*\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)', m_name.strip())
    if fn_ptr_match:
        # Strategy 2 — name == typedef key
        candidate = fn_ptr_match.group(1).lower()
        entry = type_table.get(candidate)
        if entry and entry.get("kind") == "typedef":
            return candidate, entry

        # Strategy 3 — signature match
        sig = _extract_fn_ptr_signature(m_type, m_name)
        if sig is not None:
            sig_index = _get_fn_sig_index(type_table)
            key = sig_index.get(sig)
            if key:
                return key, type_table[key]

    return None, None


def _collect_referenced_types(members: list, type_table: dict, doc_table: dict, file_path: str, seen: set) -> list:
    """
    Walk members recursively and collect unique external types that resolve
    in the type_table.  Returns [(display_str, url), ...] in encounter order.
    URLs point to the doc page (doc_table) when available, otherwise GitHub.

    Function pointer members are matched against typedef signatures so that
    `void (*on_tick)(struct foo *)` correctly links to `on_tick_fn` even
    when the member name bears no relation to the typedef name.
    """
    results = []
    for m in members:
        nested = m.get("nested")
        if nested:
            results.extend(_collect_referenced_types(
                nested.get("members", []), type_table, doc_table, file_path, seen))
            continue

        m_type = (m.get("type") or "").strip()
        m_name = (m.get("name") or "").strip()

        norm, type_entry = _resolve_member_typedef(m_type, m_name, type_table)
        if not norm or not type_entry:
            continue
        if norm in seen:
            continue

        seen.add(norm)
        url = doc_table.get(norm) or generate_github_link_safe(type_entry["file"], type_entry["line"])
        display = type_entry["full_name"]
        results.append((display, url))

    return results


def format_struct_as_c_code(data: dict, s: dict, type_table: dict, doc_table: dict = None) -> str:
    """
    Render a struct/union as a fenced ```c code block (Astro-safe) followed
    by a compact "referenced types" link list of unique external types only.
    Nested anonymous composites are inlined in the code block.
    """
    name = s.get("name", "?")
    kind = s.get("kind") or "struct"
    size = s.get("size")
    members = s.get("members", [])
    struct_line = s.get("line")
    file_path = data.get("file")

    struct_url = generate_github_link_safe(file_path, struct_line)

    size_comment = f"//0x{size:x} bytes  " if size is not None else ""

    top_level_types = [m for m in members if not m.get("nested")]
    col_width = 16
    if top_level_types:
        col_width = min(
            max(len((m.get("type") or "").strip()) for m in top_level_types) + 2, 40
        )

    code_lines = [f"{size_comment}{kind} {name} {{"]
    code_lines.extend(_render_struct_body(members, indent=4, col_width=col_width))
    code_lines.append("};")

    code_block = "```c\n" + "\n".join(code_lines) + "\n```"

    seen: set = set()
    refs = _collect_referenced_types(members, type_table, doc_table or {}, file_path, seen)

    if refs:
        struct_link = f"[`{name}`]({struct_url})"
        link_lines = [f"**{kind} {struct_link}** referenced types:"]
        for type_str, type_url in refs:
            link_lines.append(f"- [`{type_str}`]({type_url})")
        return code_block + "\n\n" + "\n".join(link_lines)

    return code_block


def format_enum_as_c_code(data: dict, e: dict, type_table: dict) -> str:
    """
    Render an enum as a fenced ```c code block.
    """
    name = e.get("name", "?")
    members = e.get("members", [])

    code_lines = [f"enum {name} {{"]
    for m in members:
        m_name = (m.get("name") or "").strip()
        m_value = m.get("value")
        value_str = f" = {m_value}" if m_value is not None else ""
        code_lines.append(f"    {m_name}{value_str},")
    code_lines.append("};")

    return "```c\n" + "\n".join(code_lines) + "\n```"



def format_typedef_fn_ptr_raw(data: dict, t: dict, type_table: dict, doc_table: dict = None) -> str:
    """
    Build the raw pre-retick string for a typedef, mirroring the exact same
    strategy as format_function_signature_raw.
    Param/return types resolve to doc-site URLs via doc_table when available.
    The alias name itself always links to the GitHub source definition.
    """
    _doc = doc_table or {}
    fn_ptr = t.get("fn_ptr")
    t_url = generate_github_link_safe(data["file"], t.get("line"))
    alias_name = t.get("name") or "?"

    if not fn_ptr:
        type_raw = link_type_doc(t.get("type") or "", type_table, _doc, False).strip()
        return f"[`{alias_name}`]({t_url}) : {type_raw}"

    ret_type = fn_ptr.get("return_type") or "void"
    ret_raw = link_type_doc(ret_type, type_table, _doc, False)

    parts = [ret_raw, f"[`{alias_name}`]({t_url})", "("]

    param_strs = []
    for p in fn_ptr.get("parameters") or []:
        p_type = (p.get("type") or "").strip()
        p_name = p.get("name")
        type_raw = link_type_doc(p_type, type_table, _doc, False).strip()
        if p_name:
            param_strs.append(f"{type_raw} {p_name}")
        else:
            param_strs.append(type_raw)
    parts.append(",".join(param_strs))
    parts.append(")")

    return "".join(parts)


def format_typedef_fn_ptr(data: dict, t: dict, type_table: dict, doc_table: dict = None) -> str:
    """
    Render a typedef as a fenced ```c code block followed by a referenced
    types section, consistent with how structs/enums are rendered.
    """
    _doc = doc_table or {}
    fn_ptr   = t.get("fn_ptr")
    alias    = t.get("name") or "?"
    t_url    = generate_github_link_safe(data["file"], t.get("line"))
    file_path = data.get("file")

    if not fn_ptr:
        # Plain typedef — one-liner
        raw_type = (t.get("type") or "").strip()
        code_block = "```c\ntypedef " + raw_type + " " + alias + ";\n```"
        # Referenced type
        norm = normalize_type_name(raw_type)
        entry = type_table.get(norm)
        if entry:
            url = _doc.get(norm) or generate_github_link_safe(entry["file"], entry["line"])
            ref_lines = [f"**type alias [`{alias}`]({t_url})** referenced types:"]
            ref_lines.append(f"- [`{raw_type}`]({url})")
            return code_block + "\n\n" + "\n".join(ref_lines)
        return code_block

    # Function-pointer typedef — build signature line
    ret_type = (fn_ptr.get("return_type") or "void").strip()
    params = fn_ptr.get("parameters") or []
    param_strs = []
    for p in params:
        p_type = (p.get("type") or "").strip()
        p_name = p.get("name")
        param_strs.append((p_type + " " + p_name).strip() if p_name else p_type)
    sig = ret_type + " (*" + alias + ")(" + ", ".join(param_strs) + ");"
    code_block = "```c\ntypedef " + sig + "\n```"

    # Collect referenced types from return + params
    seen = set()
    ref_results = []
    for type_str in [ret_type] + [p.get("type") or "" for p in params]:
        type_str = type_str.strip()
        norm = normalize_type_name(type_str)
        if not norm or norm in seen:
            continue
        entry = type_table.get(norm)
        if entry:
            seen.add(norm)
            url = _doc.get(norm) or generate_github_link_safe(entry["file"], entry["line"])
            ref_results.append((entry["full_name"], url))

    if ref_results:
        ref_lines = [f"**type alias [`{alias}`]({t_url})** referenced types:"]
        for display, url in ref_results:
            ref_lines.append(f"- [`{display}`]({url})")
        return code_block + "\n\n" + "\n".join(ref_lines)
    return code_block


def generate_docs(json_dir: Path):
    ideas, c_parse_map = load_json_dir(json_dir)
    type_table = build_type_table(c_parse_map)
    doc_table  = build_type_doc_table(c_parse_map, DOCS_ROOT)

    # Step 1: Group ideas by their source file

    functions_map = {}
    files_map = {}
    idea_doc_paths = {}
    ideas_by_file = defaultdict(list)
    collision_counter = defaultdict(int)
    json_files = list(json_dir.glob("*.json")) 
    total_files = len(json_files)
    json_title_index = build_json_title_index(json_dir)

    for idea in ideas:
        src_file = idea["path"]
        ideas_by_file[src_file].append(idea)
    
    functions_map = build_global_function_table(c_parse_map)

    # Second pass: build function/file links
    for idea in ideas:
        for f in idea.get("references", {}).get("files", []):
            files_map[f["name"]] = generate_github_link_safe(f["name"])

    # Step 2: For each JSON file, write the Markdown with ideas on top
    for i, json_file in enumerate(json_dir.glob("*.json"), start = 1):
        with open(json_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    
        json_title = data.get("title")

        source_path = Path(data["file"])
        try:
            relative_path = source_path.relative_to("charmos/include")
        except ValueError:
            relative_path = source_path
    
        md_out_path = DOCS_ROOT / relative_path.parent / (relative_path.stem + ".mdx")
        md_out_path.parent.mkdir(parents=True, exist_ok=True)
    
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
        
                        
        front_matter_lines = [
            "---\n",
            f'title: "{title}"\n',
            f'author: "{author}"\n', 
            f'status: "{status}"\n',
            "---\n\n"
        ]

        front_matter = "".join(front_matter_lines)

        combined_lines = []

        only_one = len(file_ideas) == 1
        status_added = False
    
        for idea in file_ideas:
            md_text = idea["content_md"]
            mdx_title, md_body = extract_mdx_title(md_text)
            md_body = link_functions_in_md(md_body, functions_map)
            md_body = link_files_in_md(md_body, files_map)
            md_body = link_bugs_in_md(md_body)
            md_body = link_commits_in_md(md_body)
            md_body = merge_changelog_and_notes(md_body)
            md_body = embed_idea_refs_in_md(md_body, idea, idea_doc_paths, json_title_index)
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
            if (only_one):
                front_matter = insert_string_at_line(front_matter, 
                                        "sidebar:\n  badge:\n    text: " + status.capitalize() + "\n    variant: "
                                                           + variant + "\n", 5)
                status_added = True
            
            card_md = (
                f'<Card title="{idea_name}" icon="{card_icon}" color="{card_color}">\n'
                f"{badge_md}  \n"
                f"**Audience:** {audience}  \n"
                f"**Author:** {author}\n"
                f"</Card>\n"
            )
            
            combined_lines.append(card_md)
            combined_lines.append(md_body)
                     
        def collect_markdown_lines(json_path, type_table, doc_table):
            data = json.loads(open(json_path, encoding="utf-8").read())
            lines = []
    
            source_path = Path(data["file"])
            file_url = generate_github_link_safe(data["file"])
            lines.append(f"# [{source_path.as_posix()[8:]}]({file_url})\n")
    
            # Structs — rendered as C-style monospaced blocks with inline links
            for s in data["c_parse"]["types"].get("structs", []):
                if not s.get("name") or s["name"].lower() == "none":
                    continue

                kind = s.get("kind") or "struct"
                s_url = generate_github_link_safe(data["file"], s.get("line"))
                lines.append(f"### {kind} [`{s['name']}`]({s_url})\n")
                lines.append(format_struct_as_c_code(data, s, type_table, doc_table))
                lines.append("\n")
    
            # Enums — rendered as C-style monospaced blocks with inline links
            for e in data["c_parse"]["types"].get("enums", []):
                if not e.get("name") or e["name"].lower() == "none":
                    continue

                e_url = generate_github_link_safe(data["file"], e.get("line"))
                lines.append(f"### enum [`{e['name']}`]({e_url})\n")
                lines.append(format_enum_as_c_code(data, e, type_table))
                lines.append("\n")
    
            # Typedefs
            for t in data["c_parse"]["types"].get("typedefs", []):
                if not t.get("name"):
                    continue
                t_name = t["name"]
                t_url  = generate_github_link_safe(data["file"], t.get("line"))
                rendered = format_typedef_fn_ptr(data, t, type_table, doc_table)
                lines.append(f"### type alias [`{t_name}`]({t_url})\n")
                lines.append(rendered)
                lines.append("\n")

            # Functions
            for f in data["c_parse"].get("functions", []):
                if not f.get("name"):
                    continue
                f_url = generate_github_link_safe(data["file"], f.get("line"))
                rendered = format_function_signature(data, f, type_table, doc_table)
                lines.append(f"### [`{f['name']}`]({f_url})\n")
                lines.append(rendered)
                lines.append("\n")
    
            return lines
    
        file_md_lines = collect_markdown_lines(json_file, type_table, doc_table)
        combined_lines.extend(file_md_lines)
        combined_lines = append_defines_to_md(combined_lines, data)
        combined_lines = append_globals_to_md(combined_lines, data, type_table, doc_table)
    
        # Write combined Markdown to single file
        text = front_matter + "\n".join(combined_lines)
        line = 7
        if status_added:
            line = 11

        text = insert_string_at_line(text, LIGHTS, line)
        text = insert_string_at_line(text, "import { Badge } from '@astrojs/starlight/components';\n", line)
        text = insert_string_at_line(text, "import { Card } from '@astrojs/starlight/components';\n", line)
        text = insert_string_at_line(text, "import { Aside } from '@astrojs/starlight/components';\n", line)
        text = insert_string_at_line(text, "import { Icon } from '@astrojs/starlight/components';\n", line)
        text = insert_string_at_line(text, "import { Tabs, TabItem } from '@astrojs/starlight/components';\n", line)
        md_out_path.write_text(text, encoding="utf-8")
        print_single_line("compiled JSON " + str(json_dir) + " → " + str(md_out_path), progress = i / total_files)

def insert_string_at_line(original_string, new_string, line_n):
    lines = original_string.splitlines()

    insert_index = max(0, min(line_n - 1, len(lines)))

    lines.insert(insert_index, new_string)

    return '\n'.join(lines)

def format_function_signature_raw(data, f, type_table, doc_table=None):
    _doc = doc_table or {}
    qualifiers = " ".join(f.get("qualifiers", []))
    return_type = f.get("return_type") or "void"

    parts = []
    if qualifiers:
        parts.append(qualifiers)
    
    ret_type_full = link_type_doc(return_type, type_table, _doc, False)
    if not qualifiers:
        ret_type_full = ret_type_full.lstrip()

    parts.append(ret_type_full)

    f_url = generate_github_link_safe(data["file"], f.get("line"))
    parts.append(f"[`{f['name']}`]({f_url})")

    parts.append("(")

    param_strs = []
    for p in f.get("parameters", []):
        type_md = link_type_doc(p['type'], type_table, _doc, False).strip()
        if p.get("name"):
            param_strs.append(f"{type_md} {p['name']}")
        else:
            param_strs.append(type_md)
    parts.append(",".join(param_strs))
    parts.append(")")

    return "".join(parts)

# Matches any markdown link starting at the current position:
#   [display text](url)
# The display text may contain backticks, parens, asterisks, etc.
# We match a balanced [...] then (...) — good enough for our generated URLs
# which never contain bare ')' inside the URL portion.
_LINK_RE = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')


def retick_segmentwise(s: str) -> str:
    out = []
    i = 0
    n = len(s)

    out.append("`")  # open the first tick span

    while i < n:
        c = s[i]

        # Rule A: hit the start of a markdown link [display](url)
        # Detect via regex so we catch links whose display text starts with
        # anything — backtick, paren, letter, etc.
        if c == "[":
            m = _LINK_RE.match(s, i)
            if m:
                # close the current tick span, emit the link verbatim, reopen
                out.append("`")
                out.append(m.group(0))
                out.append("`")
                i = m.end()
                continue
            # Not a valid link — treat as normal character
            out.append(c)
            i += 1
            continue

        # Rule B: hit a comma — split tick spans around it
        if c == ",":
            out.append("`")
            out.append(",")
            out.append("`")
            i += 1
            continue

        # Default: normal character inside the current tick span
        out.append(c)
        i += 1

    # close final tick span
    out.append("`")
    return "".join(out)


def clean_string(input_string):
    cleaned_string = ' '.join(line.lstrip() for line in input_string.splitlines())
    return cleaned_string

def format_function_signature(data, f, type_table, doc_table=None):
    """
    Render a function as a fenced ```c code block followed by a referenced
    types section, consistent with structs/enums/typedefs.
    """
    _doc = doc_table or {}
    name      = f.get("name") or "?"
    ret_type  = (f.get("return_type") or "void").strip()
    params    = f.get("parameters") or []
    quals     = f.get("qualifiers") or []
    f_url     = generate_github_link_safe(data["file"], f.get("line"))
    file_path = data.get("file")

    qual_prefix = (" ".join(quals) + " ") if quals else ""
    param_strs = []
    for p in params:
        p_type = (p.get("type") or "").strip()
        p_name = p.get("name")
        param_strs.append((p_type + " " + p_name).strip() if p_name else p_type)
    sig = qual_prefix + ret_type + " " + name + "(" + ", ".join(param_strs) + ");"
    code_block = "```c\n" + sig + "\n```"

    # Collect referenced types from return type + all param types
    seen = set()
    ref_results = []
    for type_str in [ret_type] + [p.get("type") or "" for p in params]:
        type_str = type_str.strip()
        norm = normalize_type_name(type_str)
        if not norm or norm in seen:
            continue
        entry = type_table.get(norm)
        if entry:
            seen.add(norm)
            url = _doc.get(norm) or generate_github_link_safe(entry["file"], entry["line"])
            ref_results.append((entry["full_name"], url))

    if ref_results:
        ref_lines = [f"**[`{name}`]({f_url})** referenced types:"]
        for display, url in ref_results:
            ref_lines.append(f"- [`{display}`]({url})")
        return code_block + "\n\n" + "\n".join(ref_lines)
    return code_block

def merge_changelog_and_notes(markdown: str) -> str:
    section_re = re.compile(
        r"(?:^|\n)##\s*(Changelog|Notes)\s*\n(.*?)(?=\n##\s|\Z)",
        re.DOTALL
    )

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


def append_globals_to_md(md_lines, json_data, type_table, doc_table=None):
    globals_list = json_data.get("c_parse", {}).get("globals", [])
    if not globals_list:
        return md_lines

    md_lines.append("\n### Global Variables\n")

    for g in globals_list:
        var_name = g.get("name")
        var_type = g.get("type") or "unknown"
        init_val = g.get("initializer")

        line = g.get("line")
        file_path = json_data.get("file")
        url = generate_github_link_safe(file_path, line)

        name_md = f"[`{var_name}`]({url})"

        type_md = link_type_doc(var_type, type_table, doc_table, True)
        type_md = clean_string(type_md)

        init_md = f" = `{init_val}`" if init_val is not None else ""

        md_lines.append(f"- {type_md} {name_md}{init_md}")

    md_lines.append("\n---\n")

    return md_lines

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
