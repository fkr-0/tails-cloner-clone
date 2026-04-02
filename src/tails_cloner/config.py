from __future__ import annotations

from dataclasses import dataclass

DEFAULT_REMOTE_INDEX_URL = "https://download.tails.net/tails/stable/"
WINDOW_TITLE = "Tails Cloner"
WINDOW_SIZE = "980x640"
MIN_WINDOW_SIZE = (900, 580)
REFRESH_INTERVAL_MS = 200
VERSIONS_REFRESH_TIMEOUT_SECONDS = 15


@dataclass(frozen=True, slots=True)
class Branding:
    distribution: str = "Tails"
    window_title: str = WINDOW_TITLE
    accent_hex: str = "#56347c"


BRANDING = Branding()
