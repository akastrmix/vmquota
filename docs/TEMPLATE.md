# vmquota 模板与 guest 脚本说明

这份文档只讲两件事：

- `traffic` 脚本如何进入模板和现有 VM
- 模板修改时必须遵守的安全流程

## 1. 什么时候需要动模板

以下情况**只动 PVE 宿主机即可**：

- 计费逻辑升级
- 宿主机 CLI / API 升级
- SQLite、systemd、自动限速逻辑升级

以下情况**需要考虑模板**：

- 想让新售卖 VM 默认自带 `traffic`
- 需要更新 guest 内 `/usr/local/bin/traffic`
- 想把现有 VM 内的自助查询体验和新模板保持一致

## 2. `traffic` 的定位

- `traffic` 是 guest 内自助查询脚本
- 它不是常驻 agent
- 它依赖宿主机 API 和 BIOS UUID
- 默认路径是 `/usr/local/bin/traffic`
- 脚本源码在仓库里的 `guest/traffic`

## 3. 推荐分发策略

### 3.1 新售卖 VM

- 把 `guest/traffic` 同步进模板 `100`
- 这样后续新克隆出的 VM 会默认具备自助查询能力

### 3.2 已在运行的 VM

- 如有需要，可以把同一份脚本补发到现有 VM
- 补发后建议立即执行一次：

```bash
traffic --brief
```

确认 guest 到宿主机 API 的查询链路正常

## 4. 模板修改流程

不要直接“解模板后硬改”。

固定流程：

1. 从模板 `100` 做一个 **Full Clone** 工作副本
2. 在工作副本里修改
3. 验证功能没问题
4. 清理唯一身份信息
5. 用工作副本重新生成新的模板 `100`
6. 再拉一个一次性测试副本验证
7. 清理工作副本和测试副本

## 5. 模板清理清单

至少清理以下内容：

- `cloud-init clean --logs`
- `/var/lib/cloud/instance`
- `/var/lib/cloud/instances`
- `/var/lib/cloud/sem`
- `/etc/machine-id`
- `/var/lib/dbus/machine-id`
- `/etc/ssh/ssh_host_*`

## 6. 验证顺序

建议按这个顺序验证：

1. 在工作副本里确认 `traffic` 已存在：

```bash
command -v traffic
```

2. 验证脚本是否可执行：

```bash
traffic --brief
```

3. 重新制作为模板后，再拉一个测试副本重复验证

## 7. 高风险边界

- 不要直接改模板本体
- 不要跳过工作副本验证
- 除非用户明确授权，不要主动删除/重建实例
- 模板相关动作会影响 cloud-init、SSH host key、机器标识和后续售卖链路

## 8. 当前环境约定

- 当前模板：`VM100` `debian12-base`
- 默认售卖 VM 范围：`101-110`
- 宿主机 API 默认绑定：`10.200.0.1:9527`

## 9. 相关文档

- 宿主机日常运维请看 [RUNBOOK.md](RUNBOOK.md)
- 实现不变量请看 [ARCHITECTURE.md](ARCHITECTURE.md)
