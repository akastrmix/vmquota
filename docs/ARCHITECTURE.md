# vmquota 架构说明

这份文档只记录**长期稳定的实现不变量**，帮助后续维护者快速判断“什么可以改，什么不能改偏”。

## 1. 系统边界

- `vmquota` 运行在 Proxmox VE 宿主机上
- 核心计费、账期、状态持久化和限速都在宿主机侧完成
- guest 内不需要常驻 agent
- 如果要启用 VM 内自助查询，只需要预置 `/usr/local/bin/traffic`

## 2. 受管对象识别

- 受管 VM 由配置中的 `vmid_ranges` 决定
- 模板 VM 不纳入计费
- 实例身份优先使用 BIOS UUID
- 如果同一个 VMID 被删除后重建，只要 BIOS UUID 变化，就视为新实例重新建档

## 3. 计费口径

- 流量来源是宿主机虚拟网卡计数器，不按 guest IP 统计
- 计费按每个受管 NIC 的 `rx_bytes + tx_bytes` 累加
- 默认统计设备是 `tap<vmid>i<index>`
- 多张网卡的上行和下行流量统一累计到同一个月账期

## 4. 账期模型

- 默认月账期锚点是“首次发现当天”
- `period_start` 和 `next_reset_at` 统一按配置时区计算，再以 UTC 存储
- 每次跨过 `next_reset_at` 时，进入新账期并清零当前累计
- 手动 `reset` 会清流量
- 手动 `reanchor` 会同时修改锚点日并从当前时刻开始新账期
- `anchor_day` / `reanchor_day` 只允许 `1-31`

## 5. 状态持久化

- 状态库默认是 `/var/lib/vmquota/state.sqlite`
- `managed_vms` 保存 VM 当前账期和限速状态
- `nic_counters` 保存每张计费网卡的上次基线计数
- `events` 保存关键状态变更事件
- 数据库写入必须避免“状态改了一半、基线先丢了”的半成功状态

## 6. 限速模型

- 限速依赖 `tc + IFB`
- 上传方向优先从 `tap<vmid>i<index> ingress` 重定向
- 下载方向：
  - 当 `firewall=1` 时，优先使用 `fwln<vmid>i<index> ingress`
  - 如果 `fwln` 不存在，再回退到 `fwpr<vmid>p<index>`
  - 如果 `fwln` 和 `fwpr` 都不存在，视为下载 hook 未完整发现，等待下一轮 `sync`
  - 只有 `firewall=0` 的 NIC 才使用 `tap... egress`
- 只有运行中 VM 的计数设备、上传 hook、下载 hook 都完整发现后，才允许认为限速已应用；接口缺失时应保持未限速状态并等待下一轮 `sync` 重试

## 7. 自动限速与手动限速

- 自动限速由 `enforce_shaping` 控制
- 当自动限速开启且 VM 超额时，会自动下发双向限速
- 解除限速必须删除本 VM 的 redirect filter、IFB qdisc 和 `ifbup/ifbdn` 运行时设备
- 手动 `vmquota throttle <vmid> --apply` 是**持久 override**
- 手动 override 不会被下一轮 `sync` 自动清掉
- 只有显式 `vmquota throttle <vmid> --clear` 才会撤销 override
- 如果目标 VM 当前是 `stopped`，override 会先落库，等 VM 真正运行后再下发内核规则

## 8. API 与 guest 查询

- 宿主机只读 API 默认绑定 `10.200.0.1:9527`
- `traffic` 通过 BIOS UUID 查询宿主机 API
- `traffic --brief` 返回 4 列：

```text
usage_bytes<TAB>limit_bytes<TAB>usage_percent<TAB>state
```

## 9. 主要入口

- 宿主机 CLI：`/usr/local/bin/vmquota`
- 配置文件：`/etc/vmquota/config.toml`
- systemd timer：`vmquota-sync.timer`
- API service：`vmquota-api.service`

## 10. 相关文档

- 日常运维请看 [RUNBOOK.md](RUNBOOK.md)
- 模板与 guest 脚本流程请看 [TEMPLATE.md](TEMPLATE.md)
