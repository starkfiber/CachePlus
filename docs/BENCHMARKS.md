# CachePlus MVP verification · 2026-11-17

These are bounded observations on one Mac with an APFS cache volume sharing its SSD. They establish local behavior, not universal speed or capacity. The source revision for the MVP is the release tag attached to this document.

## WhatIF loopback API scenario

The reviewed suite made GET requests to `/v1/status` and `/v1/backends` at `127.0.0.1:8767`, 20 requests per stage. Baseline and follow-up used concurrency 1; parallel used concurrency 5. The budget was 60 requests and 45 seconds of probe time. It checked response status and header latency; application body assertions were checked separately in unit tests. WhatIF verification confirmed the evidence hashes.

| Revision | Stage | Requests | Errors | `/v1/status` p95 | `/v1/backends` p95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Before status fix | Baseline | 20 | 1 timeout | 2,010 ms | 30.0 ms |
| MVP candidate | Baseline | 20 | 0 | 12.6 ms | 0.61 ms |
| MVP candidate | Parallel | 20 | 0 | 5.12 ms | 0.95 ms |
| MVP candidate | Follow-up | 20 | 0 | 1.54 ms | 0.42 ms |

The earlier suite stopped after baseline failure. The fix stopped recursively measuring every completed job on each status request. New completed jobs record their size once; `cacheplus status --deep` explicitly measures older jobs. A follow-up stage repeats requests after parallel traffic; it does not inject a fault or prove service recovery. Each route has only 10 samples per stage, so p95 values are descriptive and unstable.

Local raw WhatIF receipts remain in project-owned `.whatif/cacheplus-api-20261117` and `.whatif/cacheplus-api-mvp-20261117`; they are intentionally excluded from the public repository because they contain machine-specific paths. The reviewed scenario SHA-256 is `f54969f8178de1cc84848ef0b3b71f351b1ea1bd670a32cd661b71346425dd13`. WhatIF source review returned no heuristic leads, which is not a security audit.

## Interface latency

Run `python3 tests/bench_protocols.py` while the local API is running. Each interface was sampled 30 times sequentially. CLI includes process startup; MCP measurements reuse one initialized process; API is loopback HTTP. These are not equivalent execution paths.

| Status interface | Median | p95 | Maximum |
| --- | ---: | ---: | ---: |
| CLI, fresh process | 49.9 ms | 53.6 ms | 54.0 ms |
| MCP, warm process | 0.584 ms | 0.625 ms | 0.789 ms |
| HTTP API, loopback | 0.805 ms | 0.943 ms | 9.61 ms |

## Disposable job I/O

`cacheplus run --disposable benchmark20261117 -- python3 tests/bench_job.py` wrote, fsynced, read and SHA-256 checked 64 MiB in the CachePlus APFS volume. Three repeats gave median write plus fsync **30.44 ms**, median read **24.10 ms**, and median runner wall time **309.96 ms**. The expected SHA-256 was `c08a9908af257f753675344c70b537f5839cc0e7961c134154b6b2710dff300a` in all three runs. The exact benchmark slot was reclaimed afterward. Repeated identical data may benefit from OS caching, so these figures are not sustained device throughput or a USB/NAS/cloud benchmark.

## Failure and safety checks

- An unmounted path was rejected before a job could write to it.
- Two simultaneous jobs with the same label: the first completed, the second was rejected by the slot lock. Its exact test slot was reclaimed.
- An MCP run without `disposable: true` was rejected; a real MCP `/usr/bin/true` job returned exit 0 with clean JSON-RPC output.
- Long-lived MCP and API processes observed a changed profile without a restart.
- HTTP POST returned 405; status and backend GETs returned valid JSON. Assessments are read-only.
- Eight local unit and protocol tests passed. The public CI matrix is configured for Python 3.10 and 3.13 on macOS and Linux; check the latest workflow result for its observed status.

## Limits

The runner redirects common temporary/cache paths, not arbitrary application writes. The bank size cap is checked during a job and can be overshot between checks; a filesystem quota must be provided separately for a hard limit. A mounted cloud drive uses the user's own sync or mount client; CachePlus has no native cloud account connector. Windows job execution, physical USB/NAS/cloud disconnect tests, multi-machine throughput, and sustained production load are not established by these results. Source, chats, Git and unique files must remain outside disposable slots.
