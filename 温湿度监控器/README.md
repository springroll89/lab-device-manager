# WHD46-33 温湿度监控器

本目录用于集中说明 WHD46-33 的设备资料与维护入口。运行时代码已经按职责拆分为独立模块：

```text
lab_device_manager/instruments/whd46/
├── adapter.py      # 只读设备适配器与状态快照
├── protocol.py     # 寄存器定义、字节解析、三通道解码
└── discovery.py    # 串口枚举、自动发现与无状态通信探测

lab_device_manager/web/whd46.py
└── WHD46 专属 Web API、连接管理与 CSV 导出
```

连接生命周期统一由 `lab_device_manager/runtime/engine.py` 管理。设备探测只短暂打开串口，确认通信后立即释放；正式连接、采样、重连和断开均由 Engine 接管，避免 Web 接口与后台采样同时占用同一串口。

## 配置示例

```toml
[[devices]]
name        = "whd46-33"
type        = "whd46"
alias       = "环境温湿度监控器"
serial_port = "/dev/cu.usbserial-XXXXXX"
baudrate    = 9600
parity      = "NONE"
modbus_addr = 1
```

兼容入口 `lab_device_manager/instruments/whd46_33.py` 暂时保留，已有导入无需立即修改；新代码应直接从 `lab_device_manager.instruments.whd46` 导入。
