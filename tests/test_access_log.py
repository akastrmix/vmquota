from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from vmquota.access_log import append_access_log, read_access_log


class AccessLogTests(unittest.TestCase):
    def test_append_access_log_trims_to_max_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "api-access.jsonl"
            for idx in range(3):
                append_access_log(
                    path,
                    max_entries=2,
                    ts=datetime(2026, 3, 24, 1, idx, tzinfo=timezone.utc),
                    request_path="/v1/usage/brief",
                    uuid=f"uuid-{idx}",
                    status=200,
                    vmid=101 + idx,
                )

            entries = read_access_log(path, limit=10)

            self.assertEqual(len(entries), 2)
            self.assertEqual(entries[0]["uuid"], "uuid-1")
            self.assertEqual(entries[1]["uuid"], "uuid-2")
            self.assertEqual(list(Path(tempdir).glob("*.tmp")), [])

    def test_access_log_can_be_disabled_with_zero_max_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "api-access.jsonl"

            append_access_log(
                path,
                max_entries=0,
                ts=datetime(2026, 3, 24, 1, 0, tzinfo=timezone.utc),
                request_path="/v1/usage",
                uuid="uuid-101",
                status=200,
                vmid=101,
            )

            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
