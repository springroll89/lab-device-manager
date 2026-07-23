"""兼容旧导入路径；新代码使用 ``instruments.whd46`` 包。"""

from lab_device_manager.instruments.whd46 import (
    WHD46Adapter,
    auto_detect_port,
    enum_serial_ports,
    probe_port,
    raw_to_value,
)

_raw_to_value = raw_to_value

__all__ = [
    "WHD46Adapter",
    "_raw_to_value",
    "auto_detect_port",
    "enum_serial_ports",
    "probe_port",
]
