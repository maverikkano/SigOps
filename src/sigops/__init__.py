"""SigOps — Signal Operations. RSS ingestion pipeline."""

from __future__ import annotations

import logging
import sys

__all__ = ["__version__"]

__version__ = "0.1.0"

_handler = logging.StreamHandler(sys.stderr)
_handler.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
)
_pkg_logger = logging.getLogger("sigops")
_pkg_logger.addHandler(_handler)
_pkg_logger.setLevel(logging.DEBUG)
