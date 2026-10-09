"""
The cache every process shares.

The web workers, the cron commands and any command run by hand each run in
their own process. A cache kept in memory belongs to one process, so a
restock written by an import could not clear what the web workers had
cached. A folder of cache files is shared by all of them: a clear in one
process is seen by every other at once.
"""

import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger("ripraptor")

SERVER_CACHE_DIR = "/var/lib/ripraptor/cache"

MEMORY_CACHE = {
    "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    "LOCATION": "ripraptor",
}


def cache_dir(environ, debug, base_dir):
    """RIPRAPTOR_CACHE_DIR, else the server folder in production and .cache beside the code in development."""
    chosen = environ.get("RIPRAPTOR_CACHE_DIR", "").strip()
    if chosen:
        return Path(chosen)
    return base_dir / ".cache" if debug else Path(SERVER_CACHE_DIR)


def shared_caches(directory):
    """CACHES for a file cache in directory, or a per-process memory cache when the folder cannot be used."""
    try:
        os.makedirs(directory, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".write-check-"):
            pass
    except OSError as exc:
        logger.warning(
            "Cache folder %s cannot be used (%s). Each process keeps its own cache instead, "
            "so imports cannot clear what the site has cached.", directory, exc,
        )
        return {"default": dict(MEMORY_CACHE)}
    return {"default": {"BACKEND": "django.core.cache.backends.filebased.FileBasedCache", "LOCATION": str(directory)}}
