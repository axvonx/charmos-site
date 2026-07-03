#!/usr/bin/env python3
"""Typed document model (IR) for a parsed C translation unit.

This sits between extraction (``make_json`` → JSON) and rendering (``make_md``).
Instead of threading raw ``dict``s (and their ``.get("key")`` guesswork) through
every renderer, ``make_md`` loads each JSON file into a :class:`Module` of typed
constructs and renders from attributes.

The dataclasses mirror the JSON shape 1:1; each ``from_dict`` is tolerant of
missing keys (they become ``None``/empty), matching the ``dict.get`` semantics
the renderers previously relied on. :meth:`Module.from_json` also performs the
struct/union split and the "has a name, not literally 'none'" filtering that the
render loop used to do inline, so the model already reflects *what will be
rendered*.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Param:
    """A function (or function-pointer) parameter."""

    type: str = ""
    name: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> Param:
        return cls(type=d.get("type") or "", name=d.get("name"))


@dataclass
class Field:
    """A struct/union member. ``nested`` holds an inlined anonymous composite."""

    type: str = ""
    name: str = ""
    offset: int | None = None
    nested: Composite | None = None

    @classmethod
    def from_dict(cls, d: dict) -> Field:
        nested = d.get("nested")
        return cls(
            type=d.get("type") or "",
            name=d.get("name") or "",
            offset=d.get("offset"),
            nested=Composite.from_nested(nested) if nested else None,
        )


@dataclass
class Composite:
    """A struct or union (``kind`` distinguishes them)."""

    name: str = ""
    kind: str = "struct"
    size: int | None = None
    line: int | None = None
    members: list[Field] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> Composite:
        return cls(
            name=d.get("name") or "",
            kind=d.get("kind") or "struct",
            size=d.get("size"),
            line=d.get("line"),
            members=[Field.from_dict(m) for m in d.get("members", [])],
        )

    @classmethod
    def from_nested(cls, d: dict) -> Composite:
        """An anonymous nested composite carries only ``kind`` + ``members``."""
        return cls(
            kind=d.get("kind") or "struct",
            members=[Field.from_dict(m) for m in d.get("members", [])],
        )


@dataclass
class EnumMember:
    name: str = ""
    value: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> EnumMember:
        return cls(name=d.get("name") or "", value=d.get("value"))


@dataclass
class Enum:
    name: str = ""
    line: int | None = None
    members: list[EnumMember] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> Enum:
        return cls(
            name=d.get("name") or "",
            line=d.get("line"),
            members=[EnumMember.from_dict(m) for m in d.get("members", [])],
        )


@dataclass
class FnPtr:
    """The function-pointer shape of a typedef."""

    return_type: str = ""
    parameters: list[Param] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> FnPtr:
        return cls(
            return_type=d.get("return_type") or "",
            parameters=[Param.from_dict(p) for p in d.get("parameters") or []],
        )


@dataclass
class Typedef:
    name: str = ""
    type: str = ""
    line: int | None = None
    fn_ptr: FnPtr | None = None

    @classmethod
    def from_dict(cls, d: dict) -> Typedef:
        fn_ptr = d.get("fn_ptr")
        return cls(
            name=d.get("name") or "",
            type=d.get("type") or "",
            line=d.get("line"),
            fn_ptr=FnPtr.from_dict(fn_ptr) if fn_ptr else None,
        )


@dataclass
class Function:
    name: str = ""
    return_type: str = ""
    parameters: list[Param] = field(default_factory=list)
    qualifiers: list[str] = field(default_factory=list)
    line: int | None = None

    @classmethod
    def from_dict(cls, d: dict) -> Function:
        return cls(
            name=d.get("name") or "",
            return_type=d.get("return_type") or "",
            parameters=[Param.from_dict(p) for p in d.get("parameters") or []],
            qualifiers=list(d.get("qualifiers") or []),
            line=d.get("line"),
        )


@dataclass
class Variable:
    """A file-scope / extern global variable."""

    name: str = ""
    type: str = ""
    storage: str = ""
    raw_text: str = ""
    line: int | None = None

    @classmethod
    def from_dict(cls, d: dict) -> Variable:
        return cls(
            name=d.get("name") or "",
            type=d.get("type") or "",
            storage=d.get("storage") or "",
            raw_text=d.get("raw_text") or "",
            line=d.get("line"),
        )


@dataclass
class Macro:
    """A ``#define``. ``params`` is None for object-like, ``"(a, b)"`` for
    function-like macros; ``multiline`` keeps the real source layout in
    ``raw_text``."""

    name: str = ""
    params: str | None = None
    value: str = ""
    multiline: bool = False
    raw_text: str = ""
    line: int | None = None

    @classmethod
    def from_dict(cls, d: dict) -> Macro:
        return cls(
            name=d.get("name") or "",
            params=d.get("params"),
            value=d.get("value") or "",
            multiline=bool(d.get("multiline", False)),
            raw_text=d.get("raw_text") or "",
            line=d.get("line"),
        )


def _has_name(name: str) -> bool:
    """Rendered constructs must have a real name that isn't literally 'none'."""
    return bool(name) and name.lower() != "none"


@dataclass
class Module:
    """Everything documentable in one parsed C file, already split + filtered
    the way the render loop needs it."""

    file: str = ""
    title: str | None = None
    structs: list[Composite] = field(default_factory=list)
    unions: list[Composite] = field(default_factory=list)
    enums: list[Enum] = field(default_factory=list)
    typedefs: list[Typedef] = field(default_factory=list)
    functions: list[Function] = field(default_factory=list)
    variables: list[Variable] = field(default_factory=list)
    macros: list[Macro] = field(default_factory=list)

    @classmethod
    def from_json(cls, data: dict) -> Module:
        c_parse = data.get("c_parse", {})
        types = c_parse.get("types", {})

        composites = [
            Composite.from_dict(s)
            for s in types.get("structs", [])
            if _has_name(s.get("name") or "")
        ]
        return cls(
            file=data.get("file", ""),
            title=data.get("title"),
            structs=[c for c in composites if c.kind.lower() != "union"],
            unions=[c for c in composites if c.kind.lower() == "union"],
            enums=[
                Enum.from_dict(e) for e in types.get("enums", []) if _has_name(e.get("name") or "")
            ],
            typedefs=[Typedef.from_dict(t) for t in types.get("typedefs", []) if t.get("name")],
            functions=[
                Function.from_dict(f) for f in c_parse.get("functions", []) if f.get("name")
            ],
            variables=[Variable.from_dict(g) for g in types.get("globals", []) if g.get("name")],
            # Macros are rendered unfiltered (append_defines_to_md applies no
            # name filter), so keep every define here.
            macros=[Macro.from_dict(m) for m in c_parse.get("defines", [])],
        )
