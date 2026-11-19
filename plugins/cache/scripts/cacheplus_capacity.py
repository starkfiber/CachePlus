"""Offline, fail-closed capacity quotes for hosted Cache++ workspaces.

This module does not read host metrics, reserve orders, or alter the live service.
The host adapter must supply fresh measurements and commit reservations atomically
with its order rows before using a quote to admit a checkout.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, fields
from pathlib import Path

GB = 1_000_000_000
PLAN_BYTES = {"Space": 2 * GB, "Universe": GB}


class CapacityError(ValueError):
    """A required safety measurement or invariant is missing."""


def _parse_record(cls, raw):
    if not isinstance(raw, dict) or set(raw) != {item.name for item in fields(cls)}:
        raise CapacityError("Capacity input fields are missing or unexpected")
    for name, value in raw.items():
        if name in {"primary_device", "backup_device"}:
            if not isinstance(value, str) or not value.strip() or len(value) > 128:
                raise CapacityError(f"Invalid {name}")
        elif type(value) is not int or value < 0:
            raise CapacityError(f"Invalid {name}; use nonnegative integer bytes or seconds")
    return cls(**raw)


@dataclass(frozen=True)
class Measurements:
    observed_at_s: int
    restore_verified_at_s: int
    primary_device: str
    backup_device: str
    physical_free_bytes: int
    volume_quota_bytes: int
    volume_used_bytes: int
    object_used_bytes: int
    pending_write_peak_bytes: int
    backup_free_bytes: int

    @classmethod
    def parse(cls, raw):
        return _parse_record(cls, raw)


@dataclass(frozen=True)
class Policy:
    founder_cap_bytes: int
    physical_floor_bytes: int
    restore_scratch_bytes: int
    local_backup_growth_bytes: int
    metadata_buffer_bytes: int
    backup_floor_bytes: int
    backup_growth_bytes: int
    backup_ratio_numerator: int
    backup_ratio_denominator: int
    max_observation_age_s: int
    max_restore_age_s: int

    @classmethod
    def parse(cls, raw):
        item = _parse_record(cls, raw)
        if (item.backup_ratio_denominator == 0 or
                item.backup_ratio_numerator < item.backup_ratio_denominator or
                item.max_observation_age_s == 0 or item.max_restore_age_s == 0):
            raise CapacityError("Backup ratio and freshness limits must be positive")
        return item


def quote(measurements: Measurements, policy: Policy, committed_bytes: int, now_s: int):
    """Return a conservative quote; never reserve or admit an order itself."""
    if not isinstance(measurements, Measurements) or not isinstance(policy, Policy):
        raise CapacityError("Expected typed measurements and policy")
    m = Measurements.parse(vars(measurements))
    p = Policy.parse(vars(policy))
    if type(committed_bytes) is not int or committed_bytes < 0 or type(now_s) is not int or now_s < 0:
        raise CapacityError("Commitment and clock must be nonnegative integers")
    if (m.observed_at_s > now_s or now_s - m.observed_at_s > p.max_observation_age_s or
            m.restore_verified_at_s > now_s or
            now_s - m.restore_verified_at_s > p.max_restore_age_s):
        raise CapacityError("Current host metrics and independent restore proof are required")
    if m.primary_device == m.backup_device:
        raise CapacityError("Backup capacity must come from a separate failure domain")
    if (m.volume_quota_bytes == 0 or m.volume_used_bytes > m.volume_quota_bytes or
            m.object_used_bytes > m.volume_used_bytes or m.object_used_bytes > committed_bytes or
            committed_bytes > p.founder_cap_bytes):
        raise CapacityError("Existing use or commitments violate the declared limits")

    unwritten = committed_bytes - m.object_used_bytes
    primary = (m.physical_free_bytes - p.physical_floor_bytes - p.restore_scratch_bytes
               - p.local_backup_growth_bytes - p.metadata_buffer_bytes
               - m.pending_write_peak_bytes - unwritten)
    volume = (m.volume_quota_bytes - m.volume_used_bytes - p.restore_scratch_bytes
              - p.metadata_buffer_bytes - m.pending_write_peak_bytes - unwritten)
    backup = ((m.backup_free_bytes - p.backup_floor_bytes - p.backup_growth_bytes)
              * p.backup_ratio_denominator // p.backup_ratio_numerator
              - m.pending_write_peak_bytes - unwritten)
    founder = p.founder_cap_bytes - committed_bytes
    headroom = {"physical": primary, "volume_quota": volume,
                "independent_backup": backup, "founder_cap": founder}
    limiting = min(headroom, key=headroom.get)
    sellable = max(0, headroom[limiting])
    return {"headroom_bytes": headroom, "sellable_bytes": sellable,
            "limiting_gate": limiting,
            "admit": {name: sellable >= size for name, size in PLAN_BYTES.items()},
            "maximum_homogeneous_orders": {name: sellable // size
                                           for name, size in PLAN_BYTES.items()},
            "measurement_age_s": now_s - m.observed_at_s,
            "restore_proof_age_s": now_s - m.restore_verified_at_s,
            "warning": "Quote only. An order must reserve capacity atomically with current measurements."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", type=Path, help="Synthetic JSON input, never a live admission")
    args = parser.parse_args(argv)
    try:
        raw = json.loads(args.scenario.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or set(raw) != {"measurements", "policy", "committed_bytes", "now_s"}:
            raise CapacityError("Expected measurements, policy, committed_bytes and now_s")
        result = quote(Measurements.parse(raw["measurements"]), Policy.parse(raw["policy"]),
                       raw["committed_bytes"], raw["now_s"])
    except (OSError, ValueError, TypeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
