"""Trame based 3D viewer for VERAin core descriptions."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("vera-in-viewer")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0"

__all__ = ["__version__"]
