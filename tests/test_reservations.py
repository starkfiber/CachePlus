import copy
import json
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/cache/scripts"))
from cacheplus_capacity import CapacityError, Measurements, Policy  # noqa: E402
from cacheplus_reservations import ReservationLedger  # noqa: E402

BASE = json.loads((ROOT / "docs/capacity-example.json").read_text(encoding="utf-8"))
GB = 1_000_000_000


class ReservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ledger = ReservationLedger(Path(self.temp.name) / "orders.db")
        self.raw = copy.deepcopy(BASE)
        self.raw["policy"]["founder_cap_bytes"] = 12 * GB
        self.configure()

    def configure(self):
        return self.ledger.configure(Measurements.parse(self.raw["measurements"]),
                                     Policy.parse(self.raw["policy"]),
                                     self.raw["committed_bytes"], self.raw["now_s"])

    def test_concurrent_orders_cannot_oversell_backup(self):
        def attempt(number):
            try:
                return self.ledger.reserve(f"order-{number}", "Space", 1000)
            except CapacityError:
                return None

        with ThreadPoolExecutor(max_workers=8) as pool:
            receipts = list(pool.map(attempt, range(8)))
        self.assertEqual(sum(item is not None for item in receipts), 3)
        self.assertEqual(self.ledger.inspect(1000)["committed_bytes"], 9 * GB)
        self.assertEqual(self.ledger.inspect(1000)["sellable_bytes"], 0)

    def test_retry_is_idempotent_and_wrong_plan_rejected(self):
        first = self.ledger.reserve("same-order", "Universe", 1000)
        second = self.ledger.reserve("same-order", "Universe", 1001)
        self.assertFalse(first["idempotent"])
        self.assertTrue(second["idempotent"])
        self.assertEqual(self.ledger.inspect(1001)["committed_bytes"], 4 * GB)
        with self.assertRaises(CapacityError):
            self.ledger.reserve("same-order", "Space", 1001)

    def test_stale_metrics_stop_new_orders_but_preserve_retry(self):
        self.ledger.reserve("existing", "Universe", 1000)
        with self.assertRaises(CapacityError):
            self.ledger.reserve("new", "Universe", 1031)
        self.assertTrue(self.ledger.reserve("existing", "Universe", 1031)["idempotent"])
        self.assertEqual(self.ledger.inspect(1000)["committed_bytes"], 4 * GB)

    def test_config_refresh_cannot_erase_held_capacity(self):
        self.ledger.reserve("held", "Space", 1000)
        self.raw["policy"]["founder_cap_bytes"] = 4 * GB
        with self.assertRaises(CapacityError):
            self.configure()
        self.assertEqual(self.ledger.inspect(1000)["committed_bytes"], 5 * GB)

    def test_activate_and_release_keep_tombstone(self):
        self.ledger.reserve("paid", "Space", 1000)
        self.assertEqual(self.ledger.activate("paid", 1001), "active")
        with self.assertRaises(CapacityError):
            self.ledger.release("paid", 1002, "short")
        self.assertEqual(self.ledger.inspect(1002)["committed_bytes"], 5 * GB)
        self.assertEqual(self.ledger.release("paid", 1002, "deprovision:receipt-123"), "released")
        self.assertEqual(self.ledger.inspect(1002)["committed_bytes"], 3 * GB)
        with self.assertRaises(CapacityError):
            self.ledger.reserve("paid", "Space", 1002)

    def test_unconfigured_or_invalid_order_refused(self):
        empty = ReservationLedger(Path(self.temp.name) / "empty.db")
        with self.assertRaises(CapacityError):
            empty.reserve("order", "Universe", 1000)
        with self.assertRaises(CapacityError):
            self.ledger.reserve("order / weird", "Universe", 1000)


if __name__ == "__main__":
    unittest.main()
