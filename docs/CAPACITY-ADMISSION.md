# Hosted capacity admission foundation (feature branch)

CachePlus local disposable jobs and the hosted Cache++ Space/Universe workspace are separate products. The local APFS cache on a developer's Mac is **not** capacity for hosted customers. This branch includes an offline admission calculator and a local SQLite reference ledger that serializes capacity reservations. Neither is wired to checkout or production storage.

Run the synthetic example:

```sh
python3 -B plugins/cache/scripts/cacheplus_capacity.py docs/capacity-example.json
python3 -B -m unittest discover -s tests -p test_capacity.py -v
python3 -B -m unittest discover -s tests -p test_reservations.py -v
```

The example models 2 GB Space and 1 GB Universe orders already pending against a 3 GB founder cap. It returns **0 sellable bytes** despite hypothetical 40 GB physical free space. Increasing only the app cap to 12 GB leaves **6 GB** under the assumed independent backup headroom. Those are scenario results, not current server measurements or approved new inventory.

## Inputs and accounting

All sizes are integer **decimal bytes**. The host must supply recent physical free bytes, an enforced service-volume quota and its current use, bytes already stored for committed orders, an unaccounted in-flight write peak, independently measured backup free bytes, and a verified restore time. A policy reserves a physical floor, restore scratch, local backup growth, metadata room, backup floor and backup growth. The example uses a 6:5 backup capacity ratio; that ratio must be measured for real data before adoption.

The calculator subtracts only the **unwritten** portion of existing whole-plan commitments, because stored object bytes are already reflected in volume use and physical free. `pending_write_peak_bytes` must exclude bytes already covered by those commitments or current use. The four gates are physical headroom, volume quota headroom, independent backup headroom and the founder app cap. The lowest governs admission. A Space checkout requires at least 2 GB remaining across **every** gate; Universe requires 1 GB. Missing, stale, impossible or same-device backup measurements raise an error and should stop new intake.

The `primary_device` and `backup_device` strings are a basic mismatch check, **not proof** of independent failure domains. A production adapter must derive those identities from trusted infrastructure, verify off-server restore, and fail closed when that evidence expires. The sample freshness limits are 30 seconds for metrics and 30 days for restore proof; they are policy examples, not a service-level promise.

## Atomic reservation reference

`cacheplus_reservations.ReservationLedger` stores an operator-supplied measurement/policy snapshot, a baseline of existing obligations, and full-plan order holds in one SQLite database. `reserve(order_id, plan, now_s)` takes an immediate write transaction, recalculates the quote including every pending and active order, then inserts a hold only if every gate passes. Identical retries return the same hold; a conflicting plan or reused released ID fails. A concurrency test sends eight simultaneous 2 GB requests into 6 GB of modelled headroom and accepts only three. Stale input prevents new holds. Activating a paid order keeps its hold. Releasing a hold requires an audit pointer, retains a tombstone and **must** follow independently verified deprovisioning or cancellation. The pointer alone is not proof.

The ledger is a **reference boundary**, not a production checkout service. Its SQLite transaction does not include an external payment/order database. A production implementation must put order creation and capacity reservation in the same database transaction, with trusted host snapshots captured after acquiring its admission lock. No customer-facing endpoint may expose `configure` or `release`. Tenant quotas, authentication, upload accounting, expiry reconciliation, observability, payment failure handling and restore drills remain required.

## Required integration before sales depend on this

1. Collect metrics on the **hosted** primary and backup systems, with observed timestamps and a verified hard quota. Use the volume containing actual workspace objects. Include temp uploads, database/WAL growth, backup copies, restore scratch and export grace in measured reserves.
2. In the **same database transaction** as creating an order, hold the full 2 GB or 1 GB plan commitment. Serialize concurrent checkouts, read fresh metrics after acquiring the admission lock, and make retry requests idempotent. The reference ledger demonstrates the reservation race but cannot make an external order database atomic.
3. Keep pending, paid and unresolved orders committed. Release a pending reservation only after provider reconciliation proves cancellation or expiry; never release it merely because a request timed out. Keep existing customer read/export access separate from a new-sale stop.
4. Exercise concurrent order races, backup loss, quota exhaustion, failed writes, recovery, refund and restore paths against an isolated copy. Measure actual billed gateway, disk, backup, traffic, support and refund costs before claiming margin.

With an illustrative RM1 payment cost and a 65% contribution target, RM12 Space leaves at most RM3.20 and RM8 Universe at most RM1.80 for all **other variable costs**. These are ceilings, not achieved margins. Fixed costs and tax are separate. The model changes neither price nor live capacity.

## Positioning

CachePlus should not claim to have invented caching or remote developer storage. [sccache](https://github.com/mozilla/sccache) already supports local and cloud compilation caches; [Bazel](https://bazel.build/remote/rbe) supports remote caching and execution; [Docker BuildKit](https://docs.docker.com/build/cache/backends) exports caches to several backends; [GitHub Actions](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manage-caches) manages cache quotas and eviction; [Depot Cache](https://depot.dev/docs/cache/overview) supports remote cache integrations. CachePlus can differentiate through an agent-facing disposable-work policy plus a hosted, expiring workspace **if** admission, restore and costs are measured and proven. That combination is a product direction, not a verified first-in-market claim.

The public 0.4.0 local-cache release is unchanged. The workspace connector and this capacity foundation live on a feature branch. No hosted gate is enabled by these modules.
