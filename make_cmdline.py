#!/usr/bin/env python3
"""Generate the kernel command-line options guide from charmos source.

Scans the source tree for ``CMDLINE_ENTRY_DECLARE(...)`` sites, parses the
designated initialisers, and emits a Starlight MDX guide containing a table of
every boot parameter the kernel accepts.

The declarations look like::

    CMDLINE_ENTRY_DECLARE(name,
        .desc = "...", .arg = "...", .default_val = "...",
        .required = true, .callback = fn, .value = &x);

Only ``.desc`` / ``.arg`` / ``.default_val`` / ``.required`` feed the docs;
everything else (callbacks, value pointers) is opaque to documentation and is
ignored. This is a source-level extractor — no build or boot required — which
is why it lives in the docs pipeline rather than the kernel: the kernel ships
the same data at runtime via ``help`` on the command line.

Usage::

    make_cmdline.py <source_root> [-o OUT.mdx]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

MACRO = "CMDLINE_ENTRY_DECLARE"
SOURCE_REPO_URL = "https://github.com/axvonx/charmos/blob/main"


# ── Parsing ───────────────────────────────────────────────────────────────────


def find_decls(text: str):
    """Yield ``(args, offset)`` for each ``CMDLINE_ENTRY_DECLARE(...)`` call.

    ``args`` is the raw text between the parentheses; ``offset`` is the byte
    index of the macro name (used to derive a line number). The macro's own
    ``#define`` is skipped.
    """
    i = 0
    while True:
        m = re.search(rf"\b{MACRO}\s*\(", text[i:])
        if not m:
            return
        abs_start = i + m.start()
        line_begin = text.rfind("\n", 0, abs_start) + 1
        if re.match(r"\s*#\s*define\b", text[line_begin:abs_start]):
            i = i + m.end()
            continue

        start = i + m.end()  # just past the '('
        depth, j = 1, start
        while j < len(text) and depth:
            if text[j] == "(":
                depth += 1
            elif text[j] == ")":
                depth -= 1
            j += 1
        yield text[start : j - 1], abs_start
        i = j


def split_top_level(args: str):
    """Split ``args`` on top-level commas, ignoring nested (){}[] and strings."""
    parts, depth, buf = [], 0, []
    in_str = quote = None
    esc = False
    for ch in args:
        if in_str:
            buf.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                in_str = None
            continue
        if ch in "\"'":
            in_str = quote = ch
            buf.append(ch)
        elif ch in "([{":
            depth += 1
            buf.append(ch)
        elif ch in ")]}":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return [p.strip() for p in parts]


def unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1]
    return s


def parse_entry(args: str) -> dict:
    """Parse the argument text of one macro call into a doc entry dict."""
    parts = split_top_level(args)
    entry = {
        "name": parts[0].strip() if parts else "",
        "desc": None,
        "arg": None,
        "default_val": None,
        "required": False,
    }
    for p in parts[1:]:
        m = re.match(r"\.(\w+)\s*=\s*(.*)", p, re.S)
        if not m:
            continue
        field, val = m.group(1), m.group(2).strip()
        if field in ("desc", "arg"):
            entry[field] = unquote(val)
        elif field == "default_val":
            entry["default_val"] = None if val == "NULL" else unquote(val)
        elif field == "required":
            entry["required"] = val == "true"
    return entry


def collect(root: Path) -> list[dict]:
    """Collect every cmdline entry declared under ``root`` (``*.c`` / ``*.h``)."""
    entries: list[dict] = []
    for path in sorted(root.rglob("*.[ch]")):
        if not path.is_file():
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        if MACRO not in text:
            continue
        rel = path.relative_to(root).as_posix()
        for args, offset in find_decls(text):
            e = parse_entry(args)
            e["src_file"] = rel
            e["src_line"] = text.count("\n", 0, offset) + 1
            entries.append(e)
    return entries


# ── Rendering ─────────────────────────────────────────────────────────────────


def _cell(text: str) -> str:
    """Escape a value for use inside a Markdown table cell."""
    return text.replace("|", "\\|").replace("\n", " ")


def render_row(e: dict) -> str:
    url = f"{SOURCE_REPO_URL}/{e['src_file']}#L{e['src_line']}"
    name = f"[`{e['name']}`]({url})"
    value = f"`{e['arg']}`" if e.get("arg") else "—"
    dv = e.get("default_val")
    default = f"`{dv}`" if dv else "—"
    required = "Yes" if e.get("required") else "No"
    desc = _cell(e["desc"]) if e.get("desc") else "_(undocumented)_"
    return f"| {name} | {value} | {default} | {required} | {desc} |"


def render_mdx(entries: list[dict]) -> str:
    entries = sorted(entries, key=lambda e: e["name"])
    lines = [
        "---",
        'title: "Command-Line Options"',
        'description: "Boot-time kernel parameters parsed from the bootloader ' 'command line."',
        "---",
        "",
        "## Options",
        "TODO",
    ]
    if entries:
        lines += [
            "| Option | Value | Default | Required | Description |",
            "| --- | --- | --- | --- | --- |",
        ]
        lines += [render_row(e) for e in entries]
    else:
        lines.append("_No command-line options were found in the source tree._")
    lines += [
        "",
        "## Listing options at runtime",
        "TODO",
    ]
    return "\n".join(lines)


# ── Entry point ───────────────────────────────────────────────────────────────


def generate(root: Path, out: Path | None) -> list[dict]:
    entries = collect(root)
    mdx = render_mdx(entries)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(mdx)
    else:
        sys.stdout.write(mdx)
    return entries


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("root", type=Path, help="charmos source root to scan")
    ap.add_argument("-o", "--out", type=Path, help="output .mdx (default: stdout)")
    args = ap.parse_args()

    if not args.root.exists():
        print(f"error: source root {args.root} does not exist", file=sys.stderr)
        return 1

    entries = generate(args.root, args.out)
    if not entries:
        print("warning: no CMDLINE_ENTRY_DECLARE sites found", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
