import unittest

from vmquota.pve import PveInspector


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


if __name__ == "__main__":
    unittest.main()
