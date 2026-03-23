# AGENTS

这份文件给新的 AI 对话、子代理或后续接手者提供**高密度上下文**。  
目标是让新对话在不了解历史聊天的情况下，也能快速理解这套系统的部署方式、运行逻辑和高风险操作边界。

## 1. 项目定位

`vmquota` 是运行在 **Proxmox VE 宿主机**上的每 VM 流量配额与超额限速工具。

核心职责：

- 统计每台 VM 的月累计流量
- 超额后自动双向限速
- 提供宿主机 CLI 运维接口
- 提供 VM 内自助查询入口 `traffic`

## 2. 当前部署场景

已知现网是单节点 PVE，宿主机地址：

- `192.168.50.9`

当前模板：

- `VM100`：`debian12-base`

默认售卖 VM 范围：

- `101-110`

## 3. 网络拓扑

这套系统依赖当前双网卡结构，不是泛化到任意 PVE 拓扑的版本。

- `net0 -> vmbr1`
  - IPv4 NAT
  - 私网网段：`10.200.0.0/24`
  - 宿主机网关：`10.200.0.1`
- `net1 -> vmbr0`
  - 原生 IPv6
  - 走上游 LAN

重要：

- 流量统计按宿主机虚拟网卡计数器做，不按 guest IP 做。
- 计费口径是两张网卡的上行 + 下行总和。

## 4. 关键实现事实

### 4.1 计费

- 统计来源：`tap<vmid>i<index>` 的 `rx_bytes + tx_bytes`
- 月账期：按**首次发现当天**作为默认重置日
- 持久化：SQLite，默认 `/var/lib/vmquota/state.sqlite`

### 4.2 限速

- 用 `tc + IFB`
- 上传方向：
  - 优先从 `tap<vmid>i<index> ingress` 重定向
- 下载方向：
  - 当 `firewall=1` 时，优先使用 `fwln<vmid>i<index> ingress`
  - 如果 `fwln` 不存在，再回退到 `fwpr<vmid>p<index>`

不要再按旧印象把下载方向默认理解成 `fwpr`。

### 4.3 手动限速

- `vmquota throttle <vmid> --apply` 现在是**持久 override**
- 不会再被下一轮 `sync` 自动清掉
- 只有显式 `--clear` 才会撤销

### 4.4 VM 内自助查询

- guest 脚本：`/usr/local/bin/traffic`
- API 默认绑定：`10.200.0.1:9527`
- `traffic --brief` 返回 4 列原始字段：

```text
usage_bytes<TAB>limit_bytes<TAB>usage_percent<TAB>state
```

## 5. 文档入口

主要文档：

- `README.md`
- `docs/RUNBOOK.md`

本文件只保留高密度上下文，不替代运维手册。

## 6. 高风险操作边界

### 6.1 不要直接硬改模板

模板修改必须走下面流程：

1. 从模板 `100` 做 **Full Clone** 工作副本
2. 在工作副本里修改
3. 验证无误
4. 清理唯一身份信息
5. 用工作副本重新生成模板 `100`
6. 再拉一个测试副本验证
7. 清理工作副本和测试副本

不要直接依赖“解模板后继续改”。

### 6.2 清理模板工作副本时要做的事

至少清理：

- `cloud-init clean --logs`
- `/var/lib/cloud/instance`
- `/var/lib/cloud/instances`
- `/var/lib/cloud/sem`
- `/etc/machine-id`
- `/var/lib/dbus/machine-id`
- `/etc/ssh/ssh_host_*`

### 6.3 删除/重建实例是高风险动作

- 除非用户明确授权，否则不要主动做删除/重建实例操作
- 这类动作会影响账本、UUID 识别和模板链路验证

## 7. 已知踩坑点

### 7.1 宿主机重启恢复

- `vmquota-sync.timer` 和 `vmquota-api.service` 会自动恢复
- 但“已超额 VM 自动继续被限速”依赖 VM 本身重新运行
- 如果 VM 没有 `onboot`，宿主机重启后它不会自动启动

### 7.2 停机 VM 的手动限速

- 如果 VM 当前是 `stopped`，手动 override 会持久化到数据库
- 但不会立刻出现内核 `tc` 规则
- 要等 VM 真正运行后才会下发

### 7.3 小流量百分比显示

- 当前百分比展示对极小占比会显示成 `<0.01%`
- 这是刻意设计，不是统计失效

### 7.4 旧配置升级

- 老配置可能没有 `[api]`
- 当前安装脚本会自动补 `[api]` 段
- 如果是手工复制老配置而不是走安装脚本，容易漏掉

## 8. 当前推荐的验证顺序

如果 AI 需要继续验证系统，不要盲目大动作，优先顺序建议是：

1. 先看 `vmquota show <vmid>`
2. 再看 `tc qdisc/filter`
3. 再做小流量或短时 `iperf3`
4. 涉及模板修改时，一律先走 Full Clone 工作副本

## 9. 不要写进仓库的内容

不要把这些内容写进仓库：

- 宿主机 SSH 密码
- 任何长期密钥
- 外部面板密码

如果新对话需要远端登录信息，应由用户重新提供，或从安全来源读取，而不是写死在仓库文档里。
