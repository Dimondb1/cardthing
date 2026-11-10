"""
The shared file cache (ripraptor/caching.py), where a cache file this process may not read is a miss, not an error.

Django's file cache writes each entry as a file only its writer may read (tempfile.mkstemp: mode 600). The site runs
as its own user, so an entry written by any other user is one it cannot open, and Django's ``get`` lets that through
as a PermissionError: a 500 on every page that reads the entry, for as long as the file lies there. On 10 Nov 2026
the owner ran ``manage.py visibility_proof --inside`` on the server's console as root, as he had been told to. It
built the Pokémon and Magic pages and kept them, as root's files, and those two pages and llms.txt answered 500 until
the next update emptied the cache.

Here such a file is treated as not there. The site then builds the entry itself and writes its own copy over the
other user's, which it may do because the folder is its own: the next request mends it. The same goes for asking
whether an entry is there and for renewing one.
"""

from django.core.cache.backends.base import DEFAULT_TIMEOUT
from django.core.cache.backends.filebased import FileBasedCache


class SharedFileCache(FileBasedCache):
    def get(self, key, default=None, version=None):
        try:
            return super().get(key, default, version)
        except PermissionError:
            return default

    def has_key(self, key, version=None):
        try:
            return super().has_key(key, version)
        except PermissionError:
            return False

    def touch(self, key, timeout=DEFAULT_TIMEOUT, version=None):
        try:
            return super().touch(key, timeout, version)
        except PermissionError:
            return False
