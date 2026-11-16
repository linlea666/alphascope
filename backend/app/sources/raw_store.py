"""Raw response archive. Local filesystem now; an S3-compatible implementation can replace it behind the same interface."""

import gzip
import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class StoredObject:
    path: str
    sha256: str
    size_bytes: int


class RawStore(Protocol):
    def put(self, source: str, key: str, fetched_at: datetime, body: bytes) -> StoredObject: ...

    def get(self, path: str) -> bytes: ...


class LocalRawStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def put(self, source: str, key: str, fetched_at: datetime, body: bytes) -> StoredObject:
        rel = Path(source) / fetched_at.strftime("%Y/%m/%d") / f"{key}.json.gz"
        dest = self.root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".tmp")
        tmp.write_bytes(gzip.compress(body, compresslevel=6))
        tmp.replace(dest)
        return StoredObject(path=str(rel), sha256=hashlib.sha256(body).hexdigest(), size_bytes=len(body))

    def get(self, path: str) -> bytes:
        return gzip.decompress((self.root / path).read_bytes())
