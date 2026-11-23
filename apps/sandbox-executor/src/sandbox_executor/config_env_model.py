"""The records of the layered configuration (see ``config_env``): a parsed layer, one resolved setting, the whole."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedLayer:
    """The keys of one layer file and the warnings its lines drew."""

    values: dict[str, str]
    warnings: list[str]


@dataclass(frozen=True)
class ResolvedSetting:
    """One effective variable: its value (None for unset), source layer, shadowed broader layers and secrecy."""

    key: str
    value: str | None
    source: str
    shadowed: tuple[str, ...]
    secret: bool


@dataclass(frozen=True)
class EffectiveConfig:
    """Every effective setting by key, the warnings of the resolution, and whether it was inherited."""

    settings: dict[str, ResolvedSetting]
    warnings: list[str]
    inherited: bool = False
