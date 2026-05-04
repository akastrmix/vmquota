import unittest
from unittest.mock import patch

from vmquota.models import SourceHook, TrafficPlan
from vmquota.shaping import TrafficShaper
from vmquota.system import CommandResult


class ShapingTests(unittest.TestCase):
    def test_rate_tokens_accept_mbit_and_kbit_forms(self) -> None:
        tokens = TrafficShaper._rate_tokens(2000)
        self.assertIn("rate 2000Kbit", tokens)
        self.assertIn("rate 2Mbit", tokens)

    def test_empty_plan_is_not_reported_as_applied(self) -> None:
        class TbfRunner:
            def run(self, args: list[str], *, check: bool = False, error_message: str = "command failed") -> CommandResult:
                return CommandResult(args=tuple(args), returncode=0, stdout="qdisc tbf 8001: root rate 2Mbit", stderr="")

        plan = TrafficPlan(counter_devices=(), upload_hooks=(), download_hooks=())
        shaper = TrafficShaper(runner=TbfRunner())

        self.assertFalse(shaper.is_applied(101, plan, 2_000_000))

    def test_rate_must_be_positive(self) -> None:
        plan = TrafficPlan(
            counter_devices=("tap101i0",),
            upload_hooks=(SourceHook(device="tap101i0", hook="ingress"),),
            download_hooks=(SourceHook(device="fwpr101p0", hook="ingress"),),
        )
        shaper = TrafficShaper(dry_run=True)

        with self.assertRaisesRegex(ValueError, "rate_bps must be > 0"):
            shaper.apply(101, plan, 0)
        with self.assertRaisesRegex(ValueError, "rate_bps must be > 0"):
            shaper.is_applied(101, plan, 0)

    def test_dry_run_does_not_execute_system_commands(self) -> None:
        plan = TrafficPlan(
            counter_devices=("tap101i0",),
            upload_hooks=(SourceHook(device="tap101i0", hook="ingress"),),
            download_hooks=(SourceHook(device="fwpr101p0", hook="ingress"),),
        )
        shaper = TrafficShaper(dry_run=True)
        with patch("vmquota.system.subprocess.run", side_effect=AssertionError("subprocess.run should not be called")):
            shaper.apply(101, plan, 2_000_000)
            self.assertFalse(shaper.is_applied(101, plan, 2_000_000))
            shaper.clear(101, plan)

    def test_apply_uses_runner_to_program_ifb_and_redirects(self) -> None:
        class RecordingRunner:
            def __init__(self) -> None:
                self.calls: list[tuple[str, ...]] = []

            def run(self, args: list[str], *, check: bool = False, error_message: str = "command failed") -> CommandResult:
                self.calls.append(tuple(args))
                if args[:3] == ["ip", "link", "show"]:
                    return CommandResult(args=tuple(args), returncode=1, stdout="", stderr="not found")
                return CommandResult(args=tuple(args), returncode=0, stdout="", stderr="")

        plan = TrafficPlan(
            counter_devices=("tap101i0",),
            upload_hooks=(SourceHook(device="tap101i0", hook="ingress"),),
            download_hooks=(SourceHook(device="fwln101i0", hook="ingress"),),
        )
        runner = RecordingRunner()
        shaper = TrafficShaper(runner=runner)
        shaper.apply(101, plan, 2_000_000)

        self.assertIn(("modprobe", "ifb"), runner.calls)
        self.assertIn(("ip", "link", "add", "ifbup101", "type", "ifb"), runner.calls)
        self.assertIn(("tc", "qdisc", "replace", "dev", "tap101i0", "clsact"), runner.calls)
        self.assertIn(
            ("tc", "filter", "replace", "dev", "fwln101i0", "ingress", "pref", "49152", "handle", "1", "matchall", "action", "mirred", "egress", "redirect", "dev", "ifbdn101"),
            runner.calls,
        )

    def test_clear_removes_ifb_runtime_devices(self) -> None:
        class RecordingRunner:
            def __init__(self) -> None:
                self.calls: list[tuple[str, ...]] = []

            def run(self, args: list[str], *, check: bool = False, error_message: str = "command failed") -> CommandResult:
                self.calls.append(tuple(args))
                return CommandResult(args=tuple(args), returncode=0, stdout="", stderr="")

        plan = TrafficPlan(
            counter_devices=("tap101i0",),
            upload_hooks=(SourceHook(device="tap101i0", hook="ingress"),),
            download_hooks=(SourceHook(device="fwln101i0", hook="ingress"),),
        )
        runner = RecordingRunner()
        shaper = TrafficShaper(runner=runner)

        shaper.clear(101, plan)

        self.assertIn(("tc", "filter", "delete", "dev", "tap101i0", "ingress", "pref", "49152"), runner.calls)
        self.assertIn(("tc", "filter", "delete", "dev", "fwln101i0", "ingress", "pref", "49152"), runner.calls)
        self.assertIn(("tc", "qdisc", "del", "dev", "ifbup101", "root"), runner.calls)
        self.assertIn(("tc", "qdisc", "del", "dev", "ifbdn101", "root"), runner.calls)
        self.assertIn(("ip", "link", "del", "ifbup101"), runner.calls)
        self.assertIn(("ip", "link", "del", "ifbdn101"), runner.calls)

    def test_clear_raises_when_tc_command_is_missing(self) -> None:
        class MissingCommandRunner:
            def run(self, args: list[str], *, check: bool = False, error_message: str = "command failed") -> CommandResult:
                return CommandResult(args=tuple(args), returncode=127, stdout="", stderr="tc not found")

        plan = TrafficPlan(
            counter_devices=(),
            upload_hooks=(SourceHook(device="tap101i0", hook="ingress"),),
            download_hooks=(),
        )
        shaper = TrafficShaper(runner=MissingCommandRunner())
        with self.assertRaisesRegex(RuntimeError, "tc not found"):
            shaper.clear(101, plan)


if __name__ == "__main__":
    unittest.main()
