# vmquota

`vmquota` 是一个运行在 **Proxmox VE 宿主机**上的轻量级流量配额工具，用来做：

- 按 VM 统计月流量
- 超额后自动双向限速
- 不在 guest 内安装 agent
- 通过命令行完成日常运维
- 让 VM 内用户直接自助查询当前流量

它适合当前这类双网卡结构：

- `net0 -> vmbr1`，承载 IPv4 NAT
- `net1 -> vmbr0`，承载原生 IPv6
- 计费口径按两张网卡的上行 + 下行总和计算

## 设计目标

- 独立：核心计费、账期和限速逻辑都在宿主机侧，guest 内不需要常驻 agent；若要启用 VM 内自助查询，只需预置 `traffic` 脚本
- 轻量：只用 Python 标准库，不依赖第三方 Python 包
- 模块化：发现、账期、状态、限速、CLI、API 分层实现
- 可运维：自带安装、卸载、systemd 单元和状态库

## 当前能力

- 自动纳管受管范围内的新 VM
- 以“首次发现当天”作为默认重置日
- 用 BIOS UUID 识别“删掉后重建”的新实例
- 把状态持久化到 SQLite
- 从宿主机虚拟网卡计数器累计流量
- 在 `list/show` 里显示进度条、百分比和剩余流量
- 支持手动 `show`、`list`、`set`、`set-range`、`reset`
- 支持 `tc + IFB` 的双向限速
- 支持 VM 内通过 `traffic` 查询自己的流量状态

## 默认管理范围

示例配置默认管理 `VMID 101-110`。

## 跨工作区协作

`vmquota` 不是孤立项目。当前常用工作区一共有 3 个：

- `C:\Users\Lenovo\Downloads\pve docs`
  - 宿主机事实、部署记录、检查脚本、跨组件约束
- `C:\Users\Lenovo\Desktop\vmquota`
  - `vmquota` 自身的代码、schema、配置、CLI/API 和项目文档
- `C:\Users\Lenovo\Desktop\vmaudit`
  - `vmaudit` 自身的代码、schema、配置、CLI 和项目文档

如果任务涉及下面这些内容，不要只看本仓库：

- `pve-main` 当前现实状态
- 测试槽位 `9001`
- 受管范围约定
- 端口规划
- 跨组件共享契约

默认先读：

- `AGENTS.md`
- `C:\Users\Lenovo\Downloads\pve docs\system\SYSTEM_MAP.md`
- `C:\Users\Lenovo\Downloads\pve docs\system\OWNERSHIP_MATRIX.md`
- `C:\Users\Lenovo\Downloads\pve docs\system\INTEGRATION_CONTRACT.md`

## 目录结构

- `src/vmquota/`：主程序源码
- `examples/config.toml`：示例配置
- `systemd/`：systemd 单元模板
- `tests/`：本地回归测试
- `guest/traffic`：供模板和现有 VM 分发的一键查询脚本
- `install.sh`：安装脚本
- `uninstall.sh`：卸载脚本
- `docs/RUNBOOK.md`：运维手册
- `docs/TEMPLATE.md`：模板和 guest 脚本说明
- `docs/ARCHITECTURE.md`：核心实现不变量
- `AGENTS.md`：给 AI/新对话使用的关键上下文

## 默认安装路径

- 程序目录：`/opt/vmquota`
- 命令入口：`/usr/local/bin/vmquota`
- 配置文件：`/etc/vmquota/config.toml`
- 状态库：`/var/lib/vmquota/state.sqlite`
- 运维文档：`/opt/vmquota/docs/RUNBOOK.md`
- guest 脚本参考：`/opt/vmquota/guest/traffic`

## 常用命令

```bash
vmquota sync
vmquota list
vmquota serve
vmquota show 101
vmquota set 101 --limit 3TB --throttle 1mbit --anchor-day 15
vmquota set-range 101-110 --limit 2TB
vmquota reset 101 --usage-only
vmquota reset 101 --reanchor-today
vmquota reset 101 --reanchor-day 20
vmquota throttle 101 --apply
vmquota throttle 101 --clear
```

## 关键说明

- `vmquota` 必须运行在 PVE 宿主机上。
- `anchor-day` / `reanchor-day` 只接受 `1-31`。
- `sync` / `list` / `show` / `set` / `set-range` / `reset` / `throttle` 支持 `--json` 机器可读输出。
- 计数默认读取 `tap<vmid>i<index>`。
- 当 `firewall=1` 时，下载方向整形优先挂在 `fwln<vmid>i<index>`；若该接口不存在，才回退到 `fwpr<vmid>p<index>`。
- 第一次 `sync` 主要是建立计数基线；要看到明显累计，通常要等后续 `sync`。
- `enforce_shaping` 控制“是否自动按超额状态限速”。
- 手动 `throttle --apply` 现在是**持久 override**，不会再被下一轮 `sync` 自动清掉。
- `set` / `set-range` / `reset` 现在会即时协调当前 VM 的限速状态，不再只改数据库。
- 只读查询接口默认建议绑定在 `10.200.0.1:9527`。
- 示例配置里的 `enforce_shaping = false` 是保守示例值，不代表你线上宿主机当前一定是关闭状态。

## 文档导航

- 宿主机安装、命令和排障请看 [docs/RUNBOOK.md](docs/RUNBOOK.md)
- 模板和 `traffic` 脚本分发请看 [docs/TEMPLATE.md](docs/TEMPLATE.md)
- 计费、账期、整形等长期不变量请看 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- AI/后续接手上下文请看 [AGENTS.md](AGENTS.md)

## 安装

```bash
chmod +x install.sh uninstall.sh
./install.sh
```

如果只想先安装、暂不启动 timer/API：

```bash
./install.sh --no-start
```

## 卸载

```bash
./uninstall.sh
```

默认卸载不会删配置和状态库。若要彻底清理：

```bash
./uninstall.sh --purge-all
```

## 测试

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

## 文档

- 运维文档请看 [docs/RUNBOOK.md](docs/RUNBOOK.md)
- 模板与 guest 脚本请看 [docs/TEMPLATE.md](docs/TEMPLATE.md)
- 架构不变量请看 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- 新对话/AI 上下文请看 [AGENTS.md](AGENTS.md)
