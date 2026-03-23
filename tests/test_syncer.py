from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from vmquota.config import AppConfig
from vmquota.db import StateDB
from vmquota.models import NicConfig, VmInfo
from vmquota.parsing import parse_vmid_ranges
from vmquota.syncer import VmQuotaService


class FakeInspector:
    def __init__(self) -> None:
        self.vm = VmInfo(
            vmid=101,
            name="vm101",
            status="running",
            bios_uuid="uuid-101",
            tags=(),
            template=False,
            nics=(NicConfig(index=0, bridge="vmbr1", firewall=True, mac=None, model="virtio", raw=""),),
        )

    def discover_vms(self) -> list[VmInfo]:
        return [self.vm]

    def get_vm(self, vmid: int) -> VmInfo | None:
        return self.vm if vmid == 101 else None

    def existing_interfaces(self) -> set[str]:
        return {"tap101i0", "fwpr101p0"}

    def read_interface_counters(self, device: str) -> tuple[int, int]:
        return (1000, 2000)


class MultiFakeInspector(FakeInspector):
    def __init__(self) -> None:
        super().__init__()
        self.vm2 = VmInfo(
            vmid=102,
            name="vm102",
            status="running",
            bios_uuid="uuid-102",
            tags=(),
            template=False,
            nics=(NicConfig(index=0, bridge="vmbr1", firewall=True, mac=None, model="virtio", raw=""),),
        )

    def discover_vms(self) -> list[VmInfo]:
        return [self.vm, self.vm2]

    def get_vm(self, vmid: int) -> VmInfo | None:
        if vmid == 101:
            return self.vm
        if vmid == 102:
            return self.vm2
        return None


class FakeShaper:
    def __init__(self) -> None:
        self.actions: list[tuple[str, int]] = []
        self.active: set[int] = set()

    def apply(self, vmid: int, plan, rate_bps: int) -> None:
        self.actions.append(("apply", vmid))
        self.active.add(vmid)

    def clear(self, vmid: int, plan) -> None:
        self.actions.append(("clear", vmid))
        self.active.discard(vmid)

    def is_applied(self, vmid: int, plan, rate_bps: int) -> bool:
        return vmid in self.active


class FailingApplyShaper(FakeShaper):
    def apply(self, vmid: int, plan, rate_bps: int) -> None:
        raise RuntimeError("boom")


class SyncerTests(unittest.TestCase):
    def test_reset_usage_only_preserves_cycle(self) -> None:
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
            db = StateDB(config.state_db)
            try:
                service = VmQuotaService(config=config, db=db, inspector=FakeInspector(), shaper=FakeShaper())
                now = datetime(2026, 3, 23, 4, 40, tzinfo=timezone.utc)
                service.sync(now=now)
                original = db.get_vm(101)
                self.assertIsNotNone(original)
                assert original is not None
                original.total_bytes = 12345
                db.upsert_vm(original)

                reset_at = datetime(2026, 3, 25, 1, 0, tzinfo=timezone.utc)
                updated = service.reset_vm(101, usage_only=True, now=reset_at)

                self.assertEqual(updated.total_bytes, 0)
                self.assertEqual(updated.period_start, original.period_start)
                self.assertEqual(updated.next_reset_at, original.next_reset_at)
            finally:
                db.close()

    def test_sync_clears_throttle_when_vm_is_back_under_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            config = AppConfig(
                path=Path(tempdir) / "config.toml",
                timezone_name="Asia/Shanghai",
                timezone=ZoneInfo("Asia/Shanghai"),
                state_db=Path(tempdir) / "state.sqlite",
                api_bind_host="127.0.0.1",
                api_bind_port=9527,
                enforce_shaping=True,
                auto_enroll=True,
                vmid_ranges=parse_vmid_ranges(["101-110"]),
                default_limit_bytes=2_000_000_000_000,
                default_throttle_bps=2_000_000,
            )
            db = StateDB(config.state_db)
            shaper = FakeShaper()
            try:
                service = VmQuotaService(config=config, db=db, inspector=FakeInspector(), shaper=shaper)
                now = datetime(2026, 3, 23, 4, 40, tzinfo=timezone.utc)
                service.sync(now=now)
                record = db.get_vm(101)
                self.assertIsNotNone(record)
                assert record is not None
                record.throttle_active = True
                record.total_bytes = 1024
                db.upsert_vm(record)
                shaper.active.add(101)

                later = datetime(2026, 3, 23, 5, 0, tzinfo=timezone.utc)
                service.sync(now=later)

                updated = db.get_vm(101)
                self.assertIsNotNone(updated)
                assert updated is not None
                self.assertFalse(updated.throttle_active)
                self.assertIn(("clear", 101), shaper.actions)
            finally:
                db.close()

    def test_set_range_updates_existing_vms_and_skips_missing(self) -> None:
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
            db = StateDB(config.state_db)
            try:
                service = VmQuotaService(config=config, db=db, inspector=MultiFakeInspector(), shaper=FakeShaper())
                updated, skipped = service.set_range("101-103", limit_bytes=500_000_000_000)
                self.assertEqual([vm.vmid for vm in updated], [101, 102])
                self.assertEqual(skipped, [103])
                self.assertEqual(db.get_vm(101).limit_bytes, 500_000_000_000)
                self.assertEqual(db.get_vm(102).limit_bytes, 500_000_000_000)
            finally:
                db.close()

    def test_manual_throttle_persists_when_vm_is_not_over_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            config = AppConfig(
                path=Path(tempdir) / "config.toml",
                timezone_name="Asia/Shanghai",
                timezone=ZoneInfo("Asia/Shanghai"),
                state_db=Path(tempdir) / "state.sqlite",
                api_bind_host="127.0.0.1",
                api_bind_port=9527,
                enforce_shaping=True,
                auto_enroll=True,
                vmid_ranges=parse_vmid_ranges(["101-110"]),
                default_limit_bytes=2_000_000_000_000,
                default_throttle_bps=2_000_000,
            )
            db = StateDB(config.state_db)
            shaper = FakeShaper()
            try:
                service = VmQuotaService(config=config, db=db, inspector=FakeInspector(), shaper=shaper)
                now = datetime(2026, 3, 23, 4, 40, tzinfo=timezone.utc)
                service.sync(now=now)
                service.throttle_vm(101, "apply", now=now)

                later = datetime(2026, 3, 23, 5, 0, tzinfo=timezone.utc)
                service.sync(now=later)

                updated = db.get_vm(101)
                self.assertIsNotNone(updated)
                assert updated is not None
                self.assertTrue(updated.manual_throttle)
                self.assertTrue(updated.throttle_active)
                self.assertIn(("apply", 101), shaper.actions)
                self.assertNotIn(("clear", 101), shaper.actions)
            finally:
                db.close()

    def test_failed_sync_does_not_advance_counter_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            config = AppConfig(
                path=Path(tempdir) / "config.toml",
                timezone_name="Asia/Shanghai",
                timezone=ZoneInfo("Asia/Shanghai"),
                state_db=Path(tempdir) / "state.sqlite",
                api_bind_host="127.0.0.1",
                api_bind_port=9527,
                enforce_shaping=True,
                auto_enroll=True,
                vmid_ranges=parse_vmid_ranges(["101-110"]),
                default_limit_bytes=2_000_000_000_000,
                default_throttle_bps=2_000_000,
            )
            db = StateDB(config.state_db)
            try:
                service = VmQuotaService(config=config, db=db, inspector=FakeInspector(), shaper=FakeShaper())
                now = datetime(2026, 3, 23, 4, 40, tzinfo=timezone.utc)
                service.sync(now=now)
                record = db.get_vm(101)
                self.assertIsNotNone(record)
                assert record is not None
                record.total_bytes = 100
                record.limit_bytes = 1
                db.upsert_vm(record)
                db.set_counter(101, "tap101i0", 10, 20, now)

                failing = VmQuotaService(config=config, db=db, inspector=FakeInspector(), shaper=FailingApplyShaper())
                with self.assertRaises(RuntimeError):
                    failing.sync(now=datetime(2026, 3, 23, 5, 0, tzinfo=timezone.utc))

                persisted = db.get_vm(101)
                self.assertIsNotNone(persisted)
                assert persisted is not None
                self.assertEqual(persisted.total_bytes, 100)
                self.assertEqual(db.get_counters(101)["tap101i0"], (10, 20))
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
