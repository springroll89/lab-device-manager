"""WHD46-33 串口枚举与无状态通信探测。"""

from __future__ import annotations

import time
from typing import Dict, List

import serial
from serial.tools import list_ports

from lab_device_manager.instruments.whd46.protocol import REG_COUNT, REG_START
from lab_device_manager.modbus_io import (
    FC_READ_HOLDING,
    build_read_request,
    parse_response,
)


def parity_code(parity: str) -> str:
    value = parity.upper()
    if value == "NONE":
        return "N"
    if value == "EVEN":
        return "E"
    if value == "ODD":
        return "O"
    raise ValueError(f"unsupported parity {parity}")


def enum_serial_ports() -> List[Dict[str, str]]:
    """返回可用串口，过滤蓝牙、Modem 和打印机虚拟端口。"""
    result = []
    try:
        ports = list(list_ports.comports())
    except Exception:
        return result
    for port in ports:
        try:
            description = (port.description or "").lower()
            hardware_id = (port.hwid or "").lower()
            if any(
                word in description
                for word in (
                    "bluetooth",
                    "bth",
                    "modem",
                    "virtual",
                    "printer",
                    "lpt",
                )
            ):
                continue
            if any(word in hardware_id for word in ("bt", "bth", "blue")):
                continue
            result.append(
                {
                    "device": port.device,
                    "desc": port.description or "未知",
                }
            )
        except Exception:
            continue
    return result


def probe_port(
    port: str,
    *,
    slave: int = 1,
    baudrate: int = 9600,
    parity: str = "NONE",
) -> dict:
    """短暂打开端口验证 WHD46，不保留连接所有权。"""
    connection = None
    try:
        connection = serial.Serial(
            port=port,
            baudrate=baudrate,
            bytesize=serial.EIGHTBITS,
            parity=parity_code(parity),
            stopbits=serial.STOPBITS_ONE,
            timeout=2,
        )
        time.sleep(0.2)
        last_error = "设备无应答（超时）"
        for address in (REG_START, 0):
            try:
                request = build_read_request(
                    slave,
                    FC_READ_HOLDING,
                    address,
                    REG_COUNT,
                )
                connection.reset_input_buffer()
                connection.write(request)
                time.sleep(0.1)
                response = connection.read(1024)
                if len(response) < 5:
                    continue
                parse_response(response, slave, FC_READ_HOLDING)
                return {"ok": True, "port": port, "error": "", "detail": ""}
            except Exception as exc:
                last_error = str(exc)
        return {
            "ok": False,
            "port": port,
            "error": "设备无响应",
            "detail": (
                f"端口 {port} 已打开，但 Modbus 设备无响应\n"
                f"从机地址: {slave}  波特率: {baudrate}\n"
                f"最后错误: {last_error}\n\n"
                "请检查：\n"
                "1. 设备是否已通电\n"
                "2. A(+) / B(-) 接线是否正确 (WHD46 端子 30/31)\n"
                "3. 从机地址与面板 COMM→ADDR 一致\n"
                "4. 波特率与面板 COMM→bAud 一致\n"
                "5. 末端 A/B 间是否加了终端电阻"
            ),
        }
    except Exception as exc:
        message = str(exc)
        occupied = "Access is denied" in message or "拒绝访问" in message
        return {
            "ok": False,
            "port": port,
            "error": "端口被占用" if occupied else "打开失败",
            "detail": (
                f"端口 {port} 被其他程序占用"
                if occupied
                else f"端口 {port} 无法打开: {message}"
            ),
        }
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


def auto_detect_port(
    *,
    slave: int = 1,
    baudrate: int = 9600,
    parity: str = "NONE",
) -> dict:
    """探测所有串口，返回第一台可通信的 WHD46。"""
    ports = enum_serial_ports()
    if not ports:
        return {
            "ok": False,
            "port": "",
            "error": "无可用端口",
            "detail": "未检测到串口，请确认 USB-RS485 适配器已插入",
        }
    last_result = None
    for item in ports:
        last_result = probe_port(
            item["device"],
            slave=slave,
            baudrate=baudrate,
            parity=parity,
        )
        if last_result["ok"]:
            return last_result
    return last_result or {
        "ok": False,
        "port": "",
        "error": "连接失败",
        "detail": "所有端口均连接失败",
    }
