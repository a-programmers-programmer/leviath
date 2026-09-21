"""Generic, strict record parsing.

Every typed record in this package subclasses `Record`. The base class reads
the dataclass field list and type hints, and parses a dict from the daemon:

* missing required key        -> SchemaError
* wrong type                  -> SchemaError
* key not declared on the type -> SchemaError ("unknown key")

So the types are validated against real daemon responses: any drift shows up
as an exception instead of a silently dropped field.
"""

from __future__ import annotations

import dataclasses
import typing

JSON = typing.Union[Dict, List, str, int, float, bool, None] if False else object


class SchemaError(ValueError):
    """A daemon response did not match the declared type."""


def _scalar_check(value, typ, where):
    if typ is bool:
        if not isinstance(value, bool):
            raise SchemaError(f"{where}: expected bool, got {type(value).__name__} ({value!r})")
        return value
    if typ is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise SchemaError(f"{where}: expected int, got {type(value).__name__} ({value!r})")
        return value
    if typ is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SchemaError(f"{where}: expected float, got {type(value).__name__} ({value!r})")
        return float(value)
    if typ is str:
        if not isinstance(value, str):
            raise SchemaError(f"{where}: expected str, got {type(value).__name__} ({value!r})")
        return value
    if typ is object:  # typing.Any
        return value
    if not isinstance(value, typ):
        raise SchemaError(f"{where}: expected {typ.__name__}, got {type(value).__name__} ({value!r})")
    return value


def _split_optional(tp):
    """Return (inner_type, is_optional) for Optional[T] / T."""
    origin = typing.get_origin(tp)
    if origin is typing.Union:
        args = [a for a in typing.get_args(tp) if a is not type(None)]  # noqa: E721
        if len(args) == 1:
            return args[0], True
    return tp, False


def _parse_value(value, tp, where):
    tp, optional = _split_optional(tp)
    if value is None:
        if optional:
            return None
        raise SchemaError(f"{where}: got null for non-optional {tp}")
    origin = typing.get_origin(tp)
    if origin in (list, typing.List):
        (inner,) = typing.get_args(tp) or (object,)
        if not isinstance(value, list):
            raise SchemaError(f"{where}: expected list, got {type(value).__name__}")
        return [_parse_value(v, inner, f"{where}[{i}]") for i, v in enumerate(value)]
    if origin in (dict, typing.Dict):
        args = typing.get_args(tp) or (str, object)
        if not isinstance(value, dict):
            raise SchemaError(f"{where}: expected dict, got {type(value).__name__}")
        out = {}
        for k, v in value.items():
            key = _scalar_check(k, args[0], f"{where}.<key>")
            out[key] = _parse_value(v, args[1], f"{where}.{k}")
        return out
    if isinstance(tp, type) and issubclass(tp, Record):
        if not isinstance(value, dict):
            raise SchemaError(f"{where}: expected dict for {tp.__name__}, got {type(value).__name__}")
        return tp.from_dict(value, where)
    return _scalar_check(value, tp, where)


class Record:
    """Base for types parsed from real daemon JSON."""

    @classmethod
    def known_keys(cls) -> typing.Tuple[str, ...]:
        return tuple(f.name for f in dataclasses.fields(cls))

    @classmethod
    def from_dict(cls, d, where: str = None):
        where = where or cls.__name__
        if not isinstance(d, dict):
            raise SchemaError(f"{where}: expected dict, got {type(d).__name__}")
        hints = typing.get_type_hints(cls)
        known = cls.known_keys()
        unknown = sorted(set(d) - set(known))
        if unknown:
            raise SchemaError(f"{where}: unknown keys {unknown}; daemon shape changed?")
        fields = {f.name: f for f in dataclasses.fields(cls)}
        kwargs = {}
        for name in known:
            if name not in d:
                f = fields[name]
                if f.default is not dataclasses.MISSING or f.default_factory is not dataclasses.MISSING:
                    continue  # daemon version omits an optional field
                raise SchemaError(f"{where}: missing required key {name!r}")
            kwargs[name] = _parse_value(d[name], hints[name], f"{where}.{name}")
        return cls(**kwargs)

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)
