from datetime import datetime, timezone
import unittest

from vmquota.models import ManagedVm, VmEvent
from vmquota.presentation import build_usage_snapshot, event_summary, progress_bar, render_usage_brief, usage_progress
from zoneinfo import ZoneInfo


def sample_vm() -> ManagedVm:
    return ManagedVm(
        vmid=101,
        bios_uuid="uuid-101",
        name="vm101",
        created_at=datetime(2026, 3, 23, 1, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 3, 23, 1, 0, tzinfo=timezone.utc),
        last_seen_at=datetime(2026, 3, 23, 1, 0, tzinfo=timezone.utc),
        anchor_day=23,
        period_start=datetime(2026, 3, 22, 16, 0, tzinfo=timezone.utc),
        next_reset_at=datetime(2026, 4, 22, 16, 0, tzinfo=timezone.utc),
        limit_bytes=200,
        throttle_bps=2_000_000,
        manual_throttle=False,
        throttle_active=False,
        total_bytes=50,
        last_sync_at=datetime(2026, 3, 23, 1, 0, tzinfo=timezone.utc),
    )


class PresentationTests(unittest.TestCase):
    def test_progress_bar(self) -> None:
        self.assertEqual(progress_bar(0.25, width=10), "[##--------]")
        self.assertEqual(usage_progress(sample_vm(), width=10), "[##--------] 25.00%")

    def test_snapshot_uses_local_timezone(self) -> None:
        snapshot = build_usage_snapshot(sample_vm(), ZoneInfo("Asia/Shanghai"))
        self.assertEqual(snapshot["remaining_bytes"], 150)
        self.assertEqual(snapshot["next_reset_at"], "2026-04-23 00:00:00+08:00")
        self.assertEqual(render_usage_brief(snapshot), "50\t200\t25.000000\tnormal")

    def test_event_summary_keeps_recent_events_compact(self) -> None:
        event = VmEvent(
            vmid=101,
            bios_uuid="uuid-101",
            ts=datetime(2026, 3, 24, 1, 0, tzinfo=timezone.utc),
            kind="set",
            message="Updated VM policy",
            details={
                "old": {"limit_bytes": 200, "throttle_bps": 1_000_000, "anchor_day": 23},
                "new": {"limit_bytes": 300, "throttle_bps": 2_000_000, "anchor_day": 24},
            },
        )

        self.assertEqual(
            event_summary(event),
            "limit 200 B -> 300 B, throttle 1.00 mbit/s -> 2.00 mbit/s, reset day 23 -> 24",
        )

        reset_event = VmEvent(
            vmid=101,
            bios_uuid="uuid-101",
            ts=datetime(2026, 3, 24, 1, 0, tzinfo=timezone.utc),
            kind="reset",
            message="Reset VM usage",
            details={"mode": "usage-only", "cleared_total_bytes": 1024},
        )

        self.assertEqual(event_summary(reset_event), "cleared 1.02 KB, mode=usage-only")


if __name__ == "__main__":
    unittest.main()
