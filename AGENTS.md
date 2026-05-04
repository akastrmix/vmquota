# AGENTS

本文件给新的 AI 对话、子代理或后续接手者提供高密度边界上下文。日常命令看 `docs/RUNBOOK.md`，模板流程看 `docs/TEMPLATE.md`。

## 角色定位

`vmquota` 是运行在 Proxmox VE 宿主机上的每 VM 流量配额与超额限速工具。

| 负责 | 不负责 |
| --- | --- |
| `vmquota` 代码、schema、配置、CLI、API | `pve-main` 当前真实状态、网络事实、NAT、端口映射 |
| 每 VM 流量统计、账期、限速协调 | 宿主机本机服务端口限速，那是 `hostportlimit` 的职责 |
| 安装卸载、systemd、项目内文档 | `vmaudit` 的内部实现、状态库或审计逻辑 |
| guest 自助查询脚本 `guest/traffic` | PVE 通用运维手册和跨组件事实维护 |

相关工作区：

- `pve docs`：`C:\Users\Lenovo\Downloads\pve docs`
- `hostportlimit`：`C:\Users\Lenovo\Desktop\hostportlimit`
- `vmaudit`：`C:\Users\Lenovo\Desktop\vmaudit`

## 实现原则

- 保持 `vmquota` 简单、轻量、独立；优先减少概念、命令、配置项和状态面。
- 不写兼容层、临时补丁、启发式兜底，或为了保留旧行为而增加复杂度的代码；旧设计不合理时，直接删除并重构为最优解。
- 当前 PVE 契约明确要求的路径不算兼容包袱，例如下载方向 `fwln` 不存在时回退 `fwpr`；但不要扩展成泛化兼容框架。
- 线上异常必须追到根因；不要只清理现象或依赖人工记忆，修复应落到代码、测试、文档或明确的操作流程里。
- 不把宿主机代理端口限速、审计、NAT 管理塞进本仓库。
- 只使用 Python 标准库；新增依赖前必须有明确收益。

## 关键边界

- 受管范围由 `scope.vmid_ranges` 决定；示例默认 `101-110`，真实现网范围以 `pve docs` 为准。
- 模板 VM 不纳入计费；实例身份优先使用 BIOS UUID。
- 流量统计按宿主机虚拟网卡计数器做，不按 guest IP 做；计费口径是受管 VM 所有已配置 NIC 的 `rx_bytes + tx_bytes` 总和。
- 默认账期锚点是首次发现当天。
- 数据库写入必须避免“状态改了一半、计数基线先丢了”的半成功状态。
- 计数设备默认是 `tap<vmid>i<index>`。
- 上传限速从 `tap<vmid>i<index> ingress` 重定向到 IFB。
- 下载限速在 `firewall=1` 时优先使用 `fwln<vmid>i<index> ingress`；没有 `fwln` 时才回退 `fwpr<vmid>p<index>`；不要按旧经验默认查 `fwpr`。
- 限速状态必须反映真实 `tc + IFB` 覆盖；运行中 VM 的计数设备、上传 hook 或下载 hook 未完整发现时，不要标记 `throttle_active=True`。
- 手动 `vmquota throttle <vmid> --apply` 是持久 override；只有显式 `--clear` 才撤销。
- guest 查询脚本默认路径是 `/usr/local/bin/traffic`，API 默认绑定 `10.200.0.1:9527`，`traffic --brief` 返回 4 列 tab 字段。

## 跨工作区读取规则

只改 `vmquota` 内部代码或项目文档时，优先读：

1. 本文件
2. `README.md`
3. `docs/ARCHITECTURE.md`
4. `docs/RUNBOOK.md`

涉及宿主机真实配置、测试槽位、端口规划、受管范围、跨组件 `tc`/IFB 边界时，先读：

1. `C:\Users\Lenovo\Downloads\pve docs\system\README.md`
2. `C:\Users\Lenovo\Downloads\pve docs\system\INTEGRATION_CONTRACT.md`
3. `C:\Users\Lenovo\Downloads\pve docs\state\pve-main.current.md`
4. `C:\Users\Lenovo\Downloads\pve docs\state\components\vmquota.current.md`

信息真相源：

- 宿主机当前事实：`pve docs`
- `vmquota` 内部实现：本仓库
- 跨组件共享约束：`pve docs/system/INTEGRATION_CONTRACT.md`

## pve-main 操作边界

可以从本工作区连接 `pve-main` 查询或操作 `vmquota` 相关状态，默认连接方式以 `pve docs/inventory/pve-hosts.yml` 为准。

只读检查优先在 `pve docs` 工作区执行：

```bash
cd "C:\Users\Lenovo\Downloads\pve docs"
python tools/run_remote_check.py checks/pve_vmquota_check.sh
```

线上变更包括：安装/卸载 `vmquota`、改 `/etc/vmquota/config.toml`、执行 `vmquota set/reset/throttle`、改 systemd、改模板内 `traffic`。

线上变更规则：

- 变更前确认目标、影响范围和回滚命令。
- 变更前后运行相关检查；优先 `vmquota show <vmid>`，底层排障再看 `tc qdisc/filter`。
- 不处理宿主机代理端口限速、`vmaudit` 状态、防火墙总策略、证书、用户权限等非本组件事项。
- 密码、passphrase、API token 等秘密只能临时用于本次命令，不能写入文件、文档、日志或提交。
- 线上事实变化后，按 `pve docs/ops/docs_update_policy.md` 更新 `state/components/vmquota.current.md`、`state/pve-main.current.md`、`CHANGELOG.md`、`changes/README.md`、相关 checks/runbook/contract。

## 模板高风险边界

不要直接硬改模板 `100`。模板修改必须走 `docs/TEMPLATE.md` 里的 Full Clone 工作副本流程。

除非用户明确授权，不要主动删除/重建实例；这会影响账本、UUID 识别和模板链路验证。

## 测试

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```
