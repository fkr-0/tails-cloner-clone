from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import urljoin
from urllib.request import urlopen

from tails_cloner.config import (
    DEFAULT_TAILS_LATEST_RELEASE_URL,
    DEFAULT_TAILS_TAGS_API_URL,
    VERSIONS_REFRESH_TIMEOUT_SECONDS,
)
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


FetchText = Callable[[str, int], str]
FetchJson = Callable[[str, int], object]


class RemoteIndexError(RuntimeError):
    """Raised when no version source could produce a usable catalog."""


@dataclass(frozen=True, slots=True)
class LatestReleaseMetadata:
    version: str
    iso_url: str | None = None
    img_url: str | None = None
    sig_url: str | None = None
    sha256_url: str | None = None



def _version_sort_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))



def is_stable_version(version: str) -> bool:
    return _VERSION_RE.fullmatch(version) is not None



def fetch_text(url: str, timeout_seconds: int) -> str:
    with urlopen(url, timeout=timeout_seconds) as response:  # noqa: S310 - remote catalog is user-configurable app input
        return response.read().decode("utf-8", errors="replace")



def fetch_json(url: str, timeout_seconds: int) -> object:
    return json.loads(fetch_text(url, timeout_seconds))



def parse_directory_listing(html: str) -> list[str]:
    parser = _DirectoryListingParser()
    parser.feed(html)
    versions = {
        href.rstrip("/")
        for href in parser.links
        if is_stable_version(href.rstrip("/"))
    }
    return sorted(versions, key=_version_sort_key, reverse=True)



def parse_latest_release_document(payload: object) -> LatestReleaseMetadata:
    if not isinstance(payload, dict):
        raise ValueError("latest release document must be a JSON object")

    version = payload.get("version")
    if not isinstance(version, str) or not is_stable_version(version):
        raise ValueError("latest release document does not contain a stable version")

    iso_entry = payload.get("iso")
    img_entry = payload.get("img")

    iso_url = iso_entry.get("url") if isinstance(iso_entry, dict) else None
    img_url = img_entry.get("url") if isinstance(img_entry, dict) else None
    sig_url = iso_entry.get("sig") if isinstance(iso_entry, dict) else None
    sha256_url = img_entry.get("sha256_url") if isinstance(img_entry, dict) else None

    return LatestReleaseMetadata(
        version=version,
        iso_url=iso_url if isinstance(iso_url, str) else None,
        img_url=img_url if isinstance(img_url, str) else None,
        sig_url=sig_url if isinstance(sig_url, str) else None,
        sha256_url=sha256_url if isinstance(sha256_url, str) else None,
    )



def parse_gitlab_tags_document(payload: object) -> list[str]:
    if not isinstance(payload, list):
        raise ValueError("tags document must be a JSON array")

    versions = {
        name
        for entry in payload
        if isinstance(entry, dict)
        for name in [entry.get("name")]
        if isinstance(name, str) and is_stable_version(name)
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



def apply_latest_release_metadata(base_url: str, metadata: LatestReleaseMetadata) -> VersionAssets:
    assets = build_version_assets(base_url=base_url, version=metadata.version)
    return VersionAssets(
        version=metadata.version,
        directory_url=assets.directory_url,
        iso_url=metadata.iso_url or assets.iso_url,
        img_url=metadata.img_url or assets.img_url,
        sig_url=metadata.sig_url or assets.sig_url,
        sha256_url=metadata.sha256_url or assets.sha256_url,
    )


@dataclass(slots=True)
class RemoteVersionIndex:
    base_url: str
    latest_release_url: str = DEFAULT_TAILS_LATEST_RELEASE_URL
    tags_api_url: str = DEFAULT_TAILS_TAGS_API_URL
    fetch_text: FetchText = field(default=fetch_text)
    fetch_json: FetchJson = field(default=fetch_json)
    timeout_seconds: int = VERSIONS_REFRESH_TIMEOUT_SECONDS

    def fetch_versions(self) -> list[VersionAssets]:
        versions_by_name: dict[str, VersionAssets] = {}
        errors: list[str] = []

        latest_release = self._fetch_latest_release(errors)
        if latest_release is not None:
            versions_by_name[latest_release.version] = latest_release

        for version in self._fetch_tag_versions(errors):
            versions_by_name.setdefault(version, build_version_assets(self.base_url, version))

        for version in self._fetch_directory_listing_versions(errors):
            versions_by_name.setdefault(version, build_version_assets(self.base_url, version))

        if not versions_by_name:
            detail = "; ".join(errors) if errors else "no sources returned any versions"
            raise RemoteIndexError(f"failed to build remote Tails version catalog: {detail}")

        return sorted(versions_by_name.values(), key=lambda entry: _version_sort_key(entry.version), reverse=True)

    def _fetch_latest_release(self, errors: list[str]) -> VersionAssets | None:
        try:
            payload = self.fetch_json(self.latest_release_url, self.timeout_seconds)
            metadata = parse_latest_release_document(payload)
            return apply_latest_release_metadata(self.base_url, metadata)
        except Exception as error:  # noqa: BLE001 - errors are aggregated for UI visibility
            errors.append(f"latest release metadata unavailable ({error})")
            return None

    def _fetch_tag_versions(self, errors: list[str]) -> list[str]:
        try:
            payload = self.fetch_json(self.tags_api_url, self.timeout_seconds)
            return parse_gitlab_tags_document(payload)
        except Exception as error:  # noqa: BLE001 - errors are aggregated for UI visibility
            errors.append(f"git tags unavailable ({error})")
            return []

    def _fetch_directory_listing_versions(self, errors: list[str]) -> list[str]:
        try:
            html = self.fetch_text(self.base_url, self.timeout_seconds)
            return parse_directory_listing(html)
        except Exception as error:  # noqa: BLE001 - errors are aggregated for UI visibility
            errors.append(f"directory listing unavailable ({error})")
            return []
