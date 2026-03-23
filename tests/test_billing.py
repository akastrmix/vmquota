from datetime import datetime, timezone
import unittest
from zoneinfo import ZoneInfo

from vmquota.billing import initial_cycle, manual_reanchor_cycle, next_anchor_after


class BillingTests(unittest.TestCase):
    def test_initial_cycle_uses_first_seen_day(self) -> None:
        now = datetime(2026, 3, 23, 4, 40, tzinfo=timezone.utc)
        anchor_day, period_start, next_reset = initial_cycle(now, ZoneInfo("Asia/Shanghai"))
        self.assertEqual(anchor_day, 23)
        self.assertEqual(period_start.isoformat(), "2026-03-22T16:00:00+00:00")
        self.assertEqual(next_reset.isoformat(), "2026-04-22T16:00:00+00:00")

    def test_manual_reanchor_creates_short_current_cycle(self) -> None:
        now = datetime(2026, 3, 23, 12, 0, tzinfo=timezone.utc)
        period_start, next_reset = manual_reanchor_cycle(now, 15, ZoneInfo("Asia/Shanghai"))
        self.assertEqual(period_start.isoformat(), "2026-03-22T16:00:00+00:00")
        self.assertEqual(next_reset.isoformat(), "2026-04-14T16:00:00+00:00")

    def test_next_anchor_handles_short_month(self) -> None:
        period_start = datetime(2026, 1, 30, 16, 0, tzinfo=timezone.utc)
        next_reset = next_anchor_after(period_start, 31, ZoneInfo("Asia/Shanghai"))
        self.assertEqual(next_reset.isoformat(), "2026-02-27T16:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
