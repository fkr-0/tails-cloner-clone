from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class LocalImageSource:
    path: Path

    @property
    def exists(self) -> bool:
        return self.path.exists()

    @property
    def suffix(self) -> str:
        return self.path.suffix.lower()

    def validate(self) -> None:
        if not self.path.exists():
            raise FileNotFoundError(str(self.path))
        if not self.path.is_file():
            raise IsADirectoryError(str(self.path))
