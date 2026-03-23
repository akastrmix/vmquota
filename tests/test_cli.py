from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from vmquota.cli import main
from vmquota.config import AppConfig
from vmquota.models import ManagedVm, VmEvent
from vmquota.parsing import parse_vmid_ranges


def sample_vm() -> ManagedVm:
    return ManagedVm(
        vmid=101,
        bios_uuid="uuid-101",
        name="vm101",
        created_at=datetime(2026, 3, 23, 1, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 3, 24, 1, 0, tzinfo=timezone.utc),
        last_seen_at=datetime(2026, 3, 24, 1, 0, tzinfo=timezone.utc),
        anchor_day=23,
        period_start=datetime(2026, 3, 22, 16, 0, tzinfo=timezone.utc),
        next_reset_at=datetime(2026, 4, 22, 16, 0, tzinfo=timezone.utc),
        limit_bytes=200,
        throttle_bps=2_000_000,
        manual_throttle=False,
        throttle_active=False,
        total_bytes=50,
        last_sync_at=datetime(2026, 3, 24, 1, 0, tzinfo=timezone.utc),
    )


class FakeService:
    def __init__(self) -> None:
        self.vm = sample_vm()

    def sync(self) -> list[str]:
        return ["VM 101: throttle applied at 2000000 bps"]

    def list_vms(self) -> list[ManagedVm]:
        return [self.vm]

    def show_vm(self, vmid: int) -> tuple[ManagedVm, list[VmEvent]]:
        assert vmid == 101
        return self.vm, [
            VmEvent(
                vmid=101,
                bios_uuid="uuid-101",
                ts=datetime(2026, 3, 24, 1, 0, tzinfo=timezone.utc),
                kind="reset",
                message="Reset VM usage",
                details={"anchor_day": 23},
            )
        ]

    def set_range(self, vmid_range: str, *, limit_bytes=None, throttle_bps=None, anchor_day=None):
        assert vmid_range == "101-103"
        return [self.vm], [102, 103]


class FailingShowService(FakeService):
    def show_vm(self, vmid: int) -> tuple[ManagedVm, list[VmEvent]]:
        raise ValueError(f"VM {vmid} is not enrolled")


class FailingSyncService(FakeService):
    def sync(self) -> list[str]:
        raise RuntimeError("failed to list VM statuses")


class FakeStateDB:
    def __init__(self, path: Path) -> None:
        self.path = path

    def close(self) -> None:
        return


class CliTests(unittest.TestCase):
    def test_show_json_output_includes_recent_events(self) -> None:
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
            output = StringIO()
            with (
                patch("vmquota.cli.load_config", return_value=config),
                patch("vmquota.cli.StateDB", FakeStateDB),
                patch("vmquota.cli.VmQuotaService", return_value=FakeService()),
                redirect_stdout(output),
            ):
                exit_code = main(["--json", "show", "101"])
            self.assertEqual(exit_code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["vmid"], 101)
            self.assertEqual(payload["recent_events"][0]["kind"], "reset")
            self.assertEqual(payload["recent_events"][0]["details"], {"anchor_day": 23})

    def test_set_range_json_output_includes_skipped_vmids(self) -> None:
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
            output = StringIO()
            with (
                patch("vmquota.cli.load_config", return_value=config),
                patch("vmquota.cli.StateDB", FakeStateDB),
                patch("vmquota.cli.VmQuotaService", return_value=FakeService()),
                redirect_stdout(output),
            ):
                exit_code = main(["--json", "set-range", "101-103", "--limit", "2TB"])
            self.assertEqual(exit_code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["updated_count"], 1)
            self.assertEqual(payload["skipped"], [102, 103])

    def test_show_json_error_output_is_machine_readable(self) -> None:
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
            output = StringIO()
            with (
                patch("vmquota.cli.load_config", return_value=config),
                patch("vmquota.cli.StateDB", FakeStateDB),
                patch("vmquota.cli.VmQuotaService", return_value=FailingShowService()),
                redirect_stdout(output),
            ):
                exit_code = main(["show", "999", "--json"])
            self.assertEqual(exit_code, 1)
            self.assertEqual(json.loads(output.getvalue()), {"error": "VM 999 is not enrolled"})

    def test_sync_json_error_output_is_machine_readable(self) -> None:
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
            output = StringIO()
            with (
                patch("vmquota.cli.load_config", return_value=config),
                patch("vmquota.cli.StateDB", FakeStateDB),
                patch("vmquota.cli.VmQuotaService", return_value=FailingSyncService()),
                redirect_stdout(output),
            ):
                exit_code = main(["sync", "--json"])
            self.assertEqual(exit_code, 1)
            self.assertEqual(json.loads(output.getvalue()), {"error": "failed to list VM statuses"})


if __name__ == "__main__":
    unittest.main()
