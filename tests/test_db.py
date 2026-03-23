from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from vmquota.db import StateDB
from vmquota.models import VmEvent


class DbTests(unittest.TestCase):
    def test_connection_uses_busy_timeout_and_wal(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            with StateDB(Path(tempdir) / "state.sqlite") as db:
                journal_mode = db.conn.execute("PRAGMA journal_mode").fetchone()[0]
                busy_timeout = db.conn.execute("PRAGMA busy_timeout").fetchone()[0]
                self.assertEqual(journal_mode.lower(), "wal")
                self.assertEqual(busy_timeout, 30000)

    def test_recent_events_return_typed_event_objects(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            with StateDB(Path(tempdir) / "state.sqlite") as db:
                db.add_event(
                    VmEvent(
                        vmid=101,
                        bios_uuid="uuid-101",
                        ts=datetime(2026, 3, 24, 1, 0, tzinfo=timezone.utc),
                        kind="reset",
                        message="Reset VM usage",
                        details={"anchor_day": 24},
                    )
                )
                events = db.recent_events(101)
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0].kind, "reset")
                self.assertEqual(events[0].details, {"anchor_day": 24})

    def test_recent_events_tolerate_legacy_non_json_details(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            with StateDB(Path(tempdir) / "state.sqlite") as db:
                db.conn.execute(
                    "INSERT INTO events (vmid, bios_uuid, ts, kind, message, details) VALUES (?, ?, ?, ?, ?, ?)",
                    (101, "uuid-101", datetime(2026, 3, 24, 1, 0, tzinfo=timezone.utc).isoformat(), "reset", "Reset VM usage", "not-json"),
                )
                db.conn.commit()
                events = db.recent_events(101)
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0].details, {"raw": "not-json"})


if __name__ == "__main__":
    unittest.main()
