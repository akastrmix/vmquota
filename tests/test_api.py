from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
import http.client
from pathlib import Path
import threading
import tempfile
import unittest
from zoneinfo import ZoneInfo

from vmquota.access_log import read_access_log
from vmquota.api import lookup_snapshot, make_handler
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
                api_access_log=Path(tempdir) / "api-access.jsonl",
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

    def test_usage_endpoint_rejects_duplicate_uuid_query(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            config = AppConfig(
                path=Path(tempdir) / "config.toml",
                timezone_name="Asia/Shanghai",
                timezone=ZoneInfo("Asia/Shanghai"),
                state_db=Path(tempdir) / "state.sqlite",
                api_bind_host="127.0.0.1",
                api_bind_port=0,
                enforce_shaping=False,
                auto_enroll=True,
                vmid_ranges=parse_vmid_ranges(["101-110"]),
                default_limit_bytes=2_000_000_000_000,
                default_throttle_bps=2_000_000,
                api_access_log=Path(tempdir) / "api-access.jsonl",
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(config))
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
                conn.request("GET", "/v1/usage?uuid=a&uuid=b")
                response = conn.getresponse()
                body = response.read().decode("utf-8")
                conn.close()

                self.assertEqual(response.status, 400)
                self.assertIn("duplicate uuid", body)

                conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
                conn.request("GET", "/v1/usage?uuid=a&uuid=")
                response = conn.getresponse()
                body = response.read().decode("utf-8")
                conn.close()

                self.assertEqual(response.status, 400)
                self.assertIn("duplicate uuid", body)
            finally:
                server.shutdown()
                thread.join()
                server.server_close()

    def test_usage_endpoint_records_access_log(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            config = AppConfig(
                path=Path(tempdir) / "config.toml",
                timezone_name="Asia/Shanghai",
                timezone=ZoneInfo("Asia/Shanghai"),
                state_db=Path(tempdir) / "state.sqlite",
                api_bind_host="127.0.0.1",
                api_bind_port=0,
                enforce_shaping=False,
                auto_enroll=True,
                vmid_ranges=parse_vmid_ranges(["101-110"]),
                default_limit_bytes=2_000_000_000_000,
                default_throttle_bps=2_000_000,
                api_access_log=Path(tempdir) / "api-access.jsonl",
                api_access_log_max_entries=10,
            )
            with StateDB(config.state_db) as db:
                db.upsert_vm(
                    ManagedVm(
                        vmid=101,
                        bios_uuid="uuid-101",
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
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(config))
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
                conn.request("GET", "/v1/usage/brief?uuid=uuid-101")
                response = conn.getresponse()
                response.read()
                conn.close()

                conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
                conn.request("GET", "/v1/usage?uuid=missing")
                response = conn.getresponse()
                response.read()
                conn.close()

                entries = read_access_log(config.api_access_log, limit=10)
                self.assertEqual(len(entries), 2)
                self.assertEqual(entries[0]["path"], "/v1/usage/brief")
                self.assertEqual(entries[0]["uuid"], "uuid-101")
                self.assertEqual(entries[0]["status"], 200)
                self.assertEqual(entries[0]["vmid"], 101)
                self.assertEqual(entries[1]["status"], 404)
                self.assertIsNone(entries[1]["vmid"])
            finally:
                server.shutdown()
                thread.join()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
