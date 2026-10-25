"""Typed paths for staged vs bundle databases."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StagedPath:
    path: Path


@dataclass(frozen=True)
class BundlePath:
    path: Path
