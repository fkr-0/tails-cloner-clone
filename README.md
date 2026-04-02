# Tails Cloner

A refreshed standalone Python desktop app for writing Tails images to removable devices.

## What's refreshed

- keeps the original installer's separation of config, source handling, creator logic, and GUI flow as inspiration
- removes the heavy GTK/UDisks runtime dependency in favor of a smaller Tk + `lsblk` standalone app
- fetches remote Tails versions asynchronously on startup by enumerating a remote directory listing
- ships a tag-triggered GitHub Actions workflow that builds an AppImage and publishes a release asset

## Local development

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m tails_cloner --help
```

## Run locally

```bash
PYTHONPATH=src python3 -m tails_cloner
```

## Release flow

Push a tag like `v0.2.0`. The workflow runs tests, builds the AppImage, emits SHA256 checksums, and creates a GitHub release.
