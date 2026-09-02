# ![Tails Cloner Clone icon](assets/tails-cloner-clone-32.png) Tails Cloner Clone

A refreshed standalone Python desktop app for safely writing Tails images to explicitly selected block devices, including intentional internal-disk targets.

## What's refreshed

- keeps the original installer's separation of config, source handling, creator logic, and GUI flow as inspiration
- removes the heavy GTK/UDisks runtime dependency in favor of a smaller Tk + `lsblk` standalone app
- fetches remote Tails versions asynchronously over HTTPS (through Tor when available)
- downloads remote images only when both SHA-256 and a detached OpenPGP signature can be verified against the bundled, pinned Tails signing key
- ships a tag-triggered GitHub Actions workflow that builds an AppImage and publishes a release asset

## System requirements

The write path uses standard Linux tools: `lsblk`, `pkexec`, `dd`, `mount`, `umount`, `blockdev`, `udevadm`, and `gpg`. Remote downloads fail closed when GnuPG is unavailable. `torify` and `curl` are used automatically when a local Tor SOCKS port is available.

## Local development

```bash
PYTHONPATH=src python3 -m pytest
uvx ruff check .
mypy src tests
PYTHONPATH=src python3 -m tails_cloner --help
```

## Run locally

```bash
PYTHONPATH=src python3 -m tails_cloner
```

## Release flow

Push a `vX.Y.Z` tag matching the package version. The workflow runs pytest, Ruff, and mypy, builds the AppImage, emits SHA256 checksums, and creates a GitHub release.
