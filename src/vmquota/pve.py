from __future__ import annotations

from pathlib import Path

from .models import NicConfig, VmInfo
from .system import CommandRunner, SubprocessCommandRunner


class PveInspector:
    def __init__(
        self,
        config_dir: Path = Path("/etc/pve/qemu-server"),
        sysfs_root: Path = Path("/sys/class/net"),
        runner: CommandRunner | None = None,
    ) -> None:
        self.config_dir = config_dir
        self.sysfs_root = sysfs_root
        self.runner = runner or SubprocessCommandRunner()

    def list_statuses(self) -> dict[int, str]:
        output = self.runner.run(
            ["qm", "list"],
            check=True,
            error_message="failed to list VM statuses",
        ).stdout
        statuses: dict[int, str] = {}
        for line in output.splitlines()[1:]:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 3:
                continue
            statuses[int(parts[0])] = parts[2]
        return statuses

    def discover_vms(self) -> list[VmInfo]:
        statuses = self.list_statuses()
        vms: list[VmInfo] = []
        for path in sorted(self.config_dir.glob("*.conf")):
            vmid = int(path.stem)
            content = path.read_text(encoding="utf-8")
            vms.append(self._parse_vm_config(vmid, content, statuses.get(vmid, "unknown")))
        return vms

    def get_vm(self, vmid: int) -> VmInfo | None:
        path = self.config_dir / f"{vmid}.conf"
        if not path.exists():
            return None
        status = self.list_statuses().get(vmid, "unknown")
        return self._parse_vm_config(vmid, path.read_text(encoding="utf-8"), status)

    def existing_interfaces(self) -> set[str]:
        if not self.sysfs_root.exists():
            return set()
        return {item.name for item in self.sysfs_root.iterdir()}

    def read_interface_counters(self, device: str) -> tuple[int, int]:
        stats_root = self.sysfs_root / device / "statistics"
        rx = int((stats_root / "rx_bytes").read_text(encoding="utf-8").strip())
        tx = int((stats_root / "tx_bytes").read_text(encoding="utf-8").strip())
        return rx, tx

    @staticmethod
    def _parse_vm_config(vmid: int, content: str, status: str) -> VmInfo:
        name = f"vm-{vmid}"
        bios_uuid: str | None = None
        template = False
        tags: tuple[str, ...] = ()
        nics: list[NicConfig] = []
        for raw_line in content.splitlines():
            if ":" not in raw_line:
                continue
            key, value = raw_line.split(":", 1)
            key = key.strip()
            value = value.strip()
            if key == "name":
                name = value
            elif key == "template":
                template = value == "1"
            elif key == "tags":
                tags = tuple(sorted(part.strip() for part in value.split(";") if part.strip()))
            elif key == "smbios1":
                for item in value.split(","):
                    item = item.strip()
                    if item.startswith("uuid="):
                        bios_uuid = item.split("=", 1)[1]
                        break
            elif key.startswith("net") and key[3:].isdigit():
                nics.append(PveInspector._parse_nic(int(key[3:]), value))
        return VmInfo(
            vmid=vmid,
            name=name,
            status=status,
            bios_uuid=bios_uuid,
            tags=tags,
            template=template,
            nics=tuple(sorted(nics, key=lambda item: item.index)),
        )

    @staticmethod
    def _parse_nic(index: int, raw: str) -> NicConfig:
        items = [part.strip() for part in raw.split(",") if part.strip()]
        model = None
        mac = None
        bridge = None
        firewall = False
        for item in items:
            if "=" not in item:
                continue
            key, value = item.split("=", 1)
            if key in {"virtio", "e1000", "rtl8139", "vmxnet3"}:
                model = key
                mac = value
            elif key == "bridge":
                bridge = value
            elif key == "firewall":
                firewall = value == "1"
        return NicConfig(
            index=index,
            bridge=bridge,
            firewall=firewall,
            mac=mac,
            model=model,
            raw=raw,
        )
