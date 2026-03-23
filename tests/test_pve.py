from pathlib import Path
import tempfile
import unittest

from vmquota.pve import PveInspector
from vmquota.system import CommandResult


SAMPLE_CONFIG = """\
name: Copy-of-VM-debian12-base
net0: virtio=BC:24:11:C9:0E:CB,bridge=vmbr1,firewall=1
net1: virtio=BC:24:11:E9:B7:81,bridge=vmbr0,firewall=1
smbios1: uuid=535c5192-e162-4dad-9800-5d457bc107ef
"""


class PveTests(unittest.TestCase):
    def test_parse_vm_and_traffic_plan(self) -> None:
        vm = PveInspector._parse_vm_config(101, SAMPLE_CONFIG, "running")
        interfaces = {"tap101i0", "tap101i1", "fwln101i0", "fwln101i1", "fwpr101p0", "fwpr101p1"}
        plan = vm.build_traffic_plan(interfaces)
        self.assertEqual(vm.bios_uuid, "535c5192-e162-4dad-9800-5d457bc107ef")
        self.assertEqual(plan.counter_devices, ("tap101i0", "tap101i1"))
        self.assertEqual([hook.device for hook in plan.upload_hooks], ["tap101i0", "tap101i1"])
        self.assertEqual([hook.device for hook in plan.download_hooks], ["fwln101i0", "fwln101i1"])

    def test_discover_vms_from_realistic_config_and_sysfs_layout(self) -> None:
        class FakeRunner:
            def __init__(self) -> None:
                self.calls: list[tuple[str, ...]] = []

            def run(self, args: list[str], *, check: bool = False, error_message: str = "command failed") -> CommandResult:
                self.calls.append(tuple(args))
                return CommandResult(
                    args=tuple(args),
                    returncode=0,
                    stdout="VMID NAME STATUS MEM(MB) BOOTDISK(GB) PID\n101 vm101 running 1024 10.00 1234\n",
                    stderr="",
                )

        with tempfile.TemporaryDirectory() as tempdir:
            config_dir = Path(tempdir) / "qemu-server"
            sysfs_root = Path(tempdir) / "sys" / "class" / "net"
            config_dir.mkdir(parents=True)
            (config_dir / "101.conf").write_text(SAMPLE_CONFIG, encoding="utf-8")
            stats_root = sysfs_root / "tap101i0" / "statistics"
            stats_root.mkdir(parents=True)
            (stats_root / "rx_bytes").write_text("123", encoding="utf-8")
            (stats_root / "tx_bytes").write_text("456", encoding="utf-8")

            runner = FakeRunner()
            inspector = PveInspector(config_dir=config_dir, sysfs_root=sysfs_root, runner=runner)
            vms = inspector.discover_vms()

            self.assertEqual([vm.vmid for vm in vms], [101])
            self.assertEqual(vms[0].status, "running")
            self.assertEqual(inspector.existing_interfaces(), {"tap101i0"})
            self.assertEqual(inspector.read_interface_counters("tap101i0"), (123, 456))
            self.assertEqual(runner.calls, [("qm", "list")])


if __name__ == "__main__":
    unittest.main()
