"""WHD46-33 只读设备适配器。"""

from __future__ import annotations

import time

from lab_device_manager.instruments.base import StatusSnapshot
from lab_device_manager.instruments.whd46.protocol import (
    REG_COUNT,
    REG_START,
    bytes_to_registers,
    decode_channels,
    raw_to_value,
)


class WHD46Adapter:
    """通过统一 ModbusClient 读取三通道温湿度。"""

    REG_START = REG_START
    REG_COUNT = REG_COUNT

    def __init__(self, client, **_compatibility_options):
        self.client = client
        self._device_id = None

    def identity(self) -> str:
        if self._device_id is None:
            try:
                raw = self.client.read_holding_registers(self.REG_START, 2)
                registers = bytes_to_registers(raw)
                if registers:
                    temp = raw_to_value(registers[0])
                    humid = raw_to_value(registers[1])
                    self._device_id = (
                        f"WHD46-33 CH1={temp}C/{humid}%RH"
                    )
                else:
                    self._device_id = "WHD46-33"
            except Exception:
                self._device_id = "WHD46-33"
        return self._device_id

    def read_status(self) -> StatusSnapshot:
        raw = self.client.read_holding_registers(
            self.REG_START,
            self.REG_COUNT,
        )
        channels = decode_channels(raw)
        avg_temp = sum(item["temp"] for item in channels) / len(channels)
        avg_humid = sum(item["humid"] for item in channels) / len(channels)
        metrics = {
            "channels": channels,
            "ch1_temp_c": channels[0]["temp"],
            "ch1_humid_rh": channels[0]["humid"],
            "ch2_temp_c": channels[1]["temp"],
            "ch2_humid_rh": channels[1]["humid"],
            "ch3_temp_c": channels[2]["temp"],
            "ch3_humid_rh": channels[2]["humid"],
            "avg_humid_rh": avg_humid,
        }
        return StatusSnapshot(
            timestamp=time.time(),
            state="running",
            work_mode="monitoring",
            device_id=self.identity(),
            temp_c=avg_temp,
            metrics=metrics,
        )

    def read_channels(self) -> dict:
        raw = None
        for attempt in range(3):
            try:
                raw = self.client.read_holding_registers(
                    self.REG_START,
                    self.REG_COUNT,
                )
                if raw:
                    break
            except Exception:
                if attempt < 2:
                    time.sleep(0.2)
        if not raw:
            return {
                "timestamp": time.time(),
                "channels": [],
                "avg_temp_c": 0,
                "avg_humid_rh": 0,
            }
        decoded = decode_channels(raw)
        channels = [
            {
                "channel": index + 1,
                "temp_c": item["temp"],
                "humid_rh": item["humid"],
            }
            for index, item in enumerate(decoded)
        ]
        return {
            "timestamp": time.time(),
            "channels": channels,
            "avg_temp_c": (
                sum(item["temp_c"] for item in channels) / len(channels)
            ),
            "avg_humid_rh": (
                sum(item["humid_rh"] for item in channels) / len(channels)
            ),
        }

    def read_channel(self, channel: int) -> dict:
        if channel < 1 or channel > 3:
            return {}
        raw = self.client.read_holding_registers(
            self.REG_START,
            self.REG_COUNT,
        )
        item = decode_channels(raw)[channel - 1]
        return {
            "channel": channel,
            "temp": item["temp"],
            "humid": item["humid"],
        }
