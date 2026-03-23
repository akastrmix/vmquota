from __future__ import annotations

from dataclasses import dataclass, field

from .models import TrafficPlan
from .system import CommandResult, CommandRunner, SubprocessCommandRunner


@dataclass(slots=True)
class TrafficShaper:
    dry_run: bool = False
    runner: CommandRunner | None = None
    _last_returncode: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        if self.runner is None:
            self.runner = SubprocessCommandRunner(dry_run=self.dry_run)

    def apply(self, vmid: int, plan: TrafficPlan, rate_bps: int) -> None:
        rate_kbit = max(1, rate_bps // 1000)
        self._ensure_ifb(f"ifbup{vmid}")
        self._ensure_ifb(f"ifbdn{vmid}")
        self._run(
            [
                "tc",
                "qdisc",
                "replace",
                "dev",
                f"ifbup{vmid}",
                "root",
                "tbf",
                "rate",
                f"{rate_kbit}kbit",
                "burst",
                "64k",
                "latency",
                "400ms",
            ]
        )
        self._run(
            [
                "tc",
                "qdisc",
                "replace",
                "dev",
                f"ifbdn{vmid}",
                "root",
                "tbf",
                "rate",
                f"{rate_kbit}kbit",
                "burst",
                "64k",
                "latency",
                "400ms",
            ]
        )
        for hook in plan.upload_hooks:
            self._install_redirect(hook.device, hook.hook, f"ifbup{vmid}")
        for hook in plan.download_hooks:
            self._install_redirect(hook.device, hook.hook, f"ifbdn{vmid}")

    def clear(self, vmid: int, plan: TrafficPlan) -> None:
        for hook in plan.upload_hooks:
            self._delete_redirect(hook.device, hook.hook)
        for hook in plan.download_hooks:
            self._delete_redirect(hook.device, hook.hook)
        self._run(["tc", "qdisc", "del", "dev", f"ifbup{vmid}", "root"], check=False)
        self._run(["tc", "qdisc", "del", "dev", f"ifbdn{vmid}", "root"], check=False)

    def is_applied(self, vmid: int, plan: TrafficPlan, rate_bps: int) -> bool:
        rate_kbit = max(1, rate_bps // 1000)
        if not self._ifb_has_tbf(f"ifbup{vmid}", rate_kbit):
            return False
        if not self._ifb_has_tbf(f"ifbdn{vmid}", rate_kbit):
            return False
        for hook in plan.upload_hooks:
            if not self._hook_redirects_to_ifb(hook.device, hook.hook, f"ifbup{vmid}"):
                return False
        for hook in plan.download_hooks:
            if not self._hook_redirects_to_ifb(hook.device, hook.hook, f"ifbdn{vmid}"):
                return False
        return True

    def _ensure_ifb(self, name: str) -> None:
        self._run(["modprobe", "ifb"], check=False)
        self._run(["ip", "link", "show", name], check=False)
        if self._last_returncode != 0:
            self._run(["ip", "link", "add", name, "type", "ifb"])
        self._run(["ip", "link", "set", name, "up"])

    def _install_redirect(self, device: str, hook: str, target_ifb: str) -> None:
        self._ensure_clsact(device)
        completed = self._execute(
            [
                "tc",
                "filter",
                "replace",
                "dev",
                device,
                hook,
                "pref",
                "49152",
                "handle",
                "1",
                "matchall",
                "action",
                "mirred",
                "egress",
                "redirect",
                "dev",
                target_ifb,
            ]
        )
        if completed.returncode == 0:
            return
        stderr = completed.stderr.strip()
        if "File exists" in stderr and self._hook_redirects_to_ifb(device, hook, target_ifb):
            return
        raise RuntimeError(stderr or "failed to install redirect")

    def _delete_redirect(self, device: str, hook: str) -> None:
        self._run(["tc", "filter", "delete", "dev", device, hook, "pref", "49152"], check=False)

    def _ifb_has_tbf(self, device: str, rate_kbit: int) -> bool:
        completed = self._execute(["tc", "qdisc", "show", "dev", device])
        if completed.returncode != 0:
            return False
        output = completed.stdout
        return "tbf " in output and any(token in output for token in self._rate_tokens(rate_kbit))

    def _hook_redirects_to_ifb(self, device: str, hook: str, target_ifb: str) -> bool:
        completed = self._execute(["tc", "filter", "show", "dev", device, hook])
        if completed.returncode != 0:
            return False
        return target_ifb in completed.stdout

    def _ensure_clsact(self, device: str) -> None:
        completed = self._execute(["tc", "qdisc", "replace", "dev", device, "clsact"])
        if completed.returncode == 0:
            return
        stderr = completed.stderr.strip()
        if "File exists" in stderr:
            return
        raise RuntimeError(stderr or "failed to ensure clsact")

    @staticmethod
    def _rate_tokens(rate_kbit: int) -> set[str]:
        tokens = {f"rate {rate_kbit}Kbit", f"rate {rate_kbit}kbit"}
        if rate_kbit % 1000 == 0:
            mbit = rate_kbit // 1000
            tokens.update({f"rate {mbit}Mbit", f"rate {mbit}mbit"})
        return tokens

    def _run(self, args: list[str], check: bool = True) -> None:
        completed = self._execute(args)
        if check and completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or "command failed")

    def _execute(self, args: list[str]) -> CommandResult:
        assert self.runner is not None
        result = self.runner.run(args, check=False)
        if result.returncode == 127:
            raise RuntimeError(result.stderr.strip() or "command not found")
        self._last_returncode = result.returncode
        return result
