"""Outer container of a ``.hog4show`` file.

A Hog 4 show file is::

    gzip( tar( hog_ident.json, hogdatabase.mk2 ) )

* ``hog_ident.json``  - small JSON header (software version, timestamp, ...)
* ``hogdatabase.mk2`` - a plain SQLite 3 database.  Every table has the
  columns ``id, sequence, xtti, attic`` (plus a few denormalised columns on
  some tables); the real content lives in the ``xtti`` BLOB, see ``xtti.py``.
"""
from __future__ import annotations

import gzip
import io
import json
import os
import sqlite3
import tarfile
import tempfile
from dataclasses import dataclass, field

IDENT_NAME = "hog_ident.json"
DB_NAME = "hogdatabase.mk2"


@dataclass
class ShowContainer:
    ident: dict
    db_bytes: bytes
    members: dict = field(default_factory=dict)  # name -> size (for info)

    def open_db(self) -> sqlite3.Connection:
        """Return an in-memory SQLite connection holding the show database."""
        # sqlite3 cannot open bytes directly on all Python versions; go via a temp file
        # and copy into memory so nothing stays locked on disk.
        fd, path = tempfile.mkstemp(suffix=".mk2")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(self.db_bytes)
            src = sqlite3.connect(path)
            mem = sqlite3.connect(":memory:")
            src.backup(mem)
            src.close()
        finally:
            os.unlink(path)
        return mem


def read_container(path: str) -> ShowContainer:
    with open(path, "rb") as f:
        data = f.read()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    if data[:16] == b"SQLite format 3\x00":
        # Already a bare database (e.g. an extracted hogdatabase.mk2)
        return ShowContainer(ident={}, db_bytes=data, members={DB_NAME: len(data)})
    ident, db, members = {}, None, {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tar:
        for m in tar.getmembers():
            if not m.isfile():
                continue
            members[m.name] = m.size
            content = tar.extractfile(m).read()
            if m.name.endswith(IDENT_NAME):
                ident = json.loads(content.decode("utf-8"))
            elif m.name.endswith(".mk2") or content[:16] == b"SQLite format 3\x00":
                db = content
    if db is None:
        raise ValueError(f"{path}: no SQLite database found inside the show archive")
    return ShowContainer(ident=ident, db_bytes=db, members=members)
