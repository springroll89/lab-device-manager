# 物品库存系统融合与迁移

## 当前结构

物品库存已经成为主系统的一个模块，不再单独部署旧 React/Supabase
应用。平板和 PC 使用同一地址、同一账号和同一 SQLite 数据库：

- `/experiments`：实验执行与人工打标。
- `/inventory`：化学品、耗材和办公用品。
- `/measurement-station`：共享粘度计工位。
- `/`：设备实时看板。

旧 `/materials` 地址继续指向统一库存页面，已有书签无需立即修改。

## 权限

| 操作 | 最高管理员 | 实验室主管 | 操作员 |
| --- | --- | --- | --- |
| 查看、搜索、扫码、打印标签 | 是 | 是 | 是 |
| 日常领用 | 是 | 是 | 是 |
| 新增或编辑物品 | 是 | 是 | 否 |
| 入库、盘点、隔离、处置 | 是 | 是 | 否 |
| 查看与导出审计流水 | 是 | 是 | 是 |

服务端从登录会话写入操作人和账号 ID，不接受浏览器传入的伪造姓名。

## 实验用料联动

在 R201-03 扫描库存标签并确认实际用量后，系统在同一个数据库事务中：

1. 保存本步骤物料使用记录。
2. 从对应库存物品扣减实际用量。
3. 首次使用时自动填写开封日期。
4. 写入 `experiment_used` 流水，关联实验 ID、步骤实例和批次号。
5. 推进实验步骤。

库存不足、物品不可用、过期、名称不一致或单位不一致时，整组操作回滚。
同一个 `client_event_id` 重试时只返回原结果，不会重复扣库。

## 从旧 Supabase 应用迁移

### 1. 导出

在旧 Supabase 项目中分别导出完整的 `items` 表和 `logs` 表为 UTF-8 CSV。
不要只导出当前筛选结果。导出后保留原文件作为只读归档。

### 2. 预检

先停止主系统的写入操作，然后在项目目录执行：

```bash
source .venv/bin/activate
python scripts/import_supabase_inventory.py \
  --items /绝对路径/items.csv \
  --logs /绝对路径/logs.csv
```

未使用 `--apply` 时不会写数据库。输出会列出计划导入的物品和流水数量。
预检会检查必填字段、编号重复、类别、状态、数值、时间格式和日志外键。

### 3. 正式导入

确认预检无误后执行：

```bash
python scripts/import_supabase_inventory.py \
  --items /绝对路径/items.csv \
  --logs /绝对路径/logs.csv \
  --apply
```

工具会在数据库旁生成
`lab_device_manager.db.pre-inventory-import-时间戳.bak`，然后使用单一事务写入。
任何一行失败都会整批回滚。再次执行同一批文件时，已导入的物品和流水会被
识别并跳过。

字段映射：

| 旧系统 | 统一系统 |
| --- | --- |
| `code` | `container_code`，保持原 `PC-xxxx` |
| `quantity` | `quantity_remaining` |
| `in_stock` | `available` |
| `used_up` | `empty` |
| `to_dispose` | `quarantined` |
| 日志 `in` | `received` |
| 日志 `out` | `issued` |
| 日志 `adjust` | `adjusted` |
| 日志 `dispose` | `disposed` |

### 4. 验收与回滚

导入后在 `/inventory` 核对：

- 物品总数、各类别数量和原 `PC-xxxx` 编号。
- 当前库存、状态、位置、负责人、阈值和化学品字段。
- 任取若干物品，对比旧系统与新系统的流水数量、变动后余额和操作人。
- 扫描一张旧标签，确认打开对应物品。

如验收失败，停止主系统，将自动生成的 `.bak` 文件复制回原数据库路径后
再启动。不要删除旧 Supabase 数据，直至新系统完成现场验收并经过约定的
保留期。

## 库存日报

只在终端预览，不发送：

```bash
python -m lab_device_manager.inventory.alerts
```

需要钉钉提醒时，在服务器的受控环境变量中设置
`LAB_DINGTALK_WEBHOOK_URL`；如机器人启用了加签，再设置
`LAB_DINGTALK_SECRET`。随后由 macOS `launchd`、Linux `cron` 或公司已有
调度平台定时执行：

```bash
python -m lab_device_manager.inventory.alerts --send
```

Webhook 和加签密钥不要写入仓库、README、`config.toml` 或命令历史。
没有低库存、超量、临期或过期项目时，命令不会发送消息。
