"""
The work behind the owner's spreadsheet (catalogue/workbook.py), run by `manage.py data_files` in its own
process, never in a web request: making a workbook takes seconds here and up to a minute on the server.
"""

import gzip
import json
import logging

from django.utils import timezone

from .models import DataExport

logger = logging.getLogger(__name__)


def pack_base(base):
    return gzip.compress(json.dumps(base, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def unpack_base(blob):
    return json.loads(gzip.decompress(bytes(blob)).decode("utf-8"))


def build_export(export):
    """Make one export's workbook and keep it with the values it was made with. Returns the workbook bytes, or
    None when it failed, with the reason on the row. Each write is its own short autocommit, so the reads
    that make the file hold no lock."""
    from .workbook import build_export as make

    now = timezone.now()
    DataExport.objects.filter(pk=export.pk).update(
        status=DataExport.Status.BUILDING, started_at=now, heartbeat_at=now, error="")
    try:
        data, base, counts = make(export)
    except Exception as exc:
        logger.exception("Spreadsheet RR-%s could not be made", export.pk)
        DataExport.objects.filter(pk=export.pk).update(
            status=DataExport.Status.FAILED, finished_at=timezone.now(),
            error=f"The file could not be made ({type(exc).__name__}).")
        return None
    DataExport.objects.filter(pk=export.pk).update(
        status=DataExport.Status.READY, finished_at=timezone.now(), heartbeat_at=timezone.now(), counts=counts,
        size_bytes=len(data), workbook=data, base=pack_base(base),
    )
    return data
