#!/usr/bin/env python3
"""Render C code as a linkified ``<SourceBlock>`` component.

This is the payload of the docs renovation's headline feature: rustdoc-style
"click a symbol → jump to its definition". Rather than post-processing
syntax-highlighted text with regex (the old, fragile approach), we:

1. tokenize C accurately with tree-sitter (identifiers vs. keywords vs. types
   vs. punctuation), then
2. resolve each identifier/type token against the clang-accurate symbol index
   (see index_clang.py), attaching a link to its definition.

The result is a list of typed segments consumed by ``SourceBlock.astro``.
Starlight's Expressive Code cannot add links to code, so the pipeline emits
this component instead of a fenced ```c block — full control, no framework
fight.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable

from tree_sitter_language_pack import get_parser

_parser = get_parser("c")

# C keywords — tree-sitter surfaces most as their own node types, but we also
# guard identifier-shaped keywords here so they never resolve as symbols.
C_KEYWORDS = {
    "auto",
    "break",
    "case",
    "char",
    "const",
    "continue",
    "default",
    "do",
    "double",
    "else",
    "enum",
    "extern",
    "float",
    "for",
    "goto",
    "if",
    "inline",
    "int",
    "long",
    "register",
    "restrict",
    "return",
    "short",
    "signed",
    "sizeof",
    "static",
    "struct",
    "switch",
    "typedef",
    "union",
    "unsigned",
    "void",
    "volatile",
    "while",
    "_Bool",
    "_Atomic",
    "_Noreturn",
}

# tree-sitter C leaf node types → a coarse CSS class for highlighting.
_TYPE_NODE_KINDS = {"primitive_type", "type_identifier", "sized_type_specifier"}
_STRING_NODE_KINDS = {
    "string_literal",
    "char_literal",
    "system_lib_string",
    "string_content",
    "escape_sequence",
}


@dataclasses.dataclass
class Segment:
    """One rendered piece of code: text, a highlight class, optional link."""

    text: str
    cls: str
    href: str | None = None
    # The resolved symbol name (for tooltips / testing), if this links somewhere.
    symbol: str | None = None

    def to_dict(self) -> dict:
        d = {"text": self.text, "cls": self.cls}
        if self.href:
            d["href"] = self.href
        if self.symbol:
            d["symbol"] = self.symbol
        return d


# A LinkResolver maps a symbol name → an href (or None if it shouldn't link).
LinkResolver = Callable[[str], str | None]


def _leaves(node):
    """Yield leaf nodes (actual tokens) of a tree-sitter tree, in order."""
    if node.child_count == 0:
        yield node
        return
    for child in node.children:
        yield from _leaves(child)


def _classify(node_type: str, text: str) -> str:
    if node_type in _TYPE_NODE_KINDS:
        return "type"
    if node_type == "identifier":
        return "ident"
    if node_type == "field_identifier":
        return "field"
    if node_type == "number_literal":
        return "number"
    if node_type in _STRING_NODE_KINDS:
        return "string"
    if node_type == "comment":
        return "comment"
    # Keyword node types are the keyword text itself ("const", "return", …),
    # or an identifier-shaped keyword we guard explicitly.
    if node_type.isalpha() and (node_type in C_KEYWORDS or text in C_KEYWORDS):
        return "keyword"
    if not node_type.replace("_", "").isalnum():
        return "punct"  # '(', '*', ',', ';', …
    return "other"


def tokenize(code: str) -> list[Segment]:
    """Tokenize a C fragment into highlight segments, preserving whitespace.

    Whitespace/gaps between tokens are emitted as plain ``ws`` segments so the
    original formatting round-trips exactly.
    """
    tree = _parser.parse(code.encode("utf-8"))
    src = code.encode("utf-8")
    segments: list[Segment] = []
    pos = 0
    for leaf in _leaves(tree.root_node):
        start, end = leaf.start_byte, leaf.end_byte
        if start < pos:
            continue  # defensive: overlapping/zero-width tokens
        if start > pos:
            segments.append(Segment(src[pos:start].decode("utf-8"), "ws"))
        text = src[start:end].decode("utf-8")
        if text:
            # A macro body is one opaque `preproc_arg` token; re-tokenize it as
            # C so #define bodies keep their syntax highlighting.
            if leaf.type == "preproc_arg" and text.strip():
                segments.extend(tokenize(text))
            else:
                segments.append(Segment(text, _classify(leaf.type, text)))
        pos = end
    if pos < len(src):
        segments.append(Segment(src[pos:].decode("utf-8"), "ws"))
    return segments


def resolve(segments: list[Segment], link_for: LinkResolver) -> list[Segment]:
    """Attach links to identifier/type segments that resolve to a symbol."""
    for seg in segments:
        if seg.cls not in ("ident", "type"):
            continue
        if seg.text in C_KEYWORDS:
            continue
        href = link_for(seg.text)
        if href:
            seg.href = href
            seg.symbol = seg.text
    return segments


def render(code: str, link_for: LinkResolver) -> list[Segment]:
    """Tokenize + resolve in one step."""
    return resolve(tokenize(code), link_for)


def index_resolver(index, target_for) -> LinkResolver:
    """Build a LinkResolver backed by a clang SymbolIndex.

    ``index`` is an index_clang.SymbolIndex (or its ``by_name``/``symbols``
    dicts loaded from JSON). ``target_for(symbol)`` turns a resolved
    ``Symbol``-like mapping ({file,line,kind,…}) into an href — this is where
    the "internal anchor vs. /source/ browser vs. GitHub" policy lives.
    """

    # Accept either a live SymbolIndex or a loaded {"symbols","by_name"} dict.
    def _lookup_live(name):
        sym = index.resolve(name)
        return dataclasses.asdict(sym) if sym is not None else None

    def _lookup_dict(name):
        usrs = index["by_name"].get(name)
        if not usrs:
            return None
        syms = [index["symbols"][u] for u in usrs]
        # A standard/builtin type (int32_t, size_t, uint64_t, …) parsed without a
        # real <stdint.h> gets implicitly re-declared as a file-local typedef in
        # every file that uses it — so it shows up "defined" as a typedef in
        # multiple files. That's the tell it isn't a real in-tree type alias, so
        # don't link it (it would otherwise point at an arbitrary use site).
        typedef_files = {s.get("file") for s in syms if s.get("kind") == "typedef"}
        if len(typedef_files) > 1:
            return None
        # Prefer a definition; fall back to the first declaration.
        best = None
        for s in syms:
            if s.get("is_definition"):
                return s
            best = best or s
        return best

    _lookup = _lookup_live if hasattr(index, "resolve") else _lookup_dict

    def _resolve(name: str) -> str | None:
        sym = _lookup(name)
        if sym is None:
            return None
        return target_for(sym)

    return _resolve


def to_mdx(
    segments: list[Segment],
    *,
    title: str | None = None,
    id: str | None = None,
    def_href: str | None = None,
) -> str:
    """Emit a ``<SourceBlock>`` MDX element carrying the resolved segments."""
    payload = json.dumps([s.to_dict() for s in segments], ensure_ascii=False)
    attrs = ""
    if title:
        attrs += f" title={json.dumps(title)}"
    if id:
        attrs += f" id={json.dumps(id)}"
    if def_href:
        attrs += f" defHref={json.dumps(def_href)}"
    return f"<SourceBlock{attrs} segments={{{payload}}} />"
