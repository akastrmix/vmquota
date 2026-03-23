from __future__ import annotations

from datetime import datetime

from .billing import initial_cycle, manual_reanchor_cycle, next_anchor_after, utc_now
from .config import AppConfig
from .db import StateDB
from .models import ManagedVm, ShapingAction, TrafficPlan, VmEvent, VmInfo, VmMutationPlan
from .parsing import parse_vmid_ranges, validate_anchor_day, vmid_in_ranges
from .pve import PveInspector
from .shaping import TrafficShaper


class VmQuotaService:
    def __init__(
        self,
        config: AppConfig,
        db: StateDB,
        inspector: PveInspector | None = None,
        shaper: TrafficShaper | None = None,
    ) -> None:
        self.config = config
        self.db = db
        self.inspector = inspector or PveInspector()
        self.shaper = shaper or TrafficShaper()

    def sync(self, now: datetime | None = None) -> list[str]:
        now = now or utc_now()
        interfaces = self.inspector.existing_interfaces()
        messages: list[str] = []
        for vm in self._managed_vms():
            if self.db.get_vm(vm.vmid) is None and not self.config.auto_enroll:
                continue
            plan = self._plan_sync(vm, now, interfaces)
            self._execute_plan(plan)
            messages.extend(plan.messages)
        return messages

    def list_vms(self) -> list[ManagedVm]:
        return self.db.list_vms()

    def show_vm(self, vmid: int) -> tuple[ManagedVm, list[VmEvent]]:
        record = self.db.get_vm(vmid)
        if not record:
            raise ValueError(f"VM {vmid} is not enrolled")
        return record, self.db.recent_events(vmid)

    def set_vm(
        self,
        vmid: int,
        *,
        limit_bytes: int | None = None,
        throttle_bps: int | None = None,
        anchor_day: int | None = None,
        now: datetime | None = None,
    ) -> ManagedVm:
        now = now or utc_now()
        plan = self._plan_policy_update(
            vmid,
            now=now,
            limit_bytes=limit_bytes,
            throttle_bps=throttle_bps,
            anchor_day=anchor_day,
        )
        self._execute_plan(plan)
        return plan.record

    def set_range(
        self,
        vmid_range: str,
        *,
        limit_bytes: int | None = None,
        throttle_bps: int | None = None,
        anchor_day: int | None = None,
        now: datetime | None = None,
    ) -> tuple[list[ManagedVm], list[int]]:
        now = now or utc_now()
        selected = parse_vmid_ranges([vmid_range])[0]
        discovered = {vm.vmid for vm in self.inspector.discover_vms() if selected.contains(vm.vmid) and not vm.template}
        enrolled = {vm.vmid for vm in self.db.list_vms() if selected.contains(vm.vmid)}
        vmids = sorted(discovered | enrolled)
        updated: list[ManagedVm] = []
        skipped: list[int] = []
        for vmid in range(selected.start, selected.end + 1):
            if vmid not in vmids:
                skipped.append(vmid)
                continue
            plan = self._plan_policy_update(
                vmid,
                now=now,
                limit_bytes=limit_bytes,
                throttle_bps=throttle_bps,
                anchor_day=anchor_day,
            )
            self._execute_plan(plan)
            updated.append(plan.record)
        return updated, skipped

    def reset_vm(
        self,
        vmid: int,
        *,
        usage_only: bool = False,
        reanchor_today: bool = False,
        reanchor_day: int | None = None,
        now: datetime | None = None,
    ) -> ManagedVm:
        del usage_only
        now = now or utc_now()
        plan = self._plan_reset(vmid, now=now, reanchor_today=reanchor_today, reanchor_day=reanchor_day)
        self._execute_plan(plan)
        return plan.record

    def throttle_vm(self, vmid: int, action: str, now: datetime | None = None) -> ManagedVm:
        now = now or utc_now()
        plan = self._plan_manual_throttle(vmid, action=action, now=now)
        self._execute_plan(plan)
        return plan.record

    def _plan_sync(self, vm: VmInfo, now: datetime, interfaces: set[str]) -> VmMutationPlan:
        record, clear_counters, events = self._ensure_record(vm, now)
        plan = VmMutationPlan(record=record, clear_counters=clear_counters, events=events)
        traffic_plan, rules_active = self._runtime_snapshot(vm, interfaces, record.throttle_bps)
        rules_active = self._plan_period_roll(plan, vm, now, traffic_plan, rules_active)
        plan.record, sampled_counters = self._sample_usage(
            vm,
            plan.record,
            now,
            interfaces,
            ignore_previous_counters=plan.clear_counters,
        )
        plan.counters = sampled_counters
        plan.replace_counters = sampled_counters is not None
        plan.record.name = vm.name
        plan.record.last_seen_at = now
        plan.record.updated_at = now
        self._plan_shaping_reconciliation(plan, vm, now, traffic_plan, rules_active)
        return plan

    def _plan_policy_update(
        self,
        vmid: int,
        *,
        now: datetime,
        limit_bytes: int | None,
        throttle_bps: int | None,
        anchor_day: int | None,
    ) -> VmMutationPlan:
        record, clear_counters, events = self._require_record_state(vmid, now)
        plan = VmMutationPlan(record=record, clear_counters=clear_counters, events=events)
        if limit_bytes is not None:
            plan.record.limit_bytes = limit_bytes
        if throttle_bps is not None:
            plan.record.throttle_bps = throttle_bps
        if anchor_day is not None:
            anchor_day = validate_anchor_day(anchor_day)
            period_start, next_reset = manual_reanchor_cycle(now, anchor_day, self.config.timezone)
            plan.record.anchor_day = anchor_day
            plan.record.period_start = period_start
            plan.record.next_reset_at = next_reset
            plan.record.total_bytes = 0
            plan.clear_counters = True
        plan.record.updated_at = now
        vm = self.inspector.get_vm(vmid)
        if vm is None:
            plan.record.throttle_active = False
        else:
            interfaces = self.inspector.existing_interfaces()
            traffic_plan, rules_active = self._runtime_snapshot(vm, interfaces, plan.record.throttle_bps)
            self._plan_shaping_reconciliation(plan, vm, now, traffic_plan, rules_active)
        plan.events.append(
            VmEvent(
                vmid=vmid,
                bios_uuid=plan.record.bios_uuid,
                ts=now,
                kind="set",
                message="Updated VM policy",
                details={
                    "limit_bytes": plan.record.limit_bytes,
                    "throttle_bps": plan.record.throttle_bps,
                    "anchor_day": plan.record.anchor_day,
                },
            )
        )
        return plan

    def _plan_reset(
        self,
        vmid: int,
        *,
        now: datetime,
        reanchor_today: bool,
        reanchor_day: int | None,
    ) -> VmMutationPlan:
        record, clear_counters, events = self._require_record_state(vmid, now)
        plan = VmMutationPlan(record=record, clear_counters=clear_counters, events=events)
        if reanchor_today:
            anchor_day, period_start, next_reset = initial_cycle(now, self.config.timezone)
            plan.record.anchor_day = anchor_day
            plan.record.period_start = period_start
            plan.record.next_reset_at = next_reset
        elif reanchor_day is not None:
            reanchor_day = validate_anchor_day(reanchor_day)
            period_start, next_reset = manual_reanchor_cycle(now, reanchor_day, self.config.timezone)
            plan.record.anchor_day = reanchor_day
            plan.record.period_start = period_start
            plan.record.next_reset_at = next_reset
        plan.record.total_bytes = 0
        plan.record.updated_at = now
        plan.clear_counters = True
        vm = self.inspector.get_vm(vmid)
        if vm is None:
            plan.record.throttle_active = False
        else:
            interfaces = self.inspector.existing_interfaces()
            traffic_plan, rules_active = self._runtime_snapshot(vm, interfaces, plan.record.throttle_bps)
            self._plan_shaping_reconciliation(plan, vm, now, traffic_plan, rules_active)
        plan.events.append(
            VmEvent(
                vmid=vmid,
                bios_uuid=plan.record.bios_uuid,
                ts=now,
                kind="reset",
                message="Reset VM usage",
                details={"anchor_day": plan.record.anchor_day},
            )
        )
        return plan

    def _plan_manual_throttle(self, vmid: int, *, action: str, now: datetime) -> VmMutationPlan:
        record, clear_counters, events = self._require_record_state(vmid, now)
        vm = self.inspector.get_vm(vmid)
        if not vm:
            raise ValueError(f"VM {vmid} does not exist on this host")
        plan = VmMutationPlan(record=record, clear_counters=clear_counters, events=events)
        if action == "apply":
            plan.record.manual_throttle = True
            if vm.status == "running":
                interfaces = self.inspector.existing_interfaces()
                traffic_plan = vm.build_traffic_plan(interfaces)
                plan.shaping_actions.append(
                    ShapingAction(action="apply", vmid=vmid, plan=traffic_plan, rate_bps=plan.record.throttle_bps)
                )
                plan.record.throttle_active = True
            else:
                plan.record.throttle_active = False
        else:
            plan.record.manual_throttle = False
            if vm.status == "running":
                interfaces = self.inspector.existing_interfaces()
                traffic_plan = vm.build_traffic_plan(interfaces)
                plan.shaping_actions.append(
                    ShapingAction(action="clear", vmid=vmid, plan=traffic_plan, rate_bps=plan.record.throttle_bps)
                )
            plan.record.throttle_active = False
        plan.record.updated_at = now
        plan.events.append(
            VmEvent(
                vmid=vmid,
                bios_uuid=plan.record.bios_uuid,
                ts=now,
                kind=f"throttle-{action}",
                message=f"Throttle {action}",
                details=None,
            )
        )
        return plan

    def _execute_plan(self, plan: VmMutationPlan) -> None:
        for action in plan.shaping_actions:
            self._execute_shaping_action(action)
        self.db.save_vm_state(
            plan.record,
            plan.counters,
            replace_counters=plan.replace_counters,
            clear_counters=plan.clear_counters,
            events=plan.events,
        )

    def _execute_shaping_action(self, action: ShapingAction) -> None:
        if action.action == "apply":
            self.shaper.apply(action.vmid, action.plan, action.rate_bps)
        else:
            self.shaper.clear(action.vmid, action.plan)

    def _managed_vms(self) -> list[VmInfo]:
        managed: list[VmInfo] = []
        for vm in self.inspector.discover_vms():
            if vm.template:
                continue
            if vmid_in_ranges(vm.vmid, self.config.vmid_ranges):
                managed.append(vm)
        return managed

    def _ensure_record(self, vm: VmInfo, now: datetime) -> tuple[ManagedVm, bool, list[VmEvent]]:
        current = self.db.get_vm(vm.vmid)
        bios_uuid = vm.bios_uuid or f"vmid-{vm.vmid}"
        if current and current.bios_uuid == bios_uuid:
            return current, False, []
        anchor_day, period_start, next_reset = initial_cycle(now, self.config.timezone)
        record = ManagedVm(
            vmid=vm.vmid,
            bios_uuid=bios_uuid,
            name=vm.name,
            created_at=now,
            updated_at=now,
            last_seen_at=now,
            anchor_day=anchor_day,
            period_start=period_start,
            next_reset_at=next_reset,
            limit_bytes=self.config.default_limit_bytes,
            throttle_bps=self.config.default_throttle_bps,
            manual_throttle=False,
            throttle_active=False,
            total_bytes=0,
            last_sync_at=None,
        )
        event = VmEvent(
            vmid=vm.vmid,
            bios_uuid=bios_uuid,
            ts=now,
            kind="enroll" if current is None else "recreate",
            message="Enrolled VM" if current is None else "Detected recreated VM",
            details={"uuid": bios_uuid},
        )
        return record, True, [event]

    def _require_record_state(self, vmid: int, now: datetime) -> tuple[ManagedVm, bool, list[VmEvent]]:
        record = self.db.get_vm(vmid)
        vm = self.inspector.get_vm(vmid)
        if vm is None and record is not None:
            return record, False, []
        if vm is None:
            raise ValueError(f"VM {vmid} does not exist on this host")
        return self._ensure_record(vm, now)

    def _runtime_snapshot(
        self,
        vm: VmInfo,
        interfaces: set[str],
        rate_bps: int,
    ) -> tuple[TrafficPlan, bool]:
        traffic_plan = vm.build_traffic_plan(interfaces)
        rules_active = self.shaper.is_applied(vm.vmid, traffic_plan, rate_bps)
        return traffic_plan, rules_active

    def _plan_period_roll(
        self,
        plan: VmMutationPlan,
        vm: VmInfo,
        now: datetime,
        traffic_plan: TrafficPlan,
        rules_active: bool,
    ) -> bool:
        if now < plan.record.next_reset_at:
            return rules_active
        if rules_active and vm.status == "running" and self.config.enforce_shaping:
            plan.shaping_actions.append(
                ShapingAction(action="clear", vmid=vm.vmid, plan=traffic_plan, rate_bps=plan.record.throttle_bps)
            )
            rules_active = False
        while now >= plan.record.next_reset_at:
            plan.record.period_start = plan.record.next_reset_at
            plan.record.next_reset_at = next_anchor_after(plan.record.period_start, plan.record.anchor_day, self.config.timezone)
        plan.record.total_bytes = 0
        plan.record.throttle_active = False
        plan.record.updated_at = now
        plan.clear_counters = True
        plan.events.append(
            VmEvent(
                vmid=plan.record.vmid,
                bios_uuid=plan.record.bios_uuid,
                ts=now,
                kind="period-reset",
                message="Started new billing period",
                details={"next_reset_at": plan.record.next_reset_at.isoformat()},
            )
        )
        plan.messages.append(f"VM {plan.record.vmid}: billing period reset")
        return rules_active

    def _sample_usage(
        self,
        vm: VmInfo,
        record: ManagedVm,
        now: datetime,
        interfaces: set[str],
        *,
        ignore_previous_counters: bool = False,
    ) -> tuple[ManagedVm, dict[str, tuple[int, int]] | None]:
        if vm.status != "running":
            record.last_sync_at = now
            return record, None
        traffic_plan = vm.build_traffic_plan(interfaces)
        previous = {} if ignore_previous_counters else self.db.get_counters(vm.vmid)
        delta_total = 0
        sampled_counters: dict[str, tuple[int, int]] = {}
        for nic in traffic_plan.counter_devices:
            rx, tx = self.inspector.read_interface_counters(nic)
            sampled_counters[nic] = (rx, tx)
            last = previous.get(nic)
            if last is not None:
                delta_total += _counter_delta(last[0], rx)
                delta_total += _counter_delta(last[1], tx)
        record.total_bytes += delta_total
        record.last_sync_at = now
        return record, sampled_counters

    def _desired_throttle(self, vm: VmInfo, record: ManagedVm) -> bool:
        if vm.status != "running":
            return False
        if record.manual_throttle:
            return True
        return self.config.enforce_shaping and record.over_limit

    def _plan_shaping_reconciliation(
        self,
        plan: VmMutationPlan,
        vm: VmInfo,
        now: datetime,
        traffic_plan: TrafficPlan,
        rules_active: bool,
    ) -> None:
        should_throttle = self._desired_throttle(vm, plan.record)
        if should_throttle and not rules_active:
            plan.shaping_actions.append(
                ShapingAction(action="apply", vmid=vm.vmid, plan=traffic_plan, rate_bps=plan.record.throttle_bps)
            )
            plan.record.throttle_active = True
            plan.events.append(
                VmEvent(
                    vmid=vm.vmid,
                    bios_uuid=plan.record.bios_uuid,
                    ts=now,
                    kind="throttle-applied",
                    message="Applied shaping",
                    details={"rate_bps": plan.record.throttle_bps, "manual": plan.record.manual_throttle},
                )
            )
            plan.messages.append(f"VM {vm.vmid}: throttle applied at {plan.record.throttle_bps} bps")
            return
        if not should_throttle and rules_active:
            plan.shaping_actions.append(
                ShapingAction(action="clear", vmid=vm.vmid, plan=traffic_plan, rate_bps=plan.record.throttle_bps)
            )
            plan.record.throttle_active = False
            plan.events.append(
                VmEvent(
                    vmid=vm.vmid,
                    bios_uuid=plan.record.bios_uuid,
                    ts=now,
                    kind="throttle-cleared",
                    message="Cleared shaping",
                    details={"manual": plan.record.manual_throttle},
                )
            )
            plan.messages.append(f"VM {vm.vmid}: throttle cleared")
            return
        plan.record.throttle_active = should_throttle and rules_active


def _counter_delta(previous: int, current: int) -> int:
    if current >= previous:
        return current - previous
    return current
