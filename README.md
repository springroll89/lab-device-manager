# Lab Device Manager

实验室多设备采集管理台 —— 统一接入注射泵、粘度计、搅拌器等串口/Modbus 仪器，提供实时状态监控、运行记录、数据导出与报告生成。

> 本项目为本地实验室工具，默认绑定 `127.0.0.1`，请勿直接暴露到公网。

## 功能特性

- **多协议设备接入**：支持 Modbus RTU（Leadfluid TYD02 注射泵、HMS-C 搅拌器）与自定义二进制串口协议（方瑞粘度计）。
- **实时状态面板**：Web 仪表盘展示各设备当前状态、流速/转速、累计液量、温度、告警等。
- **运行记录管理**：自动识别一次完整运行（start → stop），记录开始/结束时间、操作人员、项目标签、备注。
- **数据导出**：支持 CSV、Excel（runs + samples 多 sheet）、单运行 PDF 报告导出。
- **运行详情页**：展示采样曲线、事件时间线、设置参数与实际液量。
- **本地密码保护**：可配置单密码登录，未认证时页面与 API 均不可访问；关闭登录时作为本地只读采集站运行。
- **补录/Tag 修正**：可对已结束运行补充 operator、project_tag、experiment_tag、remark。

## 支持的设备

| 设备类型 | 型号 | 协议 | 状态 |
|---|---|---|---|
| 注射泵 | Leadfluid TYD02 | Modbus RTU | 已验证 |
| 搅拌器 | HMS-C × 5 | Modbus RTU | 串口参数待实测 |
| 粘度计 | 方瑞 | 自定义二进制串口 | 校验和/帧类型待实测 |

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

如果配置了 `login_password`，会先跳转到登录页；未配置则直接进入仪表盘。

## 项目结构

```
lab_device_manager/
├── __main__.py          # 应用入口：加载配置、启动 Engine 与 Flask
├── config.py            # 配置加载与 session secret 管理
├── sampler.py           # 主采样循环
├── db/                  # SQLite 仓库与数据模型
├── instruments/         # 设备适配器（注射泵、搅拌器、粘度计、基类）
├── modbus/              # Modbus RTU 帧/客户端工具
├── runtime/             # Engine、运行检测器、设备配置类型
└── web/                 # Flask Web 应用、静态页面与前端资源

scripts/                 # 探针与调试脚本
 tests/                  # pytest 测试集
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

当前测试覆盖设备解码、Modbus 帧、Repository、运行时引擎、Web API 与认证流程。

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
