from __future__ import annotations
import time
import serial
from serial.tools import list_ports
from typing import Optional, List, Dict
from lab_device_manager.instruments.base import StatusSnapshot
from lab_device_manager.modbus_io import ModbusClient, SerialTransport, build_read_request, parse_response, FC_READ_HOLDING


def _raw_to_value(raw: int) -> float:
    if raw >= 0x8000:
        raw -= 0x10000
    return round(raw / 10.0, 1)


def enum_serial_ports() -> List[Dict[str, str]]:
    """返回可用串口列表，过滤掉蓝牙/Modem等非RS485端口"""
    result = []
    try:
        raw = list(list_ports.comports())
        if not raw:
            return result
        for p in raw:
            try:
                desc = (p.description or "").lower()
                hwid = (p.hwid or "").lower()
                skip_words = ["bluetooth", "bth", "modem", "virtual", "printer", "lpt"]
                skip_hwid = ["bt", "bth", "blue"]
                if any(k in desc for k in skip_words):
                    continue
                if any(k in hwid for k in skip_hwid):
                    continue
                result.append({"device": p.device, "desc": p.description or "未知"})
            except Exception:
                pass
    except Exception:
        pass
    return result


class WHD46Adapter:
    """WHD46-33 温湿度控制器适配器 - 支持3通道温湿度数据采集"""

    REG_START = 1
    REG_COUNT = 6

    def __init__(self, client=None, slave: int = 1, baudrate: int = 9600, parity: str = "NONE"):
        self.client: Optional[ModbusClient] = client
        self.slave = slave
        self.baudrate = baudrate
        self.parity = parity
        self._device_id = None
        self._connected_port = ""
        self._transport: Optional[SerialTransport] = None

    @property
    def is_connected(self) -> bool:
        if self._transport is None:
            return False
        try:
            return self._transport._ser is not None and self._transport._ser.is_open
        except Exception:
            return False

    @property
    def connected_port(self) -> str:
        return self._connected_port

    def _try_raw_modbus(self, port: str) -> bool:
        """用 pyserial 直接发 Modbus RTU 帧验证通信"""
        try:
            ser = serial.Serial(
                port=port, baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS, parity=_parity_code(self.parity),
                stopbits=serial.STOPBITS_ONE, timeout=2
            )
        except Exception:
            return False

        time.sleep(0.2)
        for addr in [self.REG_START, 0]:
            try:
                req = build_read_request(self.slave, FC_READ_HOLDING, addr, self.REG_COUNT)
                ser.reset_input_buffer()
                ser.write(req)
                time.sleep(0.1)
                resp = ser.read(1024)
                if len(resp) < 5:
                    continue
                if resp[0] != self.slave or resp[1] != FC_READ_HOLDING:
                    continue
                ser.close()
                return True
            except Exception:
                continue

        ser.close()
        return False

    def connect(self, port: str) -> Dict:
        """
        打开指定串口并验证 Modbus 通信。
        返回: {"ok": bool, "error": str, "detail": str}
        """
        if self.is_connected:
            return {"ok": True, "error": "", "detail": f"已连接到 {self._connected_port}"}

        try:
            self._transport = SerialTransport(port=port, baudrate=self.baudrate, parity=self.parity)
            self._transport.open()
        except Exception as e:
            err = str(e)
            if "Access is denied" in err or "拒绝访问" in err:
                return {"ok": False, "error": "端口被占用", "detail": f"端口 {port} 被其他程序占用"}
            return {"ok": False, "error": "打开失败", "detail": f"端口 {port} 无法打开: {err}"}

        time.sleep(0.2)

        last_err = ""
        for addr in [self.REG_START, 0]:
            try:
                req = build_read_request(self.slave, FC_READ_HOLDING, addr, self.REG_COUNT)
                resp = self._transport.write(req)
                self._transport.flush()
                time.sleep(0.1)
                buf = self._transport.read_wait(0.4)
                if buf:
                    parse_response(buf, self.slave, FC_READ_HOLDING)
                    self.client = ModbusClient(self._transport, slave=self.slave, read_timeout=1.0)
                    self._connected_port = port
                    self._device_id = None
                    return {"ok": True, "error": "", "detail": ""}
                else:
                    last_err = "设备无应答（超时）"
            except Exception as e:
                last_err = str(e)

        self.close()
        if self._try_raw_modbus(port):
            self._transport = SerialTransport(port=port, baudrate=self.baudrate, parity=self.parity)
            self._transport.open()
            self.client = ModbusClient(self._transport, slave=self.slave, read_timeout=1.0)
            self._connected_port = port
            self._device_id = None
            return {"ok": True, "error": "", "detail": ""}

        return {
            "ok": False, "error": "设备无响应",
            "detail": (
                f"端口 {port} 已打开，但 Modbus 设备无响应\n"
                f"从机地址: {self.slave}  波特率: {self.baudrate}\n"
                f"最后错误: {last_err}\n\n"
                f"请检查：\n"
                f"1. 设备是否已通电\n"
                f"2. A(+) / B(-) 接线是否正确 (WHD46 端子 30/31)\n"
                f"3. 从机地址与面板 COMM→ADDR 一致\n"
                f"4. 波特率与面板 COMM→bAud 一致\n"
                f"5. 末端 A/B 间是否加了终端电阻"
            ),
        }

    def auto_detect(self) -> Dict:
        """遍历所有可用串口，返回第一个成功的结果"""
        ports = enum_serial_ports()
        if not ports:
            return {"ok": False, "error": "无可用端口", "detail": "未检测到串口，请确认USB-485适配器已插入"}

        last = None
        for p in ports:
            dev = p["device"]
            r = self.connect(dev)
            if r["ok"]:
                return r
            last = r

        return last or {"ok": False, "error": "连接失败", "detail": "所有端口均连接失败"}

    def close(self) -> None:
        """关闭连接"""
        if self._transport:
            try:
                self._transport.close()
            except Exception:
                pass
        self._transport = None
        self.client = None
        self._connected_port = ""

    def _bytes_to_registers(self, raw: bytes) -> list:
        """将原始字节转换为寄存器整数列表（每个寄存器2字节，大端序）"""
        registers = []
        for i in range(0, len(raw), 2):
            if i + 2 <= len(raw):
                registers.append(int.from_bytes(raw[i:i+2], "big"))
        return registers

    def identity(self) -> str:
        if self._device_id is None:
            try:
                raw = self.client.read_holding_registers(self.REG_START, 2)
                registers = self._bytes_to_registers(raw)
                if registers:
                    t1 = _raw_to_value(registers[0])
                    h1 = _raw_to_value(registers[1])
                    self._device_id = f"WHD46-33 CH1={t1}C/{h1}%RH"
                else:
                    self._device_id = "WHD46-33"
            except Exception:
                self._device_id = "WHD46-33"
        return self._device_id

    def read_status(self) -> StatusSnapshot:
        c = self.client
        raw = c.read_holding_registers(self.REG_START, self.REG_COUNT)
        registers = self._bytes_to_registers(raw)

        ch_data = []
        for i in range(3):
            t_raw = registers[i * 2] if i * 2 < len(registers) else 0
            h_raw = registers[i * 2 + 1] if i * 2 + 1 < len(registers) else 0
            temp = _raw_to_value(t_raw)
            humid = _raw_to_value(h_raw)
            ch_data.append({"temp": temp, "humid": humid})

        metrics = {
            "channels": ch_data,
            "ch1_temp_c": ch_data[0]["temp"],
            "ch1_humid_rh": ch_data[0]["humid"],
            "ch2_temp_c": ch_data[1]["temp"],
            "ch2_humid_rh": ch_data[1]["humid"],
            "ch3_temp_c": ch_data[2]["temp"],
            "ch3_humid_rh": ch_data[2]["humid"],
        }

        avg_temp = sum(ch["temp"] for ch in ch_data) / 3.0
        avg_humid = sum(ch["humid"] for ch in ch_data) / 3.0

        return StatusSnapshot(
            timestamp=time.time(),
            state="running",
            work_mode="monitoring",
            device_id=self.identity(),
            temp_c=avg_temp,
            metrics=metrics,
        )

    def read_channels(self) -> dict:
        """读取3通道温湿度详细数据"""
        c = self.client
        raw = None
        for attempt in range(3):
            try:
                raw = c.read_holding_registers(self.REG_START, self.REG_COUNT)
                if raw:
                    break
            except Exception:
                if attempt < 2:
                    time.sleep(0.2)
        if not raw:
            return {"timestamp": time.time(), "channels": [], "avg_temp_c": 0, "avg_humid_rh": 0}
        registers = self._bytes_to_registers(raw)

        channels = []
        for i in range(3):
            t_raw = registers[i * 2] if i * 2 < len(registers) else 0
            h_raw = registers[i * 2 + 1] if i * 2 + 1 < len(registers) else 0
            temp = _raw_to_value(t_raw)
            humid = _raw_to_value(h_raw)
            channels.append({
                "channel": i + 1,
                "temp_c": temp,
                "humid_rh": humid,
            })

        avg_temp = sum(ch["temp_c"] for ch in channels) / 3.0
        avg_humid = sum(ch["humid_rh"] for ch in channels) / 3.0

        return {
            "timestamp": time.time(),
            "channels": channels,
            "avg_temp_c": avg_temp,
            "avg_humid_rh": avg_humid,
        }

    def read_channel(self, channel: int) -> dict:
        """读取指定通道的温湿度数据"""
        if channel < 1 or channel > 3:
            return {}
        c = self.client
        raw = c.read_holding_registers(self.REG_START, self.REG_COUNT)
        registers = self._bytes_to_registers(raw)
        
        idx = (channel - 1) * 2
        if idx + 1 < len(registers):
            t_raw = registers[idx]
            h_raw = registers[idx + 1]
            return {
                "channel": channel,
                "temp": _raw_to_value(t_raw),
                "humid": _raw_to_value(h_raw),
            }
        return {}


def _parity_code(parity: str) -> str:
    p = parity.upper()
    if p == "NONE":
        return "N"
    if p == "EVEN":
        return "E"
    if p == "ODD":
        return "O"
    raise ValueError(f"unsupported parity {parity}")