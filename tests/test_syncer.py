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


def make_config(tempdir: str, *, enforce_shaping: bool = False, auto_enroll: bool = True) -> AppConfig:
    root = Path(tempdir)
    return AppConfig(
        path=root / "config.toml",
        timezone_name="Asia/Shanghai",
        timezone=ZoneInfo("Asia/Shanghai"),
        state_db=root / "state.sqlite",
        api_bind_host="127.0.0.1",
        api_bind_port=9527,
        enforce_shaping=enforce_shaping,
        auto_enroll=auto_enroll,
        vmid_ranges=parse_vmid_ranges(["101-110"]),
        default_limit_bytes=2_000_000_000_000,
        default_throttle_bps=2_000_000,
    )


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
        self.cleared_plans = []
        self.active: set[int] = set()

    def apply(self, vmid: int, plan, rate_bps: int) -> None:
        self.actions.append(("apply", vmid))
        self.active.add(vmid)

    def clear(self, vmid: int, plan) -> None:
        self.actions.append(("clear", vmid))
        self.cleared_plans.append(plan)
        self.active.discard(vmid)

    def clear_vmid_runtime(self, vmid: int, interfaces: set[str]) -> None:
        self.actions.append(("clear-runtime", vmid))
        self.cleared_plans.append(FakeRuntimeCleanupPlan(vmid, interfaces))
        self.active.discard(vmid)

    def is_applied(self, vmid: int, plan, rate_bps: int) -> bool:
        return vmid in self.active


class FakeRuntimeCleanupPlan:
    def __init__(self, vmid: int, interfaces: set[str]) -> None:
        from vmquota.shaping import TrafficShaper

        plan = TrafficShaper._vmid_cleanup_plan(vmid, interfaces)
        self.upload_hooks = plan.upload_hooks
        self.download_hooks = plan.download_hooks


class RateAwareFakeShaper(FakeShaper):
    def __init__(self) -> None:
        super().__init__()
        self.active_rates: dict[int, int] = {}

    def apply(self, vmid: int, plan, rate_bps: int) -> None:
        super().apply(vmid, plan, rate_bps)
        self.active_rates[vmid] = rate_bps

    def clear(self, vmid: int, plan) -> None:
        super().clear(vmid, plan)
        self.active_rates.pop(vmid, None)

    def clear_vmid_runtime(self, vmid: int, interfaces: set[str]) -> None:
        super().clear_vmid_runtime(vmid, interfaces)
        self.active_rates.pop(vmid, None)

    def is_applied(self, vmid: int, plan, rate_bps: int) -> bool:
        return self.active_rates.get(vmid) == rate_bps


class FailingApplyShaper(FakeShaper):
    def apply(self, vmid: int, plan, rate_bps: int) -> None:
        raise RuntimeError("boom")


class FailingClearShaper(FakeShaper):
    def clear(self, vmid: int, plan) -> None:
        raise RuntimeError("clear failed")

    def clear_vmid_runtime(self, vmid: int, interfaces: set[str]) -> None:
        raise RuntimeError("clear failed")

    def is_applied(self, vmid: int, plan, rate_bps: int) -> bool:
        return True


class SyncerTests(unittest.TestCase):
    def test_reset_usage_only_preserves_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            config = make_config(tempdir)
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

    def test_reset_usage_only_rejects_reanchor_options(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            config = make_config(tempdir)
            db = StateDB(config.state_db)
            try:
                service = VmQuotaService(config=config, db=db, inspector=FakeInspector(), shaper=FakeShaper())
                service.sync(now=datetime(2026, 3, 23, 4, 40, tzinfo=timezone.utc))

                with self.assertRaisesRegex(ValueError, "usage_only cannot be combined"):
                    service.reset_vm(
                        101,
                        usage_only=True,
                        reanchor_today=True,
                        now=datetime(2026, 3, 23, 5, 0, tzinfo=timezone.utc),
                    )
            finally:
                db.close()

    def test_reset_event_records_cleared_usage_and_cycle_change(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            config = make_config(tempdir)
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
                updated = service.reset_vm(101, reanchor_day=20, now=reset_at)
                event = db.recent_events(101, limit=1)[0]

                self.assertEqual(event.kind, "reset")
                self.assertEqual(event.details["mode"], "reanchor-day")
                self.assertEqual(event.details["cleared_total_bytes"], 12345)
                self.assertEqual(event.details["old"]["period_start"], original.period_start.isoformat())
                self.assertEqual(event.details["old"]["next_reset_at"], original.next_reset_at.isoformat())
                self.assertEqual(event.details["new"]["period_start"], updated.period_start.isoformat())
                self.assertEqual(event.details["new"]["next_reset_at"], updated.next_reset_at.isoformat())
            finally:
                db.close()

    def test_set_event_records_old_and_new_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            config = make_config(tempdir)
            db = StateDB(config.state_db)
            try:
                service = VmQuotaService(config=config, db=db, inspector=FakeInspector(), shaper=FakeShaper())
                now = datetime(2026, 3, 23, 4, 40, tzinfo=timezone.utc)
                service.sync(now=now)
                original = db.get_vm(101)
                self.assertIsNotNone(original)
                assert original is not None

                service.set_vm(101, limit_bytes=500_000_000_000, throttle_bps=1_000_000)
                event = db.recent_events(101, limit=1)[0]

                self.assertEqual(event.kind, "set")
                self.assertEqual(
                    event.details,
                    {
                        "old": {
                            "limit_bytes": original.limit_bytes,
                            "throttle_bps": original.throttle_bps,
                            "anchor_day": original.anchor_day,
                        },
                        "new": {
                            "limit_bytes": 500_000_000_000,
                            "throttle_bps": 1_000_000,
                            "anchor_day": original.anchor_day,
                        },
                    },
                )
            finally:
                db.close()

    def test_set_anchor_day_event_records_cleared_usage(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            config = make_config(tempdir)
            db = StateDB(config.state_db)
            try:
                service = VmQuotaService(config=config, db=db, inspector=FakeInspector(), shaper=FakeShaper())
                now = datetime(2026, 3, 23, 4, 40, tzinfo=timezone.utc)
                service.sync(now=now)
                original = db.get_vm(101)
                self.assertIsNotNone(original)
                assert original is not None
                original.total_bytes = 4321
                db.upsert_vm(original)

                updated = service.set_vm(101, anchor_day=20, now=datetime(2026, 3, 25, 1, 0, tzinfo=timezone.utc))
                event = db.recent_events(101, limit=1)[0]

                self.assertEqual(event.kind, "set")
                self.assertEqual(event.details["cleared_total_bytes"], 4321)
                self.assertEqual(event.details["cycle_old"]["period_start"], original.period_start.isoformat())
                self.assertEqual(event.details["cycle_new"]["period_start"], updated.period_start.isoformat())
            finally:
                db.close()

    def test_period_reset_event_records_previous_total(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            config = make_config(tempdir)
            db = StateDB(config.state_db)
            try:
                service = VmQuotaService(config=config, db=db, inspector=FakeInspector(), shaper=FakeShaper())
                now = datetime(2026, 3, 23, 4, 40, tzinfo=timezone.utc)
                service.sync(now=now)
                original = db.get_vm(101)
                self.assertIsNotNone(original)
                assert original is not None
                original.total_bytes = 67890
                original.next_reset_at = datetime(2026, 3, 24, 0, 0, tzinfo=timezone.utc)
                db.upsert_vm(original)

                service.sync(now=datetime(2026, 3, 24, 0, 1, tzinfo=timezone.utc))
                updated = db.get_vm(101)
                self.assertIsNotNone(updated)
                assert updated is not None
                event = db.recent_events(101, limit=1)[0]

                self.assertEqual(event.kind, "period-reset")
                self.assertEqual(event.details["cleared_total_bytes"], 67890)
                self.assertEqual(event.details["old"]["next_reset_at"], original.next_reset_at.isoformat())
                self.assertEqual(event.details["new"]["period_start"], updated.period_start.isoformat())
            finally:
                db.close()

    def test_sync_clears_throttle_when_vm_is_back_under_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            config = make_config(tempdir, enforce_shaping=True)
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
                self.assertIn(("clear-runtime", 101), shaper.actions)
            finally:
                db.close()

    def test_set_range_updates_existing_vms_and_skips_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            config = make_config(tempdir)
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
            config = make_config(tempdir, enforce_shaping=True)
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
                self.assertNotIn(("clear-runtime", 101), shaper.actions)
            finally:
                db.close()

    def test_sync_does_not_report_throttled_until_all_hooks_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            config = make_config(tempdir, enforce_shaping=True)
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
            config = make_config(tempdir, enforce_shaping=True)
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
            config = make_config(tempdir, enforce_shaping=True)
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
            config = make_config(tempdir, enforce_shaping=True)
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

                self.assertIn(("clear-runtime", 101), shaper.actions)
                cleared = db.get_vm(101)
                self.assertIsNotNone(cleared)
                assert cleared is not None
                self.assertFalse(cleared.throttle_active)
            finally:
                db.close()

    def test_failed_sync_does_not_advance_counter_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            config = make_config(tempdir, enforce_shaping=True)
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
            config = make_config(tempdir, enforce_shaping=True)
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
            config = make_config(tempdir, enforce_shaping=True)
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
            config = make_config(tempdir, enforce_shaping=True)
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

    def test_manual_throttle_clear_on_stopped_vm_cleans_runtime_ifb(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            config = make_config(tempdir, enforce_shaping=True)
            db = StateDB(config.state_db)
            shaper = FakeShaper()
            try:
                service = VmQuotaService(config=config, db=db, inspector=StoppedFakeInspector(), shaper=shaper)
                now = datetime(2026, 3, 23, 4, 40, tzinfo=timezone.utc)
                service.sync(now=now)
                record = db.get_vm(101)
                self.assertIsNotNone(record)
                assert record is not None
                record.manual_throttle = True
                record.throttle_active = True
                db.upsert_vm(record)

                updated = service.throttle_vm(101, "clear", now=datetime(2026, 3, 23, 5, 0, tzinfo=timezone.utc))

                self.assertFalse(updated.manual_throttle)
                self.assertFalse(updated.throttle_active)
                self.assertEqual(shaper.actions, [("clear-runtime", 101)])
                self.assertEqual(shaper.cleared_plans[-1].upload_hooks, ())
                self.assertEqual(shaper.cleared_plans[-1].download_hooks, ())
            finally:
                db.close()

    def test_throttle_rejects_unknown_action(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            config = make_config(tempdir, enforce_shaping=True)
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
            config = make_config(tempdir)
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
            config = make_config(tempdir)
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
                event = db.recent_events(101, limit=1)[0]
                self.assertEqual(event.kind, "recreate")
                self.assertEqual(event.details["old_uuid"], "uuid-a")
                self.assertEqual(event.details["new_uuid"], "uuid-b")
                self.assertEqual(event.details["old_record"]["total_bytes"], 0)
            finally:
                db.close()

    def test_recreate_clears_previous_custom_rate_shaping(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            config = make_config(tempdir)
            db = StateDB(config.state_db)
            try:
                inspector = FakeInspector()
                shaper = RateAwareFakeShaper()
                service = VmQuotaService(config=config, db=db, inspector=inspector, shaper=shaper)
                service.sync(now=datetime(2026, 3, 23, 4, 40, tzinfo=timezone.utc))

                original = db.get_vm(101)
                self.assertIsNotNone(original)
                assert original is not None
                original.throttle_bps = 1_000_000
                original.manual_throttle = True
                original.throttle_active = True
                db.upsert_vm(original)
                shaper.active_rates[101] = 1_000_000
                shaper.actions.clear()

                inspector.vm = VmInfo(
                    vmid=101,
                    name="vm101",
                    status="running",
                    bios_uuid="uuid-101-recreated",
                    tags=(),
                    template=False,
                    nics=(NicConfig(index=0, bridge="vmbr1", firewall=True, mac=None, model="virtio", raw=""),),
                )
                service.sync(now=datetime(2026, 3, 23, 5, 0, tzinfo=timezone.utc))

                self.assertIn(("clear-runtime", 101), shaper.actions)
                self.assertNotIn(101, shaper.active_rates)
                updated = db.get_vm(101)
                self.assertIsNotNone(updated)
                assert updated is not None
                self.assertEqual(updated.bios_uuid, "uuid-101-recreated")
                self.assertEqual(updated.throttle_bps, config.default_throttle_bps)
                self.assertFalse(updated.manual_throttle)
                self.assertFalse(updated.throttle_active)
            finally:
                db.close()

    def test_recreate_event_records_old_record_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            config = make_config(tempdir)
            db = StateDB(config.state_db)
            try:
                inspector = FakeInspector()
                service = VmQuotaService(config=config, db=db, inspector=inspector, shaper=FakeShaper())
                service.sync(now=datetime(2026, 3, 23, 4, 40, tzinfo=timezone.utc))

                original = db.get_vm(101)
                self.assertIsNotNone(original)
                assert original is not None
                original.total_bytes = 123_456
                original.limit_bytes = 500_000
                original.throttle_bps = 1_000_000
                original.manual_throttle = True
                original.throttle_active = True
                db.upsert_vm(original)

                inspector.vm = VmInfo(
                    vmid=101,
                    name="vm101",
                    status="running",
                    bios_uuid="uuid-101-recreated",
                    tags=(),
                    template=False,
                    nics=(NicConfig(index=0, bridge="vmbr1", firewall=True, mac=None, model="virtio", raw=""),),
                )
                service.sync(now=datetime(2026, 3, 23, 5, 0, tzinfo=timezone.utc))

                event = db.recent_events(101, limit=1)[0]
                self.assertEqual(event.kind, "recreate")
                self.assertEqual(event.details["old_uuid"], "uuid-101")
                self.assertEqual(event.details["new_uuid"], "uuid-101-recreated")
                self.assertEqual(
                    event.details["old_record"],
                    {
                        "total_bytes": 123_456,
                        "limit_bytes": 500_000,
                        "throttle_bps": 1_000_000,
                        "anchor_day": original.anchor_day,
                        "manual_throttle": True,
                        "throttle_active": True,
                        "period_start": original.period_start.isoformat(),
                        "next_reset_at": original.next_reset_at.isoformat(),
                        "last_sync_at": original.last_sync_at.isoformat(),
                    },
                )
            finally:
                db.close()

    def test_recreate_cleanup_covers_old_tap_egress_when_firewall_mode_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            config = make_config(tempdir)
            db = StateDB(config.state_db)
            try:
                inspector = FakeInspector()
                inspector.vm = VmInfo(
                    vmid=101,
                    name="vm101",
                    status="running",
                    bios_uuid="uuid-101",
                    tags=(),
                    template=False,
                    nics=(NicConfig(index=0, bridge="vmbr1", firewall=False, mac=None, model="virtio", raw=""),),
                )
                shaper = FakeShaper()
                service = VmQuotaService(config=config, db=db, inspector=inspector, shaper=shaper)
                service.sync(now=datetime(2026, 3, 23, 4, 40, tzinfo=timezone.utc))

                original = db.get_vm(101)
                self.assertIsNotNone(original)
                assert original is not None
                original.manual_throttle = True
                original.throttle_active = True
                db.upsert_vm(original)
                shaper.actions.clear()
                shaper.cleared_plans.clear()

                inspector.vm = VmInfo(
                    vmid=101,
                    name="vm101",
                    status="running",
                    bios_uuid="uuid-101-recreated",
                    tags=(),
                    template=False,
                    nics=(NicConfig(index=0, bridge="vmbr1", firewall=True, mac=None, model="virtio", raw=""),),
                )
                service.sync(now=datetime(2026, 3, 23, 5, 0, tzinfo=timezone.utc))

                self.assertIn(("clear-runtime", 101), shaper.actions)
                clear_plan = shaper.cleared_plans[-1]
                self.assertIn(("tap101i0", "ingress"), [(hook.device, hook.hook) for hook in clear_plan.upload_hooks])
                self.assertIn(("tap101i0", "egress"), [(hook.device, hook.hook) for hook in clear_plan.download_hooks])
                self.assertIn(("fwpr101p0", "ingress"), [(hook.device, hook.hook) for hook in clear_plan.download_hooks])
            finally:
                db.close()

    def test_reset_after_recreate_clears_previous_runtime_shaping(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            config = make_config(tempdir)
            db = StateDB(config.state_db)
            try:
                inspector = FakeInspector()
                shaper = RateAwareFakeShaper()
                service = VmQuotaService(config=config, db=db, inspector=inspector, shaper=shaper)
                service.sync(now=datetime(2026, 3, 23, 4, 40, tzinfo=timezone.utc))

                original = db.get_vm(101)
                self.assertIsNotNone(original)
                assert original is not None
                original.throttle_bps = 1_000_000
                original.manual_throttle = True
                original.throttle_active = True
                db.upsert_vm(original)
                shaper.active_rates[101] = 1_000_000
                shaper.actions.clear()

                inspector.vm = VmInfo(
                    vmid=101,
                    name="vm101",
                    status="running",
                    bios_uuid="uuid-101-recreated",
                    tags=(),
                    template=False,
                    nics=(NicConfig(index=0, bridge="vmbr1", firewall=True, mac=None, model="virtio", raw=""),),
                )
                service.reset_vm(101, usage_only=True, now=datetime(2026, 3, 23, 5, 0, tzinfo=timezone.utc))

                self.assertIn(("clear-runtime", 101), shaper.actions)
                self.assertNotIn(101, shaper.active_rates)
                updated = db.get_vm(101)
                self.assertIsNotNone(updated)
                assert updated is not None
                self.assertEqual(updated.bios_uuid, "uuid-101-recreated")
                self.assertFalse(updated.throttle_active)
            finally:
                db.close()

    def test_reset_clear_covers_old_tap_egress_when_firewall_mode_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            config = make_config(tempdir)
            db = StateDB(config.state_db)
            try:
                inspector = FakeInspector()
                inspector.vm = VmInfo(
                    vmid=101,
                    name="vm101",
                    status="running",
                    bios_uuid="uuid-101",
                    tags=(),
                    template=False,
                    nics=(NicConfig(index=0, bridge="vmbr1", firewall=False, mac=None, model="virtio", raw=""),),
                )
                shaper = FakeShaper()
                service = VmQuotaService(config=config, db=db, inspector=inspector, shaper=shaper)
                service.sync(now=datetime(2026, 3, 23, 4, 40, tzinfo=timezone.utc))

                original = db.get_vm(101)
                self.assertIsNotNone(original)
                assert original is not None
                original.throttle_active = True
                db.upsert_vm(original)
                shaper.active.add(101)
                shaper.actions.clear()
                shaper.cleared_plans.clear()

                inspector.vm = VmInfo(
                    vmid=101,
                    name="vm101",
                    status="running",
                    bios_uuid="uuid-101-recreated",
                    tags=(),
                    template=False,
                    nics=(NicConfig(index=0, bridge="vmbr1", firewall=True, mac=None, model="virtio", raw=""),),
                )
                service.reset_vm(101, usage_only=True, now=datetime(2026, 3, 23, 5, 0, tzinfo=timezone.utc))

                self.assertIn(("clear-runtime", 101), shaper.actions)
                clear_plan = shaper.cleared_plans[0]
                self.assertIn(("tap101i0", "egress"), [(hook.device, hook.hook) for hook in clear_plan.download_hooks])
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
