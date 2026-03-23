from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from vmquota.api import lookup_snapshot
from vmquota.config import AppConfig
from vmquota.db import StateDB
from vmquota.models import ManagedVm
from vmquota.parsing import parse_vmid_ranges


class ApiTests(unittest.TestCase):
    def test_lookup_snapshot_by_uuid(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            config = AppConfig(
                path=Path(tempdir) / "config.toml",
                timezone_name="Asia/Shanghai",
                timezone=ZoneInfo("Asia/Shanghai"),
                state_db=Path(tempdir) / "state.sqlite",
                api_bind_host="127.0.0.1",
                api_bind_port=9527,
                enforce_shaping=False,
                auto_enroll=True,
                vmid_ranges=parse_vmid_ranges(["101-110"]),
                default_limit_bytes=2_000_000_000_000,
                default_throttle_bps=2_000_000,
            )
            with StateDB(config.state_db) as db:
                db.upsert_vm(
                    ManagedVm(
                        vmid=101,
                        bios_uuid="ABC-UUID-101",
                        name="vm101",
                        created_at=datetime(2026, 3, 23, 1, 0, tzinfo=timezone.utc),
                        updated_at=datetime(2026, 3, 23, 1, 0, tzinfo=timezone.utc),
                        last_seen_at=datetime(2026, 3, 23, 1, 0, tzinfo=timezone.utc),
                        anchor_day=23,
                        period_start=datetime(2026, 3, 22, 16, 0, tzinfo=timezone.utc),
                        next_reset_at=datetime(2026, 4, 22, 16, 0, tzinfo=timezone.utc),
                        limit_bytes=1000,
                        throttle_bps=2_000_000,
                        manual_throttle=False,
                        throttle_active=False,
                        total_bytes=100,
                        last_sync_at=datetime(2026, 3, 23, 1, 0, tzinfo=timezone.utc),
                    )
                )
            snapshot = lookup_snapshot(config, "abc-uuid-101")
            self.assertIsNotNone(snapshot)
            assert snapshot is not None
            self.assertEqual(snapshot["vmid"], 101)
            self.assertEqual(snapshot["usage_bytes"], 100)
            self.assertEqual(snapshot["usage_percent_text"], "10.00%")


if __name__ == "__main__":
    unittest.main()
