from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import urljoin
from urllib.request import urlopen

from tails_cloner.config import VERSIONS_REFRESH_TIMEOUT_SECONDS
from tails_cloner.models import VersionAssets

_VERSION_RE = re.compile(r"^\d+(?:\.\d+)+$")


class _DirectoryListingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for name, value in attrs:
            if name.lower() == "href" and value:
                self.links.append(value)


def _version_sort_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


FetchText = Callable[[str, int], str]


def fetch_text(url: str, timeout_seconds: int) -> str:
    with urlopen(url, timeout=timeout_seconds) as response:  # noqa: S310 - user-controlled URL is expected app input
        return response.read().decode("utf-8", errors="replace")



def parse_directory_listing(html: str) -> list[str]:
    parser = _DirectoryListingParser()
    parser.feed(html)
    versions = {
        href.rstrip("/")
        for href in parser.links
        if _VERSION_RE.fullmatch(href.rstrip("/"))
    }
    return sorted(versions, key=_version_sort_key, reverse=True)



def build_version_assets(base_url: str, version: str) -> VersionAssets:
    normalized_base = base_url if base_url.endswith("/") else f"{base_url}/"
    directory_url = urljoin(normalized_base, f"{version}/")
    stem = f"tails-amd64-{version}"
    return VersionAssets(
        version=version,
        directory_url=directory_url,
        iso_url=urljoin(directory_url, f"{stem}.iso"),
        img_url=urljoin(directory_url, f"{stem}.img"),
        sig_url=urljoin(directory_url, f"{stem}.iso.sig"),
        sha256_url=urljoin(directory_url, f"{stem}.img.sha256"),
    )


@dataclass(slots=True)
class RemoteVersionIndex:
    base_url: str
    fetch_text: FetchText = field(default=fetch_text)
    timeout_seconds: int = VERSIONS_REFRESH_TIMEOUT_SECONDS

    def fetch_versions(self) -> list[VersionAssets]:
        html = self.fetch_text(self.base_url, self.timeout_seconds)
        return [build_version_assets(self.base_url, version) for version in parse_directory_listing(html)]
