#!/usr/bin/env python3
"""Clang-accurate semantic index for charmOS sources.

This is the *single source of truth* for symbol links in the docs pipeline.
Where ``make_json.py`` extracts structure syntactically with tree-sitter, this
module resolves symbols *semantically* with libclang: every definition
(functions, records, enums, typedefs, macros, global variables) is recorded by
its USR (Unified Symbol Resolution — clang's stable cross-TU identifier), along
with the sites that reference it.

Downstream (the ``<SourceBlock>`` emitter and cross-reference resolver) look up
identifiers here to produce clang-accurate "click a symbol → jump to its
definition" links, rather than guessing by name.

Usage:
    # from a compile_commands.json directory
    python3 index_clang.py --compile-commands build/ --root include/ -o index.json

    # from explicit files (args after ``--`` are passed to clang)
    python3 index_clang.py --files a.c b.c --root . -o index.json -- -I include -std=c11
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from collections.abc import Iterable
from pathlib import Path

import clang.cindex as cindex
from clang.cindex import CursorKind, TranslationUnit

# ── libclang discovery ───────────────────────────────────────────────────────
#
# clang.cindex loads a bundled/first-found libclang by default. Allow an
# explicit override (useful in CI or when multiple LLVMs are installed) via
# CHARMOS_LIBCLANG, then fall back to a few well-known locations before letting
# the default kick in.

_CANDIDATE_LIBCLANG = [
    os.environ.get("CHARMOS_LIBCLANG"),
    "/opt/homebrew/opt/llvm/lib/libclang.dylib",
    "/usr/local/opt/llvm/lib/libclang.dylib",
    "/usr/lib/llvm-18/lib/libclang.so.1",
    "/usr/lib/x86_64-linux-gnu/libclang-18.so.1",
]

_configured = False


def configure_libclang() -> None:
    """Point libclang at an explicit library if one is discoverable.

    Idempotent and best-effort: if nothing matches we let clang.cindex use its
    own default resolution (which already worked in local testing).
    """
    global _configured
    if _configured:
        return
    for cand in _CANDIDATE_LIBCLANG:
        if cand and Path(cand).exists():
            try:
                cindex.Config.set_library_file(cand)
            except Exception:
                # Config can only be set once per process; ignore if already set.
                pass
            break
    _configured = True


# ── data model ───────────────────────────────────────────────────────────────

# Cursor kinds we treat as *definitions/declarations* worth indexing.
_DEF_KINDS = {
    CursorKind.FUNCTION_DECL,
    CursorKind.STRUCT_DECL,
    CursorKind.UNION_DECL,
    CursorKind.ENUM_DECL,
    CursorKind.ENUM_CONSTANT_DECL,
    CursorKind.TYPEDEF_DECL,
    CursorKind.MACRO_DEFINITION,
    CursorKind.VAR_DECL,
}

# Cursor kinds that *reference* an already-declared symbol.
_REF_KINDS = {
    CursorKind.DECL_REF_EXPR,
    CursorKind.TYPE_REF,
    CursorKind.MEMBER_REF_EXPR,
    CursorKind.MEMBER_REF,
    CursorKind.CALL_EXPR,
    CursorKind.MACRO_INSTANTIATION,
}

_KIND_LABEL = {
    CursorKind.FUNCTION_DECL: "function",
    CursorKind.STRUCT_DECL: "struct",
    CursorKind.UNION_DECL: "union",
    CursorKind.ENUM_DECL: "enum",
    CursorKind.ENUM_CONSTANT_DECL: "enum_constant",
    CursorKind.TYPEDEF_DECL: "typedef",
    CursorKind.MACRO_DEFINITION: "macro",
    CursorKind.VAR_DECL: "variable",
}


@dataclasses.dataclass
class Symbol:
    usr: str
    name: str
    kind: str
    file: str
    line: int
    column: int
    end_line: int
    is_definition: bool


@dataclasses.dataclass
class Reference:
    usr: str
    name: str
    file: str
    line: int
    column: int


class SymbolIndex:
    """A collection of clang-resolved symbols and their reference sites."""

    def __init__(self) -> None:
        self.symbols: dict[str, Symbol] = {}
        self.references: list[Reference] = []
        self._seen_refs: set[tuple[str, str, int, int]] = set()

    # -- population --------------------------------------------------------
    def add_symbol(self, sym: Symbol) -> None:
        existing = self.symbols.get(sym.usr)
        # Prefer a definition location over a bare declaration; otherwise keep
        # the first one seen so results are deterministic.
        if existing is None or (sym.is_definition and not existing.is_definition):
            self.symbols[sym.usr] = sym

    def add_reference(self, ref: Reference) -> None:
        key = (ref.usr, ref.file, ref.line, ref.column)
        if key in self._seen_refs:
            return
        self._seen_refs.add(key)
        self.references.append(ref)

    # -- lookups -----------------------------------------------------------
    def by_name(self) -> dict[str, list[str]]:
        """Map each symbol *name* to the USRs that carry it."""
        out: dict[str, list[str]] = {}
        for usr, sym in self.symbols.items():
            out.setdefault(sym.name, []).append(usr)
        return out

    def resolve(self, name: str) -> Symbol | None:
        """Best-effort name → definition lookup (prefers a real definition)."""
        matches = [s for s in self.symbols.values() if s.name == name]
        if not matches:
            return None
        matches.sort(key=lambda s: (not s.is_definition, s.file, s.line))
        return matches[0]

    # -- serialization -----------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "symbols": {u: dataclasses.asdict(s) for u, s in sorted(self.symbols.items())},
            "by_name": {n: sorted(v) for n, v in sorted(self.by_name().items())},
            "references": [
                dataclasses.asdict(r)
                for r in sorted(self.references, key=lambda r: (r.file, r.line, r.column))
            ],
        }

    def save(self, path: str | os.PathLike) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")


# ── indexing ─────────────────────────────────────────────────────────────────


def _within_root(path: str | None, root: Path | None) -> bool:
    if path is None:
        return False
    if root is None:
        return True
    try:
        Path(path).resolve().relative_to(root)
        return True
    except ValueError:
        return False


def _rel(path: str, root: Path | None) -> str:
    if root is None:
        return str(Path(path).resolve())
    try:
        return str(Path(path).resolve().relative_to(root))
    except ValueError:
        return str(Path(path).resolve())


def _walk(cursor, index: SymbolIndex, root: Path | None) -> None:
    for node in cursor.walk_preorder():
        loc = node.location
        fname = loc.file.name if loc.file is not None else None
        if fname is None or not _within_root(fname, root):
            continue

        if node.kind in _DEF_KINDS:
            name = node.spelling
            if not name:
                continue  # unnamed declaration — no linkable name
            # Anonymous records carry a synthetic spelling like
            # "union (unnamed at foo.h:27:5)"; they are not linkable symbols.
            if (
                node.kind in (CursorKind.STRUCT_DECL, CursorKind.UNION_DECL, CursorKind.ENUM_DECL)
                and node.is_anonymous()
            ):
                continue
            # Only index file-scope variables; skip function locals, which are
            # not linkable API symbols and collide with real globals by name.
            if node.kind == CursorKind.VAR_DECL:
                parent = node.semantic_parent
                if parent is None or parent.kind != CursorKind.TRANSLATION_UNIT:
                    continue
            usr = node.get_usr()
            if not usr:
                continue
            extent_end = node.extent.end
            index.add_symbol(
                Symbol(
                    usr=usr,
                    name=name,
                    kind=_KIND_LABEL.get(node.kind, node.kind.name.lower()),
                    file=_rel(fname, root),
                    line=loc.line,
                    column=loc.column,
                    end_line=extent_end.line if extent_end is not None else loc.line,
                    is_definition=bool(node.is_definition()),
                )
            )
        elif node.kind in _REF_KINDS:
            target = node.referenced
            if target is None:
                continue
            usr = target.get_usr()
            if not usr:
                continue
            index.add_reference(
                Reference(
                    usr=usr,
                    name=target.spelling or node.spelling,
                    file=_rel(fname, root),
                    line=loc.line,
                    column=loc.column,
                )
            )


def index_files(
    files: Iterable[str],
    args: list[str] | None = None,
    root: str | os.PathLike | None = None,
    index: SymbolIndex | None = None,
) -> SymbolIndex:
    """Index an explicit list of translation units with shared ``args``."""
    configure_libclang()
    args = list(args or [])
    root_path = Path(root).resolve() if root is not None else None
    index = index if index is not None else SymbolIndex()
    idx = cindex.Index.create()
    # DETAILED_PROCESSING_RECORD is required to see macro definitions/uses.
    options = TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD
    for f in files:
        tu = idx.parse(str(f), args=args, options=options)
        _emit_diagnostics(tu, f)
        _walk(tu.cursor, index, root_path)
    return index


def _index_one_tu(job):
    """Parse+walk a single TU in a worker process; return picklable results."""
    filename, args, directory, root_str = job
    configure_libclang()
    root_path = Path(root_str).resolve() if root_str else None
    idx = cindex.Index.create()
    options = TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD
    prev = os.getcwd()
    try:
        if directory:
            os.chdir(directory)
        tu = idx.parse(filename, args=args, options=options)
        local = SymbolIndex()
        _walk(tu.cursor, local, root_path)
    finally:
        os.chdir(prev)
    return list(local.symbols.values()), local.references


def index_compile_commands(
    build_dir: str | os.PathLike,
    root: str | os.PathLike | None = None,
    index: SymbolIndex | None = None,
    jobs: int | None = None,
) -> SymbolIndex:
    """Index every translation unit in ``build_dir/compile_commands.json``.

    TUs are parsed in parallel across ``jobs`` worker processes (default: all
    CPUs), each with its own libclang Index; results are merged in the parent.
    """
    configure_libclang()
    root_str = str(Path(root).resolve()) if root is not None else None
    index = index if index is not None else SymbolIndex()
    db = cindex.CompilationDatabase.fromDirectory(str(build_dir))

    job_list = []
    for cmd in db.getAllCompileCommands() or []:
        # Drop the compiler argv[0] and the source-file argument itself; keep
        # flags (-I, -D, -std, -ffreestanding, …).
        raw = list(cmd.arguments or [])[1:]
        src = cmd.filename
        args = [a for a in raw if Path(a).name != Path(src).name]
        job_list.append((src, args, cmd.directory or ".", root_str))

    workers = jobs or (os.cpu_count() or 4)
    if workers <= 1 or len(job_list) <= 1:
        for job in job_list:
            symbols, references = _index_one_tu(job)
            _merge(index, symbols, references)
        return index

    from concurrent.futures import ProcessPoolExecutor

    with ProcessPoolExecutor(max_workers=workers) as pool:
        for symbols, references in pool.map(_index_one_tu, job_list):
            _merge(index, symbols, references)
    return index


def _merge(index: SymbolIndex, symbols, references) -> None:
    for s in symbols:
        index.add_symbol(s)
    for r in references:
        index.add_reference(r)


def _emit_diagnostics(tu, source) -> None:
    """Surface real parse errors instead of silently producing an empty index."""
    for diag in tu.diagnostics:
        if diag.severity >= cindex.Diagnostic.Error:
            print(f"[index_clang] {source}: {diag.spelling}", file=sys.stderr)


# ── CLI ──────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--compile-commands", metavar="DIR", help="directory containing compile_commands.json"
    )
    parser.add_argument("--files", nargs="+", help="explicit source files to index")
    parser.add_argument("--root", help="restrict the index to symbols under this dir")
    parser.add_argument("-o", "--output", required=True, help="output JSON path")
    parser.add_argument(
        "-j", "--jobs", type=int, default=None, help="parallel worker processes (default: all CPUs)"
    )
    parser.add_argument(
        "clang_args", nargs="*", help="args after -- passed to clang (with --files)"
    )
    ns = parser.parse_args(argv)

    if ns.compile_commands:
        index = index_compile_commands(ns.compile_commands, root=ns.root, jobs=ns.jobs)
    elif ns.files:
        index = index_files(ns.files, args=ns.clang_args, root=ns.root)
    else:
        parser.error("provide either --compile-commands or --files")

    index.save(ns.output)
    print(
        f"[index_clang] {len(index.symbols)} symbols, "
        f"{len(index.references)} references → {ns.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
