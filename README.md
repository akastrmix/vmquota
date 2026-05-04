# vmquota

`vmquota` 是运行在 Proxmox VE 宿主机上的轻量级 VM 流量配额工具。

它做四件事：

- 按 VM 统计月累计流量
- 超额后用 `tc + IFB` 自动双向限速
- 提供宿主机 CLI 运维入口
- 提供 VM 内自助查询脚本 `traffic`

默认部署面向这类 PVE 双网卡 VM：

- `net0 -> vmbr1`：IPv4 NAT
- `net1 -> vmbr0`：原生 IPv6
- 计费口径：所有受管网卡的上行 + 下行总和

## 能力

- 自动纳管配置范围内的新 VM
- 以首次发现当天作为默认月账期重置日
- 用 BIOS UUID 识别删除重建后的新实例
- SQLite 持久化状态和计数基线
- 支持 `list/show/set/set-range/reset/throttle`
- 支持 JSON 输出，方便外部脚本或面板调用
- 支持 VM 内通过 `/usr/local/bin/traffic` 查询自己的流量状态

## 目录

- `src/vmquota/`：主程序源码
- `examples/config.toml`：示例配置
- `systemd/`：systemd 单元
- `guest/traffic`：VM 内自助查询脚本
- `tests/`：回归测试
- `docs/RUNBOOK.md`：宿主机日常运维
- `docs/TEMPLATE.md`：模板和 `traffic` 分发流程
- `docs/ARCHITECTURE.md`：长期实现不变量
- `AGENTS.md`：AI/后续接手边界说明

## 默认路径

- 应用目录：`/opt/vmquota`
- 命令入口：`/usr/local/bin/vmquota`
- 配置文件：`/etc/vmquota/config.toml`
- 状态库：`/var/lib/vmquota/state.sqlite`
- API 服务：`10.200.0.1:9527`
- API 查询记录：`/var/lib/vmquota/api-access.jsonl`

## 常用命令

```bash
vmquota sync
vmquota list
vmquota show 101
vmquota set 101 --limit 3TB --throttle 1mbit --anchor-day 15
vmquota set-range 101-110 --limit 2TB
vmquota reset 101 --usage-only
vmquota throttle 101 --apply
vmquota throttle 101 --clear
vmquota access-log
```

JSON 输出：

```bash
vmquota show 101 --json
vmquota list --json
vmquota sync --json
```

VM 内查询：

```bash
traffic
traffic --json
traffic --brief
```

## 安装

```bash
chmod +x install.sh uninstall.sh
./install.sh
```

只安装、不启动 timer/API：

```bash
./install.sh --no-start
```

## 卸载

```bash
./uninstall.sh
```

彻底清理配置和状态：

```bash
./uninstall.sh --purge-all
```

## 测试

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

## 关键说明

- `vmquota` 必须运行在 PVE 宿主机上。
- 第一次 `sync` 主要建立计数基线；累计流量通常从后续 `sync` 开始增长。
- 下载方向整形在 `firewall=1` 时优先挂到 `fwln<vmid>i<index>`，没有 `fwln` 才回退到 `fwpr<vmid>p<index>`。
- `enforce_shaping` 控制是否自动按超额状态限速。
- 手动 `throttle --apply` 是持久 override，不会被下一轮 `sync` 自动清掉。
- 模板更新和 `traffic` 分发必须看 `docs/TEMPLATE.md`。

## 文档

- 宿主机日常操作：[docs/RUNBOOK.md](docs/RUNBOOK.md)
- 模板与 guest 脚本：[docs/TEMPLATE.md](docs/TEMPLATE.md)
- 实现不变量：[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- AI/后续接手边界：[AGENTS.md](AGENTS.md)
