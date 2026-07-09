from __future__ import annotations
import struct
import time
from lab_device_manager.instruments.base import StatusSnapshot

# HMS-C heating magnetic stirrer (上海小聪科技 / Xiaocong).
# Modbus RTU, holding registers. Frame address = register# - 40001.
# Read 40001..40016 (16 regs) in one request.

BLOCK_COUNT = 16  # regs 0..15

ALARM_BITS = {
    0: "内部传感器故障",
    1: "内部传感器温度报警",
    2: "外部传感器故障",
    3: "外部传感器超温",
    4: "电机堵转",
    5: "传感器跌落报警",
    6: "传感器保护报警1",
    7: "传感器震荡报警",
    8: "传感器保护报警2",
    9: "传感器保护报警3",
    10: "传感器保护报警4",
}


def _u16(block: bytes, reg: int) -> int:
    """Decode register `reg` (0-based within block) as big-endian uint16."""
    off = reg * 2
    return struct.unpack(">H", block[off:off + 2])[0]


def decode_alarm_flags(alarm_bits: int) -> list:
    return [name for bit, name in ALARM_BITS.items() if alarm_bits & (1 << bit)]


def derive_state(alarm_bits: int, heater_on: int, stirrer_on: int) -> str:
    if alarm_bits:
        return "alarm"
    if heater_on == 1 or stirrer_on == 1:
        return "running"
    return "stopped"


class StirrerAdapter:
    """HMS-C stirrer, READ-ONLY Modbus RTU. Exposes stirrer-specific fields
    via StatusSnapshot.metrics; common fields (state/temp/alarm) on the snapshot."""

    def __init__(self, client):
        self.client = client
        self._machine_type = None

    def identity(self) -> str:
        if self._machine_type is None:
            raw = self.client.read_holding_registers(0, 1)  # 40001
            self._machine_type = _u16(raw, 0)
        return f"Stirrer type {self._machine_type}"

    def read_status(self) -> StatusSnapshot:
        block = self.client.read_holding_registers(0, BLOCK_COUNT)
        machine_type = _u16(block, 0)
        self._machine_type = machine_type
        display_mode = _u16(block, 1)
        temp_raw = _u16(block, 2)
        set_temp_raw = _u16(block, 3)
        speed = _u16(block, 4)
        set_speed = _u16(block, 5)
        temp_min = _u16(block, 6)
        temp_max = _u16(block, 7)
        speed_min = _u16(block, 8)
        speed_max = _u16(block, 9)
        alarm_bits = _u16(block, 10)
        heater_on = _u16(block, 11)
        stirrer_on = _u16(block, 12)
        mode = _u16(block, 13)
        power_loss_mem = _u16(block, 14)
        sensor_connected = _u16(block, 15)

        scale = 10.0 if display_mode == 1 else 1.0
        temp_c = temp_raw / scale
        set_temp = set_temp_raw / scale

        return StatusSnapshot(
            timestamp=time.time(),
            state=derive_state(alarm_bits, heater_on, stirrer_on),
            work_mode=f"模式{mode}" if mode else "",
            device_id=f"Stirrer type {machine_type}",
            temp_c=temp_c,
            alarm=bool(alarm_bits),
            error_code=alarm_bits,
            metrics={
                "machine_type": machine_type,
                "display_mode": display_mode,
                "set_temp": set_temp,
                "speed": speed,
                "set_speed": set_speed,
                "temp_min": temp_min,
                "temp_max": temp_max,
                "speed_min": speed_min,
                "speed_max": speed_max,
                "heater_on": bool(heater_on),
                "stirrer_on": bool(stirrer_on),
                "mode": mode,
                "power_loss_mem": bool(power_loss_mem),
                "sensor_connected": bool(sensor_connected),
                "alarm_flags": decode_alarm_flags(alarm_bits),
            },
        )
