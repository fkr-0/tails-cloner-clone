"""Tails Cloner - A standalone GUI application for cloning Tails images.

This package provides a refreshed desktop application for installing and
upgrading Tails to USB devices. It supports cloning from the running Tails
system (offline) or from downloaded ISO/IMG images.

Key features:
- Clone from running Tails system (no download required)
- Use local ISO/IMG files
- Detect existing Tails installations and offer upgrade vs install
- Support for all device types (not just removable)
- Keyboard accessible interface

Example usage:
    python -m tails_cloner
    # or
    tails-cloner --remote-index-url https://download.tails.net/tails/stable/
"""

__all__ = ["__version__"]

__version__ = "0.2.0"
