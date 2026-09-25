#!/usr/bin/env python3
"""Harvest ordinary C comments as per-item documentation.

charmOS headers are densely commented with plain ``/* */`` comments rather than
a doc-comment dialect, so instead of requiring ``/**`` we attach the comments
that already exist, using rustdoc-like adjacency rules:

- **leading** — a comment block that ends on the line directly above a
  declaration (no blank line between) documents that declaration;
- **trailing** — a comment that starts on the same line as a declaration, after
  its code (``NORMAL, /* All OK */``), documents that declaration;
- a non-adjacent comment before the first declaration is **module** docs
  (rustdoc's ``//!``); any other free-floating comment is not documentation.

Comment bodies are markdown. Paragraphs led by a review tag (``TODO:``,
``XXX(sched):`` …, the same vocabulary as charmOS's ``scripts/tagscan.py``
pre-commit hook) are split out as structured *notes* rather than prose.
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass, field

# Mirrors charmOS scripts/tagscan.py (KINDS / TAG_RE): canonical kind → aliases.
TAG_KINDS = {
    "TODO": [],
    "FIX": ["FIXME", "FIXIT", "ISSUE"],
    "BUG": [],
    "HACK": [],
    "WARN": ["WARNING", "XXX"],
    "PERF": ["OPTIM", "OPTIMIZE", "PERFORMANCE"],
    "NOTE": ["INFO"],
    "TEST": ["TESTING"],
}
_TAG_ALIAS = {alt: kind for kind, alts in TAG_KINDS.items() for alt in alts}
_TAG_ALIAS.update({k: k for k in TAG_KINDS})
# A tag must LEAD its paragraph here (tagscan matches anywhere, but a tag
# mid-sentence is prose, not a note): TAG or TAG(subclass), then a colon.
_TAG_RE = re.compile(
    r"^(" + "|".join(sorted(_TAG_ALIAS, key=len, reverse=True)) + r")\b[ \t]*"
    r"(?:\(([^)]*)\))?[ \t]*:[ \t]*"
)

# Comments that are pipeline directives or idea bodies, not item docs.
_DIRECTIVE_RE = re.compile(r"@title:|@idea:|^\s*#\s*(small|big|huge)\s+idea\b", re.I | re.M)

# A group label: a short noun phrase heading a run of items ("Current tick
# data", "Pressures", "Larger types") rather than describing the first of them.
_GROUP_LABEL_RE = re.compile(r"^[^.,;:()`\n]+$")


def _is_group_label(md: str) -> bool:
    return bool(_GROUP_LABEL_RE.match(md)) and len(md.split()) <= 4 and md[:1].isupper()


# A section banner (``/* ===== Computed ===== */``) labels a group of lines;
# it documents none of them.
_BANNER_RE = re.compile(r"^[=\-*~#_]{3,}.*[=\-*~#_]{3,}$")

# Box-drawing / ASCII-art signals: a paragraph containing these is a diagram
# and must keep its exact layout, so it is fenced as ```text.
_DIAGRAM_RE = re.compile(r"[─-╿←-⇿]|[-=]{2,}>|<[-=]{2,}|\+-{2,}|\S {3,}\S.* {3,}\S")


@dataclass
class Note:
    kind: str  # canonical kind (TODO, FIX, WARN, …)
    tag: str  # tag as written (XXX, FIXME, …)
    sub: str | None  # optional (subclass)
    text: str  # markdown
    line: int

    def to_dict(self) -> dict:
        return {"kind": self.kind, "tag": self.tag, "sub": self.sub, "text": self.text, "line": self.line}


@dataclass
class Comment:
    start_line: int  # 1-based
    end_line: int
    trailing: bool  # code precedes it on its first line
    raw: str


@dataclass
class Harvest:
    """What attach() found for one file."""

    module_doc: str = ""
    module_notes: list[Note] = field(default_factory=list)  # floating tags
    attached: int = 0
    dropped: int = 0  # free-floating prose that documents nothing


# ── collection ────────────────────────────────────────────────────────────────


def collect_comments(root, code: bytes) -> list[Comment]:
    """Every comment node in the tree, with runs of ``//`` lines merged."""
    lines = code.split(b"\n")
    out: list[Comment] = []

    def walk(node):
        if node.type == "comment":
            row, col = node.start_point
            before = lines[row][:col].strip() if row < len(lines) else b""
            out.append(
                Comment(
                    start_line=row + 1,
                    end_line=node.end_point[0] + 1,
                    trailing=bool(before),
                    raw=code[node.start_byte : node.end_byte].decode("utf-8", "replace"),
                )
            )
            return
        for ch in node.children:
            walk(ch)

    walk(root)
    out.sort(key=lambda c: c.start_line)

    merged: list[Comment] = []
    for c in out:
        prev = merged[-1] if merged else None
        if (
            prev is not None
            and not c.trailing
            and not prev.trailing
            and c.raw.startswith("//")
            and prev.raw.startswith("//")
            and c.start_line == prev.end_line + 1
        ):
            prev.raw += "\n" + c.raw
            prev.end_line = c.end_line
        else:
            merged.append(c)
    return merged


# ── comment text → markdown ──────────────────────────────────────────────────


def comment_to_markdown(raw: str) -> str:
    """Strip comment syntax (``/* */``, `` * `` margins, ``//``) and dedent,
    leaving the author's markdown. ASCII-art paragraphs are fenced."""
    if raw.startswith("//"):
        body = [re.sub(r"^\s*//+!?<? ?", "", ln) for ln in raw.splitlines()]
        first, rest = (body[0], body[1:]) if body else ("", [])
    else:
        text = re.sub(r"^/\*+!?<?", "", raw)
        text = re.sub(r"\*+/$", "", text)
        lines = text.splitlines() or [""]
        first, rest = lines[0], lines[1:]
        # Continuation lines usually carry a " * " margin, but a diagram pasted
        # into the comment often doesn't — so strip it per line, only where it
        # is a real margin ('*' then space/EOL, not '**bold**').
        rest = [re.sub(r"^\s*\*(?=\s|$) ?", "", ln) for ln in rest]
    # A boxed comment's right-hand border (`… text          *`) is decoration.
    rest = [re.sub(r"\s{2,}\*\s*$", "", ln) for ln in rest]
    first = re.sub(r"\s{2,}\*\s*$", "", first)
    rest = textwrap.dedent("\n".join(rest)).splitlines()
    md = "\n".join([first.strip(), *rest]).strip("\n").strip()
    return _fence_diagrams(md)


# A comment line that reads as C: a call/statement ending in ) ; { or }.
_CODE_LINE_RE = re.compile(r"^\s*([\w.>*&-]+\s*\(.*\)\s*[;{]?|[\w\s*.>\[\]-]+=\s*.+;|[{}];?)\s*$")


def _is_code(lines: list[str]) -> bool:
    """Every line reads as C (a trailing `// note` allowed; pure `//` lines
    allowed alongside real code), with at least one call or statement."""
    code = [re.sub(r"\s*//.*$", "", ln) for ln in lines]
    real = [c for c in code if c.strip()]
    return (
        bool(real)
        and all(_CODE_LINE_RE.match(c) for c in real)
        and any("(" in c or ";" in c for c in real)
    )


def _fence_diagrams(md: str) -> str:
    out = []
    for para in re.split(r"\n\s*\n", md):
        lines = para.split("\n")
        if "```" in para:
            out.append(para)
        elif _DIAGRAM_RE.search(para) and len(lines) > 1:
            out.append("```text\n" + textwrap.dedent(para) + "\n```")
        elif _is_code(lines):
            out.append("```c\n" + textwrap.dedent(para) + "\n```")
        else:
            out.append(para)
    return "\n\n".join(out)


def split_notes(md: str, line: int) -> tuple[str, list[Note]]:
    """Separate tag-led paragraphs (``TODO: …``) from prose.

    Every paragraph that starts with a tag opens a new note. When the comment
    itself *starts* with a tag, untagged paragraphs continue the open note (the
    whole comment is review text); otherwise they are prose."""
    tag_led = bool(_TAG_RE.match(md))
    prose, notes = [], []
    for para in re.split(r"\n\s*\n", md):
        pm = _TAG_RE.match(para)
        if pm:
            notes.append(_note(pm, para, line))
        elif tag_led and notes:
            notes[-1].text = (notes[-1].text + "\n\n" + para).strip()
        else:
            prose.append(para)
    return "\n\n".join(prose).strip(), notes


def _note(m: re.Match, text: str, line: int) -> Note:
    tag = m.group(1)
    body = text[m.end() :].strip()
    return Note(kind=_TAG_ALIAS[tag], tag=tag, sub=m.group(2) or None, text=body, line=line)


# ── attachment ────────────────────────────────────────────────────────────────


def attach(
    comments: list[Comment],
    targets: list[tuple[int, dict]],
    bodies: list[tuple[int, int, dict]] = (),
) -> Harvest:
    """Attach comments to ``targets`` — (line, item-dict) pairs — in place.

    Attached items gain ``doc`` (markdown) and/or ``notes`` (list of dicts).
    Several targets may share a line (a typedef and its inline struct).
    ``bodies`` — (open line, first member line, item) — lets a comment that
    opens a struct/enum body, ahead of its members, document the item itself.
    """
    by_line: dict[int, list[dict]] = {}
    for line, obj in targets:
        by_line.setdefault(line, []).append(obj)
    first_decl = min(by_line, default=None)
    lines_sorted = sorted(by_line)
    pending_labels: list[tuple[list[dict], str, int]] = []
    harvest = Harvest()

    # Consecutive leading comments with no blank line between them form one
    # block (e.g. a one-line summary comment directly above a longer one).
    blocks: list[tuple[Comment, list[str]]] = []
    for c in comments:
        prev = blocks[-1][0] if blocks else None
        if prev and not c.trailing and not prev.trailing and c.start_line == prev.end_line + 1:
            prev.end_line = c.end_line
            blocks[-1][1].append(c.raw)
        else:
            blocks.append((Comment(c.start_line, c.end_line, c.trailing, c.raw), [c.raw]))

    for c, raws in blocks:
        parts = [comment_to_markdown(r) for r in raws]
        if any(_DIRECTIVE_RE.search(p) for p in parts):
            continue
        md = "\n\n".join(p for p in parts if p and not _BANNER_RE.match(p))
        if not md:
            continue
        doc, notes = split_notes(md, c.start_line)

        owners = by_line.get(c.start_line) if c.trailing else by_line.get(c.end_line + 1)
        # A short label directly above a run of adjacent items labels the run;
        # it isn't the first item's documentation.
        # The run's next item: the following line, or past one blank line.
        run_next = next((ln for ln in (c.end_line + 2, c.end_line + 3) if ln in by_line), None)
        if owners and not c.trailing and not notes and _is_group_label(doc) and run_next:
            # Decided after all comments attach: only a label if the run's
            # next item has no docs of its own (else it's a per-item doc).
            pending_labels.append((owners, doc, run_next))
            harvest.attached += 1
            continue
        if not owners and not c.trailing:
            owners = [
                obj for open_line, first, obj in bodies if open_line < c.start_line and c.end_line < first
            ]
        if owners:
            for obj in owners:
                if doc:
                    obj["doc"] = (obj["doc"] + "\n\n" + doc) if obj.get("doc") else doc
                if notes:
                    obj.setdefault("notes", []).extend(n.to_dict() for n in notes)
            harvest.attached += 1
            continue

        # Tags separated from the next declaration by blank lines still review
        # that declaration (a TODO above a block of #defines), so they attach
        # to it; tags in the file header (before any declaration) or with
        # nothing below them are about the file, so they stay module-level.
        if notes:
            nxt = next((ln for ln in lines_sorted if ln > c.end_line), None)
            in_header = first_decl is None or c.end_line < first_decl
            if nxt is not None and not c.trailing and not in_header:
                for obj in by_line[nxt]:
                    obj.setdefault("notes", []).extend(n.to_dict() for n in notes)
            else:
                harvest.module_notes.extend(notes)
        if doc and not c.trailing and first_decl is not None and c.end_line < first_decl:
            harvest.module_doc = (harvest.module_doc + "\n\n" + doc).strip()
        elif doc:
            harvest.dropped += 1

    for owners, label, next_line in pending_labels:
        is_label = not any(obj.get("doc") for obj in by_line[next_line])
        for obj in owners:
            if is_label:
                obj["group"] = label
            else:
                obj["doc"] = (label + "\n\n" + obj["doc"]) if obj.get("doc") else label
    return harvest


def item_targets(type_info: dict) -> list[tuple[int, dict]]:
    """(line, dict) for every documentable item and member in a c_parse dict."""
    targets: list[tuple[int, dict]] = []

    def add(obj):
        if obj.get("line"):
            targets.append((obj["line"], obj))

    def add_members(members):
        for m in members or []:
            add(m)
            if m.get("nested"):
                add_members(m["nested"].get("members"))

    types = type_info.get("types", {})
    for s in types.get("structs", []):
        add(s)
        add_members(s.get("members"))
    for e in types.get("enums", []):
        add(e)
        for m in e.get("members", []):
            add(m)
    for key in ("typedefs", "globals"):
        for t in types.get(key, []):
            add(t)
    for f in type_info.get("functions", []):
        add(f)
    for d in type_info.get("defines", []):
        add(d)
    return targets


def item_bodies(type_info: dict) -> list[tuple[int, int, dict]]:
    """(open line, first member line, item) for every struct/union/enum body."""
    types = type_info.get("types", {})
    out = []
    for item in types.get("structs", []) + types.get("enums", []):
        lines = [m["line"] for m in item.get("members", []) if m.get("line")]
        if item.get("line") and lines:
            out.append((item["line"], min(lines), item))
    return out
