"""Bounded 64 MiB disposable I/O fixture for a CachePlus job slot."""

import hashlib
import json
import os
import time
from pathlib import Path

SIZE = 64 * 1024 * 1024
CHUNK = b"cacheplus-benchmark-2026" * 50000
CHUNK = CHUNK[:1024 * 1024]
path = Path(os.environ["TMPDIR"]) / "cacheplus-bench.bin"
start = time.perf_counter()
source_hash = hashlib.sha256()
with path.open("wb") as file:
    for _ in range(SIZE // len(CHUNK)):
        file.write(CHUNK)
        source_hash.update(CHUNK)
    file.flush()
    os.fsync(file.fileno())
write_seconds = time.perf_counter() - start
start = time.perf_counter()
read_hash = hashlib.sha256()
with path.open("rb") as file:
    while block := file.read(1024 * 1024):
        read_hash.update(block)
read_seconds = time.perf_counter() - start
if source_hash.digest() != read_hash.digest() or path.stat().st_size != SIZE:
    raise SystemExit("Benchmark readback mismatch")
print(json.dumps({"bytes": SIZE, "path": str(path), "sha256": source_hash.hexdigest(),
                  "write_fsync_ms": round(write_seconds * 1000, 2),
                  "read_ms": round(read_seconds * 1000, 2)}))
