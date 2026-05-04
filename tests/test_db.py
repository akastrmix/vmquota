from datetime import datetime, timezone
import json
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

    def test_recent_events_reject_invalid_details(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            with StateDB(Path(tempdir) / "state.sqlite") as db:
                db.conn.execute(
                    "INSERT INTO events (vmid, bios_uuid, ts, kind, message, details) VALUES (?, ?, ?, ?, ?, ?)",
                    (101, "uuid-101", datetime(2026, 3, 24, 1, 0, tzinfo=timezone.utc).isoformat(), "reset", "Reset VM usage", "not-json"),
                )
                db.conn.commit()
                with self.assertRaises(json.JSONDecodeError):
                    db.recent_events(101)

    def test_get_vm_rejects_invalid_anchor_day(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            with StateDB(Path(tempdir) / "state.sqlite") as db:
                now = datetime(2026, 3, 24, 1, 0, tzinfo=timezone.utc).isoformat()
                db.conn.execute(
                    """
                    INSERT INTO managed_vms (
                        vmid, bios_uuid, name, created_at, updated_at, last_seen_at,
                        anchor_day, period_start, next_reset_at, limit_bytes,
                        throttle_bps, manual_throttle, throttle_active, total_bytes, last_sync_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (101, "uuid-101", "vm101", now, now, now, 99, now, now, 1000, 2_000_000, 0, 0, 0, None),
                )
                db.conn.commit()

                with self.assertRaisesRegex(ValueError, "anchor day must be between 1 and 31"):
                    db.get_vm(101)

    def test_get_vm_rejects_invalid_boolean_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            with StateDB(Path(tempdir) / "state.sqlite") as db:
                now = datetime(2026, 3, 24, 1, 0, tzinfo=timezone.utc).isoformat()
                db.conn.execute(
                    """
                    INSERT INTO managed_vms (
                        vmid, bios_uuid, name, created_at, updated_at, last_seen_at,
                        anchor_day, period_start, next_reset_at, limit_bytes,
                        throttle_bps, manual_throttle, throttle_active, total_bytes, last_sync_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (101, "uuid-101", "vm101", now, now, now, 24, now, now, 1000, 2_000_000, 2, 0, 0, None),
                )
                db.conn.commit()

                with self.assertRaisesRegex(ValueError, "manual_throttle must be 0 or 1"):
                    db.get_vm(101)


if __name__ == "__main__":
    unittest.main()
