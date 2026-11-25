from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedLayer:
    values: dict[str, str]
    warnings: list[str]


@dataclass(frozen=True)
class ResolvedSetting:
    key: str
    value: str | None
    source: str
    shadowed: tuple[str, ...]
    masked: bool


@dataclass(frozen=True)
class EffectiveConfig:
    settings: dict[str, ResolvedSetting]
    warnings: list[str]
    inherited: bool = False
