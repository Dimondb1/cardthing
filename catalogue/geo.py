"""
Which country a visitor's address is in, from the free DB-IP country
database (CC BY 4.0). Nothing finer than the country is looked up.

    python manage.py fetch_geoip      # downloads this month's database

The database path is RIPRAPTOR_GEOIP_DB. With no file there, every
visitor's country is unknown and the rest of the site is unaffected.
"""

import gzip
import logging
import os
import urllib.request
from datetime import date

from django.conf import settings

logger = logging.getLogger(__name__)

DOWNLOAD = "https://download.db-ip.com/free/dbip-country-lite-{year}-{month:02d}.mmdb.gz"
# DB-IP refuses Python's default user agent with 403.
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) RipRaptor/1.0 (+https://ripraptor.com)"
_reader = None
_reader_path = None


def database_path():
    return getattr(settings, "RIPRAPTOR_GEOIP_DB", "")


def reader():
    """The open database, or None when there is no file. Reopened if the path changes."""
    global _reader, _reader_path
    path = database_path()
    if not path or not os.path.exists(path):
        return None
    if _reader is None or _reader_path != path:
        import maxminddb

        _reader = maxminddb.open_database(path)
        _reader_path = path
    return _reader


def reset():
    global _reader, _reader_path
    if _reader is not None:
        _reader.close()
    _reader = _reader_path = None


def country_of(ip):
    """Two-letter country code for an address, or "" when unknown."""
    db = reader()
    if db is None or not ip:
        return ""
    try:
        record = db.get(ip) or {}
    except ValueError:
        return ""
    return ((record.get("country") or {}).get("iso_code") or "")[:2].upper()


def fetch(path=None, when=None, opener=None):
    """Download this month's database to ``path``. Returns the path written."""
    path = path or database_path()
    if not path:
        raise ValueError("Set RIPRAPTOR_GEOIP_DB to the file the database should be saved as.")
    when = when or date.today()
    opener = opener or (lambda target, timeout: urllib.request.urlopen(
        urllib.request.Request(target, headers={"User-Agent": USER_AGENT}), timeout=timeout))
    url = DOWNLOAD.format(year=when.year, month=when.month)
    try:
        with opener(url, timeout=120) as response:
            packed = response.read()
    except Exception:
        # The new month's file appears a few days in; fall back to last month's.
        previous = date(when.year - 1, 12, 1) if when.month == 1 else date(when.year, when.month - 1, 1)
        url = DOWNLOAD.format(year=previous.year, month=previous.month)
        with opener(url, timeout=120) as response:
            packed = response.read()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "wb") as out:
        out.write(gzip.decompress(packed))
    os.replace(tmp, path)
    reset()
    logger.info("GeoIP database saved to %s from %s", path, url)
    return path
