"""Storage transfer anchors for second-order taint (spec §3.1).

StorageWritePoint = data-flow-INTO-storage location (ORM save / setProperty /
cache.set / file write). NOT a dangerous sink — writing to DB is not itself a
vuln — so it stays a separate type and never enters sink_call_sites (avoids
single-hop track false-positives on every DB write).

StorageReadPoint is NOT a new type: it is SourcePoint(source_type=STORAGE)
(flavor decision A, spec §3.1 / plan Global Constraints).
"""
from __future__ import annotations
from enum import Enum
from pydantic import BaseModel


class StorageMedium(str, Enum):
    DB = "db"
    CONFIG = "config"
    CACHE = "cache"
    FILE = "file"


class StorageWritePoint(BaseModel):
    id: str
    caller_id: str
    callee_name: str
    callee_receiver: str | None = None
    medium: StorageMedium
    storage_token: str          # literal token (table/key/path); dynamic → "unresolvable"
    written_expr: str           # the expression being written (judge user-tainted)
    file_path: str
    line: int
    column: int = 0
    rule_id: str
    needs_review: bool = False
