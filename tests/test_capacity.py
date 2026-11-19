import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "plugins/cache/scripts/cacheplus_capacity.py"
sys.path.insert(0, str(SCRIPT.parent))
from cacheplus_capacity import CapacityError, Measurements, Policy, quote  # noqa: E402

BASE = json.loads((ROOT / "docs/capacity-example.json").read_text(encoding="utf-8"))
GB = 1_000_000_000


def evaluate(raw):
    return quote(Measurements.parse(raw["measurements"]), Policy.parse(raw["policy"]),
                 raw["committed_bytes"], raw["now_s"])


class CapacityTests(unittest.TestCase):
    def test_pending_founder_cap_stops_both_plans(self):
        answer = evaluate(BASE)
        self.assertEqual(answer["sellable_bytes"], 0)
        self.assertEqual(answer["limiting_gate"], "founder_cap")
        self.assertEqual(answer["admit"], {"Space": False, "Universe": False})

    def test_independent_backup_limits_hypothetical_expansion(self):
        raw = copy.deepcopy(BASE)
        raw["policy"]["founder_cap_bytes"] = 12 * GB
        answer = evaluate(raw)
        self.assertEqual(answer["sellable_bytes"], 6 * GB)
        self.assertEqual(answer["limiting_gate"], "independent_backup")
        self.assertEqual(answer["maximum_homogeneous_orders"], {"Space": 3, "Universe": 6})

    def test_backup_pressure_and_write_spike_stop_sale(self):
        for changed, value in (("backup_free_bytes", 8 * GB),
                               ("pending_write_peak_bytes", 8 * GB)):
            raw = copy.deepcopy(BASE)
            raw["policy"]["founder_cap_bytes"] = 12 * GB
            raw["measurements"][changed] = value
            self.assertFalse(any(evaluate(raw)["admit"].values()), changed)

    def test_physical_floor_and_restore_scratch_reduce_headroom(self):
        raw = copy.deepcopy(BASE)
        raw["policy"]["founder_cap_bytes"] = 12 * GB
        baseline = evaluate(raw)["headroom_bytes"]
        raw["policy"]["physical_floor_bytes"] += GB
        self.assertEqual(evaluate(raw)["headroom_bytes"]["physical"], baseline["physical"] - GB)
        raw["policy"]["restore_scratch_bytes"] += GB
        self.assertEqual(evaluate(raw)["headroom_bytes"]["volume_quota"], baseline["volume_quota"] - GB)

    def test_object_bytes_already_on_disk_not_reserved_twice(self):
        raw = copy.deepcopy(BASE)
        raw["policy"]["founder_cap_bytes"] = 12 * GB
        baseline = evaluate(raw)["headroom_bytes"]
        raw["measurements"]["object_used_bytes"] += GB
        raw["measurements"]["volume_used_bytes"] += GB
        raw["measurements"]["physical_free_bytes"] -= GB
        raw["measurements"]["backup_free_bytes"] -= 1_200_000_000
        self.assertEqual(evaluate(raw)["headroom_bytes"], baseline)

    def test_stale_metrics_or_restore_proof_fail_closed(self):
        for field, value in (("observed_at_s", 969), ("restore_verified_at_s", 0),
                             ("observed_at_s", 1001)):
            raw = copy.deepcopy(BASE)
            raw["measurements"][field] = value
            if field == "restore_verified_at_s":
                raw["now_s"] = 3_000_000
                raw["measurements"]["observed_at_s"] = 3_000_000
            with self.assertRaises(CapacityError):
                evaluate(raw)

    def test_same_failure_domain_or_missing_quota_fails_closed(self):
        raw = copy.deepcopy(BASE)
        raw["measurements"]["backup_device"] = raw["measurements"]["primary_device"]
        with self.assertRaises(CapacityError):
            evaluate(raw)
        raw = copy.deepcopy(BASE)
        raw["measurements"]["volume_quota_bytes"] = 0
        with self.assertRaises(CapacityError):
            evaluate(raw)

    def test_invalid_types_and_overcommit_rejected(self):
        for field, value in (("physical_free_bytes", True), ("backup_free_bytes", -1)):
            raw = copy.deepcopy(BASE)
            raw["measurements"][field] = value
            with self.assertRaises(CapacityError):
                evaluate(raw)
        raw = copy.deepcopy(BASE)
        raw["committed_bytes"] = 4 * GB
        with self.assertRaises(CapacityError):
            evaluate(raw)
        typed = Measurements(**{**BASE["measurements"], "physical_free_bytes": -1})
        with self.assertRaises(CapacityError):
            quote(typed, Policy.parse(BASE["policy"]), BASE["committed_bytes"], BASE["now_s"])

    def test_cli_outputs_quote_without_mutating_fixture(self):
        before = (ROOT / "docs/capacity-example.json").read_bytes()
        result = subprocess.run([sys.executable, "-B", str(SCRIPT),
                                 str(ROOT / "docs/capacity-example.json")],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["sellable_bytes"], 0)
        self.assertEqual((ROOT / "docs/capacity-example.json").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
