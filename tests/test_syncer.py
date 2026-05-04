from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from vmquota.config import AppConfig
from vmquota.db import StateDB
from vmquota.models import NicConfig, VmInfo
from vmquota.parsing import parse_vmid_ranges
from vmquota.pve import PveInspector
from vmquota.system import CommandResult
from vmquota.syncer import VmQuotaService


class FakeInspector:
    def __init__(self) -> None:
        self.interfaces = {"tap101i0", "fwpr101p0"}
        self.vm = VmInfo(
            vmid=101,
            name="vm101",
            status="running",
            bios_uuid="uuid-101",
            tags=(),
            template=False,
            nics=(NicConfig(index=0, bridge="vmbr1", firewall=True, mac=None, model="virtio", raw=""),),
        )

    def discover_vms(self, vmid_filter=None) -> list[VmInfo]:
        vms = [self.vm]
        if vmid_filter is not None:
            return [vm for vm in vms if vmid_filter(vm.vmid)]
        return vms

    def get_vm(self, vmid: int) -> VmInfo | None:
        return self.vm if vmid == 101 else None

    def existing_interfaces(self) -> set[str]:
        return set(self.interfaces)

    def read_interface_counters(self, device: str) -> tuple[int, int]:
        return (1000, 2000)


class StoppedFakeInspector(FakeInspector):
    def __init__(self) -> None:
        super().__init__()
        self.vm = VmInfo(
            vmid=101,
            name="vm101",
            status="stopped",
            bios_uuid="uuid-101",
            tags=(),
            template=False,
            nics=(NicConfig(index=0, bridge="vmbr1", firewall=True, mac=None, model="virtio", raw=""),),
        )

    def existing_interfaces(self) -> set[str]:
        return set()


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

    def discover_vms(self, vmid_filter=None) -> list[VmInfo]:
        vms = [self.vm, self.vm2]
        if vmid_filter is not None:
            return [vm for vm in vms if vmid_filter(vm.vmid)]
        return vms

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


class FailingClearShaper(FakeShaper):
    def clear(self, vmid: int, plan) -> None:
        raise RuntimeError("clear failed")

    def is_applied(self, vmid: int, plan, rate_bps: int) -> bool:
        return True


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

    def test_sync_does_not_report_throttled_until_all_hooks_exist(self) -> None:
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
            inspector = FakeInspector()
            shaper = FakeShaper()
            try:
                service = VmQuotaService(config=config, db=db, inspector=inspector, shaper=shaper)
                now = datetime(2026, 3, 23, 4, 40, tzinfo=timezone.utc)
                service.sync(now=now)
                record = db.get_vm(101)
                self.assertIsNotNone(record)
                assert record is not None
                record.total_bytes = 2
                record.limit_bytes = 1
                db.upsert_vm(record)

                inspector.interfaces = {"tap101i0"}
                service.sync(now=datetime(2026, 3, 23, 5, 0, tzinfo=timezone.utc))

                missing_hooks = db.get_vm(101)
                self.assertIsNotNone(missing_hooks)
                assert missing_hooks is not None
                self.assertFalse(missing_hooks.throttle_active)
                self.assertNotIn(("apply", 101), shaper.actions)

                inspector.interfaces = {"tap101i0", "fwpr101p0"}
                service.sync(now=datetime(2026, 3, 23, 5, 1, tzinfo=timezone.utc))

                ready = db.get_vm(101)
                self.assertIsNotNone(ready)
                assert ready is not None
                self.assertTrue(ready.throttle_active)
                self.assertIn(("apply", 101), shaper.actions)
            finally:
                db.close()

    def test_manual_throttle_waits_for_complete_traffic_plan(self) -> None:
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
            inspector = FakeInspector()
            shaper = FakeShaper()
            try:
                service = VmQuotaService(config=config, db=db, inspector=inspector, shaper=shaper)
                now = datetime(2026, 3, 23, 4, 40, tzinfo=timezone.utc)
                service.sync(now=now)

                inspector.interfaces = {"tap101i0"}
                updated = service.throttle_vm(101, "apply", now=datetime(2026, 3, 23, 5, 0, tzinfo=timezone.utc))

                self.assertTrue(updated.manual_throttle)
                self.assertFalse(updated.throttle_active)
                self.assertNotIn(("apply", 101), shaper.actions)

                inspector.interfaces = {"tap101i0", "fwpr101p0"}
                service.sync(now=datetime(2026, 3, 23, 5, 1, tzinfo=timezone.utc))

                ready = db.get_vm(101)
                self.assertIsNotNone(ready)
                assert ready is not None
                self.assertTrue(ready.manual_throttle)
                self.assertTrue(ready.throttle_active)
                self.assertIn(("apply", 101), shaper.actions)
            finally:
                db.close()

    def test_sync_preserves_counter_baseline_when_counter_device_is_missing(self) -> None:
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
            inspector = FakeInspector()
            try:
                service = VmQuotaService(config=config, db=db, inspector=inspector, shaper=FakeShaper())
                now = datetime(2026, 3, 23, 4, 40, tzinfo=timezone.utc)
                service.sync(now=now)
                db.set_counter(101, "tap101i0", 10, 20, now)

                inspector.interfaces = set()
                service.sync(now=datetime(2026, 3, 23, 5, 0, tzinfo=timezone.utc))

                self.assertEqual(db.get_counters(101)["tap101i0"], (10, 20))
            finally:
                db.close()

    def test_sync_clears_ifb_when_stopped_vm_was_marked_throttled(self) -> None:
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
                service = VmQuotaService(config=config, db=db, inspector=StoppedFakeInspector(), shaper=shaper)
                now = datetime(2026, 3, 23, 4, 40, tzinfo=timezone.utc)
                service.sync(now=now)
                record = db.get_vm(101)
                self.assertIsNotNone(record)
                assert record is not None
                record.throttle_active = True
                db.upsert_vm(record)

                service.sync(now=datetime(2026, 3, 23, 5, 0, tzinfo=timezone.utc))

                self.assertIn(("clear", 101), shaper.actions)
                cleared = db.get_vm(101)
                self.assertIsNotNone(cleared)
                assert cleared is not None
                self.assertFalse(cleared.throttle_active)
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

    def test_failed_reset_does_not_clear_counter_baseline(self) -> None:
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
                record.total_bytes = 800
                db.upsert_vm(record)
                db.set_counter(101, "tap101i0", 10, 20, now)

                failing = VmQuotaService(config=config, db=db, inspector=FakeInspector(), shaper=FailingClearShaper())
                with self.assertRaises(RuntimeError):
                    failing.reset_vm(101, usage_only=True, now=datetime(2026, 3, 23, 5, 0, tzinfo=timezone.utc))

                persisted = db.get_vm(101)
                self.assertIsNotNone(persisted)
                assert persisted is not None
                self.assertEqual(persisted.total_bytes, 800)
                self.assertEqual(db.get_counters(101)["tap101i0"], (10, 20))
            finally:
                db.close()

    def test_failed_reanchor_does_not_clear_counter_baseline(self) -> None:
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
                record.total_bytes = 800
                db.upsert_vm(record)
                db.set_counter(101, "tap101i0", 10, 20, now)

                failing = VmQuotaService(config=config, db=db, inspector=FakeInspector(), shaper=FailingClearShaper())
                with self.assertRaises(RuntimeError):
                    failing.set_vm(101, anchor_day=15, now=datetime(2026, 3, 23, 5, 0, tzinfo=timezone.utc))

                persisted = db.get_vm(101)
                self.assertIsNotNone(persisted)
                assert persisted is not None
                self.assertEqual(persisted.total_bytes, 800)
                self.assertEqual(db.get_counters(101)["tap101i0"], (10, 20))
            finally:
                db.close()

    def test_manual_throttle_on_stopped_vm_is_deferred_until_vm_runs(self) -> None:
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
                service = VmQuotaService(config=config, db=db, inspector=StoppedFakeInspector(), shaper=shaper)
                now = datetime(2026, 3, 23, 4, 40, tzinfo=timezone.utc)
                service.sync(now=now)
                updated = service.throttle_vm(101, "apply", now=now)

                self.assertTrue(updated.manual_throttle)
                self.assertFalse(updated.throttle_active)
                self.assertEqual(shaper.actions, [])
            finally:
                db.close()

    def test_throttle_rejects_unknown_action(self) -> None:
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
                service.sync(now=datetime(2026, 3, 23, 4, 40, tzinfo=timezone.utc))
                with self.assertRaisesRegex(ValueError, "throttle action must be apply or clear"):
                    service.throttle_vm(101, "disable", now=datetime(2026, 3, 23, 5, 0, tzinfo=timezone.utc))
            finally:
                db.close()

    def test_set_vm_rejects_invalid_anchor_day(self) -> None:
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
                service.sync(now=datetime(2026, 3, 23, 4, 40, tzinfo=timezone.utc))
                with self.assertRaisesRegex(ValueError, "anchor day must be between 1 and 31"):
                    service.set_vm(101, anchor_day=99, now=datetime(2026, 3, 23, 5, 0, tzinfo=timezone.utc))
            finally:
                db.close()

    def test_sync_with_realistic_pve_layout_detects_recreated_vm(self) -> None:
        class FakeRunner:
            def __init__(self, stdout: str) -> None:
                self.stdout = stdout

            def run(self, args: list[str], *, check: bool = False, error_message: str = "command failed") -> CommandResult:
                return CommandResult(args=tuple(args), returncode=0, stdout=self.stdout, stderr="")

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
            config_dir = Path(tempdir) / "qemu-server"
            sysfs_root = Path(tempdir) / "sys" / "class" / "net"
            config_dir.mkdir(parents=True)
            (config_dir / "101.conf").write_text(
                "name: vm101\nnet0: virtio=AA:BB:CC:DD:EE:FF,bridge=vmbr1,firewall=1\nsmbios1: uuid=uuid-a\n",
                encoding="utf-8",
            )
            stats_root = sysfs_root / "tap101i0" / "statistics"
            stats_root.mkdir(parents=True)
            (stats_root / "rx_bytes").write_text("100", encoding="utf-8")
            (stats_root / "tx_bytes").write_text("200", encoding="utf-8")

            db = StateDB(config.state_db)
            try:
                inspector = PveInspector(
                    config_dir=config_dir,
                    sysfs_root=sysfs_root,
                    runner=FakeRunner("VMID NAME STATUS MEM(MB) BOOTDISK(GB) PID\n101 vm101 running 1024 10.00 1234\n"),
                )
                service = VmQuotaService(config=config, db=db, inspector=inspector, shaper=FakeShaper())
                service.sync(now=datetime(2026, 3, 23, 4, 40, tzinfo=timezone.utc))

                (config_dir / "101.conf").write_text(
                    "name: vm101\nnet0: virtio=AA:BB:CC:DD:EE:FF,bridge=vmbr1,firewall=1\nsmbios1: uuid=uuid-b\n",
                    encoding="utf-8",
                )
                (stats_root / "rx_bytes").write_text("150", encoding="utf-8")
                (stats_root / "tx_bytes").write_text("250", encoding="utf-8")
                service.sync(now=datetime(2026, 3, 23, 4, 45, tzinfo=timezone.utc))

                record = db.get_vm(101)
                self.assertIsNotNone(record)
                assert record is not None
                self.assertEqual(record.bios_uuid, "uuid-b")
                self.assertEqual(record.total_bytes, 0)
                self.assertEqual(db.recent_events(101, limit=1)[0].kind, "recreate")
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
