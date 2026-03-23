# vmquota 运维手册

这份文档面向宿主机日常运维，默认你已经把 `vmquota` 安装到 PVE 上。

## 1. 当前默认路径

- 命令入口：`/usr/local/bin/vmquota`
- 配置文件：`/etc/vmquota/config.toml`
- 状态库：`/var/lib/vmquota/state.sqlite`
- 应用目录：`/opt/vmquota`
- systemd timer：`vmquota-sync.timer`
- systemd service：`vmquota-sync.service`
- API service：`vmquota-api.service`

## 2. 基本原则

- 第一次 `sync` 主要是建立流量计数基线。
- 真正的累计流量通常从后续 `sync` 开始明显增加。
- `enforce_shaping = true` 时，超额 VM 会自动下发 `tc + IFB` 限速。
- `enforce_shaping = false` 时，只统计，不自动限速。
- `traffic` 依赖宿主机 API 服务和该 VM 的 BIOS UUID。
- 手动 `throttle --apply` 现在是持久 override，不会被下一轮自动同步清掉。

## 3. 日常查看

查看所有受管 VM：

```bash
vmquota list
```

如果要给外部脚本或面板调用：

```bash
vmquota sync --json
vmquota list --json
vmquota show 101 --json
vmquota set 101 --limit 2TB --json
vmquota set-range 101-110 --limit 2TB --json
vmquota reset 101 --usage-only --json
vmquota throttle 101 --apply --json
```

返回约定：

- `list` / `show` / `set` / `reset` / `throttle` 返回 VM 快照字段，例如 `usage_bytes`、`limit_bytes`、`usage_percent`、`state`、`next_reset_at`
- `set-range` 额外返回 `updated_count`、`updated`、`skipped`
- `sync` 返回 `message_count` 和 `messages`
- 命令失败时，如果带了 `--json`，标准输出会返回 `{"error": "..."}`

`list` 输出里：

- `Progress` 是进度条 + 百分比
- `Usage` 是 `已用 / 总额`
- `Remaining` 是剩余额度
- `Next Reset` 按配置时区显示

查看单台 VM：

```bash
vmquota show 101
```

`show` 会额外显示：

- 20 格进度条
- 剩余流量
- 本地时区下的账期起止时间
- Recent events 也按本地时区显示

手动执行一次同步：

```bash
vmquota sync
```

查看 timer：

```bash
systemctl status vmquota-sync.timer --no-pager
systemctl list-timers vmquota-sync.timer --all --no-pager
```

查看最近一次同步服务：

```bash
systemctl status vmquota-sync.service --no-pager
```

查看 API 服务：

```bash
systemctl status vmquota-api.service --no-pager
```

## 4. 调整配额

给某台 VM 改月流量上限：

```bash
vmquota set 101 --limit 3TB
```

给某台 VM 改超额后限速：

```bash
vmquota set 101 --throttle 1mbit
```

同时改限额、限速和重置日：

```bash
vmquota set 101 --limit 500GB --throttle 2mbit --anchor-day 15
```

批量改一个范围：

```bash
vmquota set-range 101-110 --limit 2TB
vmquota set-range 101-110 --throttle 1mbit
vmquota set-range 101-105 --anchor-day 20
```

说明：

- `set-range` 只会更新范围内“已存在或已建档”的 VM。
- 范围内不存在的 VMID 会在输出里显示为 skipped。
- `--anchor-day` / `--reanchor-day` 只接受 `1-31`。
- `sync` / `list` / `show` / `set` / `set-range` / `reset` / `throttle` 支持 `--json`。
- `--anchor-day 15` 会把这台 VM 的账期改到每月 15 号重置。
- 改套餐参数后，当前 VM 的限速状态会立即协调，不需要再等下一轮 `sync`。

## 5. 手动重置

只清当前流量，不改重置日：

```bash
vmquota reset 101 --usage-only
```

把重置日改成今天，并从今天开始新账期：

```bash
vmquota reset 101 --reanchor-today
```

把重置日改成指定日期：

```bash
vmquota reset 101 --reanchor-day 20
```

## 6. 手动限速与解限

手动给某台 VM 下发限速：

```bash
vmquota throttle 101 --apply
```

手动清掉限速：

```bash
vmquota throttle 101 --clear
```

适用场景：

- 先手动验证 `tc + IFB` 是否正常
- 临时封顶某台 VM
- 做回滚或排障

说明：

- 现在“手动限速”是**持久 override**。
- 只要你没有执行 `vmquota throttle <vmid> --clear`，即使 VM 当前没超额，这个 override 也会继续保持。
- 如果目标 VM 当前是 `stopped`，手动 override 仍会持久化，但要等 VM 真正运行起来后才会有内核级限速规则。

## 7. 开关自动限速

查看配置：

```bash
sed -n '1,160p' /etc/vmquota/config.toml
```

把自动限速关闭：

```bash
sed -i 's/enforce_shaping = true/enforce_shaping = false/' /etc/vmquota/config.toml
vmquota sync
```

把自动限速打开：

```bash
sed -i 's/enforce_shaping = false/enforce_shaping = true/' /etc/vmquota/config.toml
vmquota sync
```

说明：

- 修改完配置后，手动跑一次 `vmquota sync` 最稳。
- timer 自己也会在下一轮自动读到最新配置。

## 8. systemd 管理

启动 timer：

```bash
systemctl start vmquota-sync.timer
```

停止 timer：

```bash
systemctl stop vmquota-sync.timer
```

重启 timer：

```bash
systemctl restart vmquota-sync.timer
```

启动 API：

```bash
systemctl start vmquota-api.service
```

停止 API：

```bash
systemctl stop vmquota-api.service
```

重启 API：

```bash
systemctl restart vmquota-api.service
```

开机自启：

```bash
systemctl enable vmquota-sync.timer
systemctl enable vmquota-api.service
```

## 9. VM 内自助查询

建议模板内预置 `/usr/local/bin/traffic`。

用户查看文字版结果：

```bash
traffic
```

用户查看 JSON：

```bash
traffic --json
```

用户查看极简原始字段：

```bash
traffic --brief
```

这个命令内部会自动读取 BIOS UUID 并查询宿主机 API，用户不需要手动处理 UUID。

说明：

- `traffic` 只是 guest 内自助查询脚本，不是常驻 agent
- 宿主机侧的计费、账期和限速逻辑仍全部由 `vmquota` 在 PVE 上执行

文本输出示例：

```text
虚拟机: 101 (Copy-of-VM-debian12-base)
流量:   [##------------------] 10.25%
已用:   205.00 GB / 2.00 TB
剩余:   1.80 TB
状态:   normal
超量限速: 2.00 mbit/s
重置日: 每月 23 号
下次重置: 2026-04-23 00:00:00+08:00
```

JSON 返回字段包括：

- `usage_bytes`
- `limit_bytes`
- `remaining_bytes`
- `usage_percent`
- `progress_text`
- `throttle_active`
- `state`
- `next_reset_at`

`traffic --brief` 返回 4 列原始字段，以制表符分隔：

1. `usage_bytes`
2. `limit_bytes`
3. `usage_percent`
4. `state`

## 10. 模板与 guest 脚本

模板更新、`traffic` 脚本分发、工作副本流程和清理清单请统一参考：

- [TEMPLATE.md](TEMPLATE.md)

这里不再重复展开模板操作细节，避免和模板专用文档出现双处维护。

## 11. 常见排障

### 11.1 `list` 里没有 VM

先看范围配置：

```bash
sed -n '1,160p' /etc/vmquota/config.toml
```

确认目标 VM 是否落在 `vmid_ranges` 里。

### 11.2 流量一直是 0

先手动跑两次：

```bash
vmquota sync
sleep 5
vmquota sync
vmquota list
```

因为第一次同步通常只是建立基线。

### 11.3 已超额但没限速

先确认全局开关：

```bash
grep -n 'enforce_shaping' /etc/vmquota/config.toml
```

再看该 VM 状态：

```bash
vmquota show 101
```

如果状态已经是 `throttled`，再查 `tc`：

```bash
tc qdisc show dev ifbup101
tc qdisc show dev ifbdn101
tc filter show dev tap101i0 ingress
tc filter show dev fwln101i0 ingress
```

说明：

- 下载方向优先看 `fwln<vmid>i<index>`，不要再按旧经验只查 `fwpr`。

### 11.4 删除重建后重新开始计费

`vmquota` 默认按 BIOS UUID 识别新实例。

如果你删掉旧 VM，再用同一个 VMID 新建一个新 VM，只要 BIOS UUID 变化，它就会被当成新实例重新建档，重置日也会按新发现日期重新设置。

### 11.5 VM 内 `traffic` 查不到

先确认模板或当前 VM 内脚本存在：

```bash
command -v traffic
```

先确认 API 服务状态：

```bash
systemctl status vmquota-api.service --no-pager
```

再确认宿主机监听：

```bash
ss -ltnp | grep 9527
```

确认配置里的监听地址：

```bash
sed -n '1,160p' /etc/vmquota/config.toml
```

如果改了 API 配置，记得重启：

```bash
systemctl restart vmquota-api.service
```

### 11.6 升级后 `traffic` 连不上

重点确认配置文件里有没有 `[api]` 段：

```bash
grep -n '^\[api\]' -A5 /etc/vmquota/config.toml
```

当前安装脚本会自动补这段；如果是手工复制老配置文件，容易漏掉。

## 12. 卸载与回滚

普通卸载：

```bash
./uninstall.sh
```

彻底清掉配置和状态：

```bash
./uninstall.sh --purge-all
```

如果只是想回退到“只统计、不限速”，不需要卸载，直接：

```bash
sed -i 's/enforce_shaping = true/enforce_shaping = false/' /etc/vmquota/config.toml
vmquota sync
```

## 13. 建议的日常操作顺序

新增一台 VM 后：

1. 确认 VMID 落在受管范围里
2. 手动跑一次 `vmquota sync`
3. 用 `vmquota show <vmid>` 看是否已经自动建档
4. 如有需要，再用 `vmquota set` 改套餐参数

月中给客户改套餐时：

1. 用 `vmquota show <vmid>` 看当前状态
2. 用 `vmquota set` 改 `--limit` 和 `--throttle`
3. 若需要重置账期，再用 `vmquota reset`

更新模板时：

1. 用 Full Clone 做工作副本
2. 在工作副本里修改并验证
3. 重新生成模板 `100`
4. 拉一次测试副本验证
5. 清理工作副本和测试副本
