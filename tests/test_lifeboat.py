import gzip
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from lifeboat import crypto, drill, retention
from lifeboat.crypto import IntegrityError
from lifeboat.store import LocalStore

KEY = bytes(range(32))


class CryptoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.src = os.path.join(self.tmp, "plain")
        with open(self.src, "wb") as f:
            f.write(os.urandom(5000) + b"end of backup")

    def path(self, name):
        return os.path.join(self.tmp, name)

    def roundtrip(self, chunk):
        crypto.encrypt_file(self.src, self.path("enc"), KEY, chunk=chunk)
        crypto.decrypt_file(self.path("enc"), self.path("out"), KEY)
        with open(self.src, "rb") as a, open(self.path("out"), "rb") as b:
            self.assertEqual(a.read(), b.read())

    def test_roundtrip_single_and_many_chunks(self):
        self.roundtrip(1 << 20)
        self.roundtrip(512)  # 10 chunks
        self.roundtrip(5013)  # exactly one full chunk

    def test_flipped_byte_is_rejected(self):
        crypto.encrypt_file(self.src, self.path("enc"), KEY, chunk=512)
        data = bytearray(open(self.path("enc"), "rb").read())
        data[len(data) // 2] ^= 1
        open(self.path("enc"), "wb").write(data)
        with self.assertRaises(IntegrityError):
            crypto.decrypt_file(self.path("enc"), self.path("out"), KEY)

    def test_truncated_backup_is_rejected(self):
        crypto.encrypt_file(self.src, self.path("enc"), KEY, chunk=512)
        data = open(self.path("enc"), "rb").read()
        # cut after a complete chunk: every remaining chunk is valid, but the "last" flag is missing
        first_chunk_end = 16 + 4 + 512 + 16
        open(self.path("enc"), "wb").write(data[:first_chunk_end])
        with self.assertRaises(IntegrityError):
            crypto.decrypt_file(self.path("enc"), self.path("out"), KEY)

    def test_wrong_key_is_rejected(self):
        crypto.encrypt_file(self.src, self.path("enc"), KEY)
        with self.assertRaises(IntegrityError):
            crypto.decrypt_file(self.path("enc"), self.path("out"), bytes(32))

    def test_key_loading(self):
        self.assertEqual(len(crypto.load_key(crypto.new_key())), 32)
        with self.assertRaises(SystemExit):
            crypto.load_key("dG9vIHNob3J0")


class RetentionTest(unittest.TestCase):
    def test_gfs_keeps_7_daily_4_weekly_6_monthly(self):
        start = datetime(2026, 1, 1, 2, 0, tzinfo=timezone.utc)
        backups = [(f"b{i}", start + timedelta(days=i)) for i in range(300)]  # one per day for ~10 months
        keep, delete = retention.plan(backups)
        self.assertIn("b299", keep)
        self.assertIn("latest", keep["b299"])
        daily = [b for b, why in keep.items() if "daily" in why]
        self.assertEqual(len(daily), 7)
        self.assertEqual(len([b for b, why in keep.items() if "weekly" in why]), 4)
        self.assertEqual(len([b for b, why in keep.items() if "monthly" in why]), 6)
        self.assertLessEqual(len(keep), 17)
        self.assertEqual(len(keep) + len(delete), 300)

    def test_several_backups_a_day_keep_the_newest(self):
        day = datetime(2026, 10, 17, tzinfo=timezone.utc)
        keep, _ = retention.plan([("early", day.replace(hour=1)), ("late", day.replace(hour=23))])
        self.assertIn("late", keep)
        self.assertNotIn("early", keep)


class FakeDatabase:
    """Stands in for MariaDB: tables are dicts, a dump is gzipped JSON."""

    name = "shop"

    def __init__(self):
        self.tables = {}

    def wait_ready(self):
        pass

    def server_version(self):
        return "11.8.9-MariaDB (fake)"

    def dump(self, out_gz):
        raw = json.dumps(self.tables, sort_keys=True).encode()
        with gzip.open(out_gz, "wb") as f:
            f.write(raw)
        return len(raw)

    def restore(self, gz):
        with gzip.open(gz, "rb") as f:
            self.tables = json.loads(f.read())

    def fingerprint(self):
        import hashlib
        return {t: {"rows": len(rows), "sha256": hashlib.sha256(json.dumps(rows).encode()).hexdigest()} for t, rows in self.tables.items()}


def fake_seed(db):
    db.tables = {"customers": [[1, "Lena"], [2, "Noah"]], "orders": [[1, 1, 990]]}
    return {"customers": 2}


def fake_day(db, day, seed_value):
    db.tables["orders"].append([len(db.tables["orders"]) + 1, 2, 100 * seed_value])
    return {"new_orders": 1}


@mock.patch.object(drill.shop, "seed", fake_seed)
@mock.patch.object(drill.shop, "business_day", fake_day)
class DrillTest(unittest.TestCase):
    def setUp(self):
        self.store = LocalStore(tempfile.mkdtemp())

    def run_drill(self, db=None):
        db = db or FakeDatabase()
        ok = drill.drill(db, self.store, KEY)
        history = json.load(open(os.path.join(self.store.path, "history.json")))
        return ok, history[-1], db

    def test_genesis_then_restore_chain(self):
        ok, entry, _ = self.run_drill()
        self.assertTrue(ok)
        self.assertIsNone(entry["restore"])  # nothing to restore on the first night

        with mock.patch.object(drill, "utcnow", return_value=datetime.now(timezone.utc) + timedelta(days=1)):
            ok, entry, db = self.run_drill()  # next night, a NEW empty database: everything must come from the backup
        self.assertTrue(ok, entry.get("error"))
        self.assertEqual(entry["restore"]["rows"], 3)
        self.assertEqual(len(db.tables["orders"]), 2)  # restored 1 + one business day
        names = [s["name"] for s in entry["steps"]]
        self.assertIn("tamper test: a modified copy must be rejected", names)
        self.assertTrue(all(s["status"] == "ok" for s in entry["steps"]))
        catalog = json.load(open(os.path.join(self.store.path, "backups.json")))
        self.assertEqual(len(catalog), 2)

    def test_corrupted_backup_stops_the_chain(self):
        self.run_drill()
        enc = next(n for n in self.store.list() if n.endswith(".enc"))
        path = os.path.join(self.store.path, enc)
        data = bytearray(open(path, "rb").read())
        data[-5] ^= 1
        open(path, "wb").write(data)
        before = [n for n in self.store.list() if n.endswith(".enc")]

        ok, entry, _ = self.run_drill()
        self.assertFalse(ok)
        self.assertEqual(entry["status"], "failed")
        self.assertIn("sha256 mismatch", entry["error"])
        self.assertEqual([n for n in self.store.list() if n.endswith(".enc")], before, "no new backup from a failed restore")

    def test_data_that_does_not_match_the_manifest_fails_verification(self):
        self.run_drill()

        class LossyDatabase(FakeDatabase):
            def restore(self, gz):
                super().restore(gz)
                self.tables["orders"].pop()  # a restore that silently loses a row

        with mock.patch.object(drill, "utcnow", return_value=datetime.now(timezone.utc) + timedelta(seconds=2)):
            ok, entry, _ = self.run_drill(LossyDatabase())
        self.assertFalse(ok)
        self.assertIn("orders: rows 0 vs 1", entry["error"])


if __name__ == "__main__":
    unittest.main()
