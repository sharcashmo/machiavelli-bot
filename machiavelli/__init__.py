"""Metadatos del paquete principal de Machiavelli."""

import logging
from importlib.metadata import PackageNotFoundError, version

logging.getLogger("machiavelli").addHandler(logging.NullHandler())

try:
    __version__ = version("machiavelli")
except PackageNotFoundError:
    __version__ = "0.7.0"

VERSION = __version__

__all__ = ["VERSION", "__version__"]
