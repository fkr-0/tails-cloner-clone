from __future__ import annotations

from dataclasses import dataclass

DEFAULT_REMOTE_INDEX_URL = "https://download.tails.net/tails/stable/"
DEFAULT_TAILS_LATEST_RELEASE_URL = "https://tails.net/install/v2/Tails/amd64/stable/latest.json"
DEFAULT_TAILS_TAGS_API_URL = "https://gitlab.tails.boum.org/api/v4/projects/tails%2Ftails/repository/tags?per_page=100"
WINDOW_TITLE = "Tails Cloner"
WINDOW_SIZE = "1100x700"
MIN_WINDOW_SIZE = (1000, 640)
REFRESH_INTERVAL_MS = 200
VERSIONS_REFRESH_TIMEOUT_SECONDS = 15

# Font sizes
FONT_SIZE_LARGE = 14
FONT_SIZE_MEDIUM = 11
FONT_SIZE_SMALL = 10


@dataclass(frozen=True, slots=True)
class Branding:
    distribution: str = "Tails"
    window_title: str = WINDOW_TITLE
    accent_hex: str = "#56347c"


BRANDING = Branding()
