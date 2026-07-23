# Lab Device Manager

实验室多设备采集管理台 —— 统一接入注射泵、粘度计、搅拌器等串口/Modbus 仪器，提供实时状态监控、运行记录、数据导出与报告生成。

> 本项目为本地实验室工具，默认绑定 `127.0.0.1`，请勿直接暴露到公网。

## 功能特性

- **多协议设备接入**：支持 Modbus RTU（Leadfluid TYD02 注射泵、HMS-C 搅拌器）与自定义二进制串口协议（方瑞粘度计）。
- **实时状态面板**：Web 仪表盘展示各设备当前状态、流速/转速、累计液量、温度、告警等。
- **运行记录管理**：自动识别一次完整运行（start → stop），记录开始/结束时间、操作人员、项目标签、备注。
- **数据导出**：支持 CSV、Excel（runs + samples 多 sheet）、单运行 PDF 报告导出。
- **运行详情页**：展示采样曲线、事件时间线、设置参数与实际液量。
- **操作员登录**：未配置密码时可在本机选择操作员身份；配置共享实验室密码后，未认证时页面与 API 均不可访问。
- **补录/Tag 修正**：可对已结束运行补充 operator、project_tag、experiment_tag、remark。
- **R-201 批次执行台**：以 Batch ID 串联 CEM/AEM 湿化学合成步骤、人工时间戳、设备数据源、物料、粘度、偏差与记录锁定。
- **实物追溯 MVP**：为实验批次、中间品、成品和存储位置生成可扫描标签，记录暂存、取回、打印与补打事件。
- **断网幂等重试**：平板写操作先进入本地 outbox，网络恢复后使用同一 `client_event_id` 自动重放。
- **R-201 电子批记录**：导出配方/物料、步骤、温度与粘度趋势、数据完整性、偏差和审计时间轴 PDF。

## 2026-07 R-201 数字化迭代

这一轮把实验执行从“把纸表搬到平板”调整为“设备持续采集，操作员只负责给真实操作打时间标记”。

### 设备数据驱动

- HMS-C、TYD02、粘度计和 WHD46-33 的数据由采集引擎持续写入后台。
- 操作员在平板点击“开始加酸”“加酸完成”“到达 40℃”等语义按钮，系统用步骤起止时间截取相应设备数据。
- 环境温湿度、搅拌转速、反应温度、注射泵累计量差值、步骤持续时间和粘度等字段优先由系统自动生成。
- 自动结果保留设备、指标键、原始样本、采样时间、截取窗口与计算方式，可以从电子批记录反查原始数据。
- 前后台统一以最近 15 秒内收到设备帧作为“在线可用”；设备离线、数据过期或目标指标缺失时，才显示人工补录入口，并明确标记人工来源。

### 平板执行体验

- 批次号按“日期 + 膜体系 + 当日序号”自动生成，操作员由登录账号带入。
- 操作界面使用连续的第 1–15 步展示；R201-01、R201-10 等内部编码只用于系统追溯。
- 页面只突出当前步骤、相关设备数据和一个主要打标按钮，其余设备和追溯信息默认折叠。
- 粘度阶段使用“记录当前设备读数”，正常情况下无需逐项抄写粘度、温度、扭矩和剪切速率。
- 数据超出建议范围时在当前步骤立即提示，操作员可返回检查，或确认“数据无误，记录异常”，避免最后一步突然出现无法理解的阻断。
- 未配置复核员时，完成步骤会直接锁定实验记录；仍有未关闭异常时会明确显示需处理的项目。

### 实物追溯与标签

- 创建批次后可为批次、中间品、成品和存储位置生成追溯编号与二维码标签。
- 支持打印、补打、暂存、取回和扫码查询事件，减少自封袋马克笔手写造成的掉色与辨认错误。
- 标签版面已由 60×40 mm 加高到 60×48 mm，长编号和二维码下方文字可以自动换行，不再被下沿截断。
- 第一期已实现批次、中间品、成品和位置追踪；原材料容器级库存及多来源谱系安排在第二、三期，详见 [`docs/TRACEABILITY-ROADMAP.md`](docs/TRACEABILITY-ROADMAP.md)。

### 当前边界

- 应用只读取设备数据，不向设备自动下发启停、转速、温度或泵速指令。
- 软件链路和模拟四设备流程已经通过测试；HMS-C 与粘度计仍需结合实验室真机完成串口参数及字段缩放验证。
- 当前默认一台设备占用一个串口；五台 HMS-C 共用一条 RS485 总线的多从机调度尚未启用。

## 支持的设备

| 设备类型 | 型号 | 协议 | 状态 |
|---|---|---|---|
| 注射泵 | Leadfluid TYD02 | Modbus RTU | 已验证 |
| 搅拌器 | HMS-C × 5 | Modbus RTU | 串口参数待实测 |
| 粘度计 | 方瑞 | 自定义二进制串口 | 校验和/帧类型待实测 |
| 温湿度 | WHD46-33 | Modbus RTU | 三通道采集；工艺判定必须绑定明确通道 |

新设备可通过实现 `instruments/base.py` 中的适配器接口扩展。

## 快速开始

### 1. 安装依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 配置设备

复制示例配置并编辑：

```bash
cp config.toml config.local.toml
# 编辑 config.local.toml，填入实际串口、波特率、Modbus 地址等
```

关键配置项：

```toml
db_path            = "./data/lab_device_manager.db"
sample_interval_ms = 1000
web_port           = 7800
auto_open_browser  = true

# 启用登录保护（为空则关闭）
# login_password = "your-strong-password"

[[devices]]
name        = "pump-1"
type        = "tyd02"
alias       = "注射泵"
serial_port = "/dev/cu.usbserial-XXXXXX"  # macOS/Linux 实际端口
baudrate    = 9600
parity      = "EVEN"        # 8E1
modbus_addr = 1
wordorder   = "CDAB"
```

> `secret_key` 首次运行时会自动生成并保存到 `data/.session_secret`，无需手动填写。

### 3. 启动应用

```bash
source .venv/bin/activate
python -m lab_device_manager
```

启动后会自动打开浏览器访问 `http://127.0.0.1:7800`。

未配置 `login_password` 时，可访问 `/login` 输入姓名建立本机操作员身份；这种模式用于本机调试与单机实验记录，不提供访问控制。配置 `login_password` 后，系统会要求“操作员姓名 + 实验室共享密码”，未认证时会先跳转到登录页。

R-201 批次执行台入口：`http://127.0.0.1:7800/experiments`。

实物追溯第二、三期路线图见
[`docs/TRACEABILITY-ROADMAP.md`](docs/TRACEABILITY-ROADMAP.md)。

> R-201 第一版固定运行在 `parallel_validation`（纸电并行验证）模式。当前共享密码不具备唯一身份、重新认证和合规电子签名能力；正式放行仍以受控纸质签名为准。
>
> 第一版按“一台设备一个串口”运行；HMS-C 五台共享同一 RS485 总线的多从机调度尚未启用。现场试运行时只接单台 HMS-C，或为每台配置独立串口。

## 项目结构

```
lab_device_manager/
├── __main__.py          # 应用入口：加载配置、启动 Engine 与 Flask
├── config.py            # 配置加载与 session secret 管理
├── sampler.py           # 主采样循环
├── db/                  # SQLite 仓库、R-201 数据模型与迁移
├── experiments/         # R-201 状态机、自动派生、偏差和追溯服务
├── instruments/         # 设备适配器（注射泵、搅拌器、粘度计、基类）
├── modbus/              # Modbus RTU 帧/客户端工具
├── runtime/             # Engine、运行检测器、设备配置类型
└── web/                 # Flask API、平板执行台、设备看板和标签模板

scripts/                 # 探针与调试脚本
tests/                   # pytest 测试集
config.toml              # 默认配置文件
requirements.txt         # 运行时依赖
requirements-dev.txt     # 开发依赖
```

## 开发

### 运行测试

```bash
source .venv/bin/activate
python -m pytest -v
```

当前测试覆盖设备解码、Modbus 帧、Repository、运行时引擎、Web API、认证流程、R-201 状态机、幂等事件、温度检查点、粘度终点、偏差闭环和四设备固定 fixture。

R-201 验收资料：

- `docs/R-201湿化学合成数字化流程蓝图.md`
- `docs/REPORT-R201-V1.md`
- `docs/UAT-R201-01.md`
- `docs/UAT-R201-02.md`

### 添加新设备

1. 在 `lab_device_manager/instruments/` 下实现 `BaseInstrumentAdapter` 子类。
2. 在 `lab_device_manager/instruments/factory.py` 注册设备类型。
3. 在 `config.toml` 中配置 `type` 与串口参数。
4. 添加对应单元测试。

## 安全说明

- 应用默认只监听 `127.0.0.1`。
- 生产/共享环境请：
  - 设置强 `login_password`；
  - 不要提交含密码的 `config.toml`；
  - 通过反向代理启用 HTTPS；
  - 定期轮换 `data/.session_secret`。

## 许可证

MIT
