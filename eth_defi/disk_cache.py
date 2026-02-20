"""Shared disk cache root for offchain metadata and other cached data.

All modules that cache data to disk should use :py:data:`DEFAULT_CACHE_ROOT`
as their base directory, creating protocol-specific subdirectories beneath it.

For multiprocess-safe cache file writes, use :py:func:`eth_defi.utils.wait_other_writers`
to acquire a file lock before writing.
"""

from pathlib import Path

#: Root directory for all disk-cached data.
#:
#: Individual modules append their own subdirectory
#: (e.g. ``euler/``, ``lagoon/``, ``ember/``).
DEFAULT_CACHE_ROOT = Path.home() / ".tradingstrategy" / "cache"
