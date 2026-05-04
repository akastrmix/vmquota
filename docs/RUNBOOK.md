# vmquota 运维手册

这份文档面向 PVE 宿主机日常运维。模板修改和 `traffic` 分发流程看 [TEMPLATE.md](TEMPLATE.md)，实现不变量看 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 1. 默认入口

| 项 | 默认值 |
| --- | --- |
| CLI | `/usr/local/bin/vmquota` |
| 配置 | `/etc/vmquota/config.toml` |
| 状态库 | `/var/lib/vmquota/state.sqlite` |
| 应用目录 | `/opt/vmquota` |
| 同步 timer | `vmquota-sync.timer` |
| 同步 service | `vmquota-sync.service` |
| API service | `vmquota-api.service` |
| API 默认监听 | `10.200.0.1:9527` |

基本原则：

- 第一次 `sync` 主要建立计数基线；累计流量通常从后续 `sync` 开始增长。
- `enforce_shaping = true` 时，超额 VM 会自动下发 `tc + IFB` 限速。
- `enforce_shaping = false` 时，只统计，不自动限速。
- 手动 `throttle --apply` 是持久 override；只有显式 `--clear` 才撤销。
- `traffic` 依赖宿主机 API 和 VM BIOS UUID，不是常驻 agent。

## 2. 日常速查

```bash
vmquota sync
vmquota list
vmquota show 101
vmquota set 101 --limit 3TB
vmquota set 101 --throttle 1mbit
vmquota set 101 --limit 500GB --throttle 2mbit --anchor-day 15
vmquota set-range 101-110 --limit 2TB
vmquota reset 101 --usage-only
vmquota reset 101 --reanchor-today
vmquota reset 101 --reanchor-day 20
vmquota throttle 101 --apply
vmquota throttle 101 --clear
```

JSON 输出：

```bash
vmquota sync --json
vmquota list --json
vmquota show 101 --json
vmquota set 101 --limit 2TB --json
vmquota set-range 101-110 --limit 2TB --json
vmquota reset 101 --usage-only --json
vmquota throttle 101 --apply --json
```

JSON 约定：

- `list/show/set/reset/throttle` 返回 VM 快照字段，例如 `usage_bytes`、`limit_bytes`、`usage_percent`、`state`、`next_reset_at`。
- `set-range` 额外返回 `updated_count`、`updated`、`skipped`。
- `sync` 返回 `message_count` 和 `messages`。
- 失败时如果带 `--json`，标准输出返回 `{"error": "..."}`。

## 3. 查看状态

查看所有受管 VM：

```bash
vmquota list
```

查看单台 VM：

```bash
vmquota show 101
```

常用 systemd 检查：

```bash
systemctl status vmquota-sync.timer --no-pager
systemctl list-timers vmquota-sync.timer --all --no-pager
systemctl status vmquota-sync.service --no-pager
systemctl status vmquota-api.service --no-pager
```

底层限速检查：

```bash
tc qdisc show dev ifbup101
tc qdisc show dev ifbdn101
tc filter show dev tap101i0 ingress
tc filter show dev fwln101i0 ingress
tc filter show dev fwpr101p0 ingress
```

下载方向优先看 `fwln<vmid>i<index>`；只有 `fwln` 不存在时才回退 `fwpr<vmid>p<index>`。

## 4. 调整套餐

改月流量上限：

```bash
vmquota set 101 --limit 3TB
```

改超额后限速：

```bash
vmquota set 101 --throttle 1mbit
```

同时改上限、限速和重置日：

```bash
vmquota set 101 --limit 500GB --throttle 2mbit --anchor-day 15
```

批量改范围：

```bash
vmquota set-range 101-110 --limit 2TB
vmquota set-range 101-110 --throttle 1mbit
vmquota set-range 101-105 --anchor-day 20
```

注意：

- `set-range` 只更新范围内已存在或已建档的 VM；缺失 VMID 会显示在 `skipped`。
- `--anchor-day` / `--reanchor-day` 只接受 `1-31`。
- 改套餐参数后，当前 VM 的限速状态会立即协调。

## 5. 重置账期

只清当前流量，不改重置日：

```bash
vmquota reset 101 --usage-only
```

把重置日改成今天：

```bash
vmquota reset 101 --reanchor-today
```

把重置日改成指定日期：

```bash
vmquota reset 101 --reanchor-day 20
```

## 6. 手动限速

手动限速：

```bash
vmquota throttle 101 --apply
```

手动解除：

```bash
vmquota throttle 101 --clear
```

说明：

- 手动限速是持久 override，不会因为下一轮 `sync` 发现 VM 未超额而自动清掉。
- VM 停机时执行 `--apply` 会先落库，等 VM 运行且接口完整发现后再下发内核规则。

## 7. 自动限速开关

查看配置：

```bash
sed -n '1,160p' /etc/vmquota/config.toml
```

关闭自动限速：

```bash
sed -i 's/enforce_shaping = true/enforce_shaping = false/' /etc/vmquota/config.toml
vmquota sync
```

打开自动限速：

```bash
sed -i 's/enforce_shaping = false/enforce_shaping = true/' /etc/vmquota/config.toml
vmquota sync
```

修改配置后建议手动跑一次 `vmquota sync`；timer 下一轮也会读取最新配置。

## 8. systemd 管理

```bash
systemctl start vmquota-sync.timer
systemctl stop vmquota-sync.timer
systemctl restart vmquota-sync.timer
systemctl enable vmquota-sync.timer

systemctl start vmquota-api.service
systemctl stop vmquota-api.service
systemctl restart vmquota-api.service
systemctl enable vmquota-api.service
```

## 9. VM 内查询

模板或现有 VM 内建议预置 `/usr/local/bin/traffic`。脚本分发和模板流程看 [TEMPLATE.md](TEMPLATE.md)。

```bash
traffic
traffic --json
traffic --brief
```

`traffic --brief` 返回 4 列，以制表符分隔：

```text
usage_bytes<TAB>limit_bytes<TAB>usage_percent<TAB>state
```

JSON 常用字段：

- `usage_bytes`
- `limit_bytes`
- `remaining_bytes`
- `usage_percent`
- `progress_text`
- `throttle_active`
- `state`
- `next_reset_at`

## 10. 排障

### `list` 里没有 VM

```bash
sed -n '1,160p' /etc/vmquota/config.toml
qm list
```

确认目标 VM 是否存在、不是模板，并且 VMID 落在 `vmid_ranges` 内。

### 流量一直是 0

```bash
vmquota sync
sleep 5
vmquota sync
vmquota show 101
```

第一次同步通常只建立基线。若仍为 0，再确认 VM 正在运行且 `tap<vmid>i<index>` 存在。

### 已超额但没限速

```bash
grep -n 'enforce_shaping' /etc/vmquota/config.toml
vmquota show 101
tc qdisc show dev ifbup101
tc qdisc show dev ifbdn101
tc filter show dev tap101i0 ingress
tc filter show dev fwln101i0 ingress
```

如果 `throttle --apply` 或自动限速没有立刻生效，重点确认 VM 是否 running，以及计数设备、上传 hook、下载 hook 是否完整出现。

### 删除重建后重新开始计费

`vmquota` 按 BIOS UUID 识别实例。同一个 VMID 删除后重建，只要 BIOS UUID 变化，就会被当作新实例重新建档。

### VM 内 `traffic` 查不到

```bash
command -v traffic
systemctl status vmquota-api.service --no-pager
ss -ltnp | grep 9527
sed -n '1,160p' /etc/vmquota/config.toml
```

如果改了 API 配置，重启：

```bash
systemctl restart vmquota-api.service
```

## 11. 卸载与回滚

普通卸载：

```bash
./uninstall.sh
```

彻底清掉配置和状态：

```bash
./uninstall.sh --purge-all
```

只想回退到“只统计、不限速”：

```bash
sed -i 's/enforce_shaping = true/enforce_shaping = false/' /etc/vmquota/config.toml
vmquota sync
```

## 12. 推荐操作顺序

新增 VM：

1. 确认 VMID 落在受管范围。
2. 执行 `vmquota sync`。
3. 用 `vmquota show <vmid>` 确认建档。
4. 如有需要再用 `vmquota set` 改套餐。

月中改套餐：

1. 用 `vmquota show <vmid>` 看当前状态。
2. 用 `vmquota set` 改 `--limit` 和 `--throttle`。
3. 需要清流量或改账期时，再用 `vmquota reset`。

模板更新：

1. 先看 [TEMPLATE.md](TEMPLATE.md)。
2. 使用 Full Clone 工作副本。
3. 验证 `traffic --brief`。
4. 重新生成模板并拉测试副本复验。
