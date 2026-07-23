"""WHD46-33 寄存器定义与纯数据解码。"""

from __future__ import annotations

REG_START = 1
REG_COUNT = 6
CHANNEL_COUNT = 3


def raw_to_value(raw: int) -> float:
    """把设备的有符号 0.1 精度寄存器值转换为浮点数。"""
    if raw >= 0x8000:
        raw -= 0x10000
    return round(raw / 10.0, 1)


def bytes_to_registers(raw: bytes) -> list[int]:
    """把 Modbus 大端字节流转换为 16 位寄存器。"""
    return [
        int.from_bytes(raw[index : index + 2], "big")
        for index in range(0, len(raw) - 1, 2)
    ]


def decode_channels(raw: bytes) -> list[dict[str, float]]:
    """解码三通道温湿度数据，缺失寄存器按 0 保持旧行为。"""
    registers = bytes_to_registers(raw)
    channels = []
    for index in range(CHANNEL_COUNT):
        register_index = index * 2
        temp_raw = (
            registers[register_index]
            if register_index < len(registers)
            else 0
        )
        humid_raw = (
            registers[register_index + 1]
            if register_index + 1 < len(registers)
            else 0
        )
        channels.append(
            {
                "temp": raw_to_value(temp_raw),
                "humid": raw_to_value(humid_raw),
            }
        )
    return channels
