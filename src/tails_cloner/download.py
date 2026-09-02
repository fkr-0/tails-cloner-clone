"""Shared verified remote-image download service.

The GUI and CLI deliberately use this module instead of maintaining separate
network or verification paths.  Remote Tails images are accepted only after
both the published SHA-256 digest and a detached OpenPGP signature chaining to
the bundled, pinned Tails signing key have been verified.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from tails_cloner.network import fetch_text_torified, should_use_torify
from tails_cloner.verification import (
    TAILS_SIGNING_PRIMARY_FINGERPRINT,
    parse_sha256_text,
    verify_openpgp_detached_signature,
    verify_sha256,
)

MAX_CHECKSUM_BYTES = 256 * 1024
MAX_SIGNATURE_BYTES = 1024 * 1024
DOWNLOAD_CHUNK_BYTES = 1024 * 1024


class DownloadError(RuntimeError):
    """Base error for verified remote downloads."""


class DownloadNetworkError(DownloadError):
    """Remote metadata or image transfer failed."""


class DownloadVerificationError(DownloadError):
    """Downloaded or already-present bytes failed cryptographic verification."""


@dataclass(frozen=True, slots=True)
class RemoteImageRequest:
    version: str
    image_url: str
    checksum_url: str
    signature_url: str
    destination: Path


@dataclass(frozen=True, slots=True)
class DownloadProgress:
    phase: str
    message: str
    bytes_received: int | None = None
    bytes_total: int | None = None


@dataclass(frozen=True, slots=True)
class VerifiedImageResult:
    path: Path
    version: str
    source_url: str
    checksum_url: str
    signature_url: str
    sha256: str
    signing_fingerprint: str
    pinned_primary_fingerprint: str
    reused_existing: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "version": self.version,
            "source_url": self.source_url,
            "checksum_url": self.checksum_url,
            "signature_url": self.signature_url,
            "sha256": self.sha256,
            "signing_fingerprint": self.signing_fingerprint,
            "pinned_primary_fingerprint": self.pinned_primary_fingerprint,
            "verification": ["sha256", "openpgp"],
            "reused_existing": self.reused_existing,
        }


ProgressCallback = Callable[[DownloadProgress], None]


def bundled_asset_path(name: str) -> Path:
    """Resolve a packaged asset in source-tree and PyInstaller executions."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "assets" / name
    return Path(__file__).resolve().parents[2] / "assets" / name


def default_download_directory() -> Path:
    return Path.home() / ".cache" / "tails-cloner-clone" / "downloads"


def default_destination_for_url(image_url: str, *, version: str = "") -> Path:
    filename = Path(urlparse(image_url).path).name or f"tails-amd64-{version or 'download'}.img"
    return default_download_directory() / filename


def require_https_url(url: str, purpose: str) -> None:
    if urlparse(url).scheme.casefold() != "https":
        raise ValueError(f"{purpose} URL must use HTTPS: {url}")


class HttpsOnlyRedirectHandler(HTTPRedirectHandler):
    """Reject HTTPS-to-HTTP redirects before the downgraded URL is fetched."""

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request | None:
        require_https_url(newurl, "Remote redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_https(url: str, *, timeout: int) -> Any:
    require_https_url(url, "Remote")
    return build_opener(HttpsOnlyRedirectHandler()).open(url, timeout=timeout)


class VerifiedImageDownloader:
    """Download and verify an official Tails IMG with atomic destination writes."""

    def __init__(
        self,
        *,
        key_path: Path | None = None,
        use_torify: Callable[[], bool] = should_use_torify,
    ) -> None:
        self.key_path = key_path or bundled_asset_path("tails-signing-minimal.key")
        self._use_torify = use_torify

    @staticmethod
    def _emit(
        callback: ProgressCallback | None,
        phase: str,
        message: str,
        *,
        bytes_received: int | None = None,
        bytes_total: int | None = None,
    ) -> None:
        if callback is not None:
            callback(
                DownloadProgress(
                    phase=phase,
                    message=message,
                    bytes_received=bytes_received,
                    bytes_total=bytes_total,
                )
            )

    def _read_checksum(self, url: str) -> str:
        require_https_url(url, "Remote checksum")
        try:
            if self._use_torify():
                text = fetch_text_torified(url, 30)
                if len(text.encode("utf-8")) > MAX_CHECKSUM_BYTES:
                    raise ValueError("Published checksum response is unexpectedly large")
                return text

            with open_https(url, timeout=30) as response:
                effective_url = str(response.geturl()) if hasattr(response, "geturl") else url
                require_https_url(effective_url, "Remote checksum redirect")
                payload: bytes = response.read(MAX_CHECKSUM_BYTES + 1)
            if len(payload) > MAX_CHECKSUM_BYTES:
                raise ValueError("Published checksum response is unexpectedly large")
            return payload.decode("utf-8", errors="strict")
        except ValueError:
            raise
        except Exception as error:  # noqa: BLE001 - normalized at service boundary
            raise DownloadNetworkError(f"Could not retrieve remote checksum: {error}") from error

    def _download_signature(self, url: str, destination: Path) -> None:
        require_https_url(url, "Remote signature")
        try:
            if self._use_torify():
                subprocess.run(
                    [
                        "torify",
                        "curl",
                        "-fL",
                        "--proto",
                        "=https",
                        "--proto-redir",
                        "=https",
                        "--max-filesize",
                        str(MAX_SIGNATURE_BYTES),
                        "--output",
                        str(destination),
                        url,
                    ],
                    check=True,
                    text=True,
                    capture_output=True,
                )
                if destination.stat().st_size > MAX_SIGNATURE_BYTES:
                    raise ValueError("Published OpenPGP signature is unexpectedly large")
                return

            with open_https(url, timeout=30) as response:
                effective_url = str(response.geturl()) if hasattr(response, "geturl") else url
                require_https_url(effective_url, "Remote signature redirect")
                payload: bytes = response.read(MAX_SIGNATURE_BYTES + 1)
            if len(payload) > MAX_SIGNATURE_BYTES:
                raise ValueError("Published OpenPGP signature is unexpectedly large")
            destination.write_bytes(payload)
        except ValueError:
            raise
        except Exception as error:  # noqa: BLE001 - normalized at service boundary
            raise DownloadNetworkError(f"Could not retrieve remote OpenPGP signature: {error}") from error

    def _download_image(
        self,
        url: str,
        destination: Path,
        callback: ProgressCallback | None,
    ) -> None:
        require_https_url(url, "Remote IMG")
        try:
            if self._use_torify():
                self._emit(callback, "download", "Downloading Tails IMG through Tor")
                subprocess.run(
                    [
                        "torify",
                        "curl",
                        "-fL",
                        "--proto",
                        "=https",
                        "--proto-redir",
                        "=https",
                        "--output",
                        str(destination),
                        url,
                    ],
                    check=True,
                    text=True,
                    capture_output=True,
                )
                self._emit(
                    callback,
                    "download",
                    "Tails IMG transfer complete",
                    bytes_received=destination.stat().st_size,
                )
                return

            with (
                open_https(url, timeout=60) as response,
                destination.open("wb") as output,
            ):
                effective_url = str(response.geturl()) if hasattr(response, "geturl") else url
                require_https_url(effective_url, "Remote IMG redirect")
                content_length = response.headers.get("Content-Length")
                total = int(content_length) if content_length and content_length.isdigit() else None
                received = 0
                self._emit(callback, "download", "Downloading Tails IMG", bytes_received=0, bytes_total=total)
                while chunk := response.read(DOWNLOAD_CHUNK_BYTES):
                    output.write(chunk)
                    received += len(chunk)
                    self._emit(
                        callback,
                        "download",
                        "Downloading Tails IMG",
                        bytes_received=received,
                        bytes_total=total,
                    )
        except ValueError:
            raise
        except Exception as error:  # noqa: BLE001 - normalized at service boundary
            raise DownloadNetworkError(f"Could not download remote IMG: {error}") from error

    def _verify_candidate(
        self,
        candidate: Path,
        *,
        expected_digest: str,
        signature_path: Path,
        callback: ProgressCallback | None,
    ) -> tuple[str, str]:
        try:
            self._emit(callback, "verification", "Verifying SHA-256")
            actual_digest = verify_sha256(candidate, expected_digest)
            self._emit(callback, "verification", "Verifying pinned OpenPGP signature")
            signing_fingerprint = verify_openpgp_detached_signature(
                candidate,
                signature_path,
                self.key_path,
            )
            return actual_digest, signing_fingerprint
        except (OSError, RuntimeError, ValueError) as error:
            raise DownloadVerificationError(str(error)) from error

    def download(
        self,
        request: RemoteImageRequest,
        *,
        progress_callback: ProgressCallback | None = None,
    ) -> VerifiedImageResult:
        for url, purpose in (
            (request.image_url, "Remote IMG"),
            (request.checksum_url, "Remote checksum"),
            (request.signature_url, "Remote signature"),
        ):
            try:
                require_https_url(url, purpose)
            except ValueError as error:
                raise DownloadNetworkError(str(error)) from error

        destination = request.destination.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        filename = Path(urlparse(request.image_url).path).name or destination.name

        self._emit(progress_callback, "metadata", "Retrieving published SHA-256")
        try:
            checksum_text = self._read_checksum(request.checksum_url)
            expected_digest = parse_sha256_text(checksum_text, expected_filename=filename)
        except DownloadNetworkError:
            raise
        except (UnicodeError, ValueError) as error:
            raise DownloadVerificationError(f"Published SHA-256 metadata is invalid: {error}") from error

        signature_path: Path | None = None
        partial_path: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="wb",
                prefix=f".{destination.name}.",
                suffix=".sig.part",
                dir=destination.parent,
                delete=False,
            ) as signature_file:
                signature_path = Path(signature_file.name)
            self._emit(progress_callback, "metadata", "Retrieving detached OpenPGP signature")
            self._download_signature(request.signature_url, signature_path)

            if destination.is_file():
                self._emit(progress_callback, "reuse", f"Verifying existing file {destination}")
                digest, fingerprint = self._verify_candidate(
                    destination,
                    expected_digest=expected_digest,
                    signature_path=signature_path,
                    callback=progress_callback,
                )
                return VerifiedImageResult(
                    path=destination,
                    version=request.version,
                    source_url=request.image_url,
                    checksum_url=request.checksum_url,
                    signature_url=request.signature_url,
                    sha256=digest,
                    signing_fingerprint=fingerprint,
                    pinned_primary_fingerprint=TAILS_SIGNING_PRIMARY_FINGERPRINT,
                    reused_existing=True,
                )
            if destination.exists():
                raise DownloadVerificationError(f"Download destination exists but is not a regular file: {destination}")

            with NamedTemporaryFile(
                mode="wb",
                prefix=f".{destination.name}.",
                suffix=".part",
                dir=destination.parent,
                delete=False,
            ) as output:
                partial_path = Path(output.name)
            self._download_image(request.image_url, partial_path, progress_callback)
            digest, fingerprint = self._verify_candidate(
                partial_path,
                expected_digest=expected_digest,
                signature_path=signature_path,
                callback=progress_callback,
            )
            partial_path.replace(destination)
            partial_path = None
            return VerifiedImageResult(
                path=destination,
                version=request.version,
                source_url=request.image_url,
                checksum_url=request.checksum_url,
                signature_url=request.signature_url,
                sha256=digest,
                signing_fingerprint=fingerprint,
                pinned_primary_fingerprint=TAILS_SIGNING_PRIMARY_FINGERPRINT,
                reused_existing=False,
            )
        finally:
            if signature_path is not None:
                signature_path.unlink(missing_ok=True)
            if partial_path is not None:
                partial_path.unlink(missing_ok=True)


def gpg_available() -> bool:
    return shutil.which("gpg") is not None
