"""Tails Cloner Clone - standalone GUI and automation CLI for Tails media.

This package provides a refreshed desktop application for installing and
upgrading Tails to USB devices. It supports the running Tails medium as a
persistence-preserving upgrade source or local/downloaded ISO/IMG images.

Key features:
- Upgrade from the running Tails system (no download required)
- Use local ISO/IMG files
- Download official images with SHA-256 + pinned OpenPGP verification
- Detect existing Tails installations and offer upgrade vs install
- Support for all device types (not just removable)
- Keyboard accessible interface
- Versioned JSON/JSONL operator output for read, plan, install, and upgrade flows

Example usage:
    python -m tails_cloner
    # or
    tails-cloner-clone status --output json
"""

__all__ = ["__version__"]

__version__ = "0.5.5"
