#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Leadfluid 雷弗注射泵 Modbus RTU 只读探测脚本 (RS485)。

安全约束：本脚本只发送功能码 0x04（读输入寄存器），永远不会写保持寄存器、
不会启动/停止泵、不会驱动任何机械动作。仅用于“链路是否打通 + 泵身份识别”。

串口格式（按协议 PDF）: 8 数据位 / 偶校验 EVEN / 1 停止位 (8E1)，CRC-16 低字节在前。
"""
import os
import sys
import time
import select
import struct
import termios

PORT = "/dev/cu.usbserial-BG039GXP"
SLAVE = 1
BAUDS = [9600, 19200, 4800, 38400]  # 泵支持的四种波特率，按可能性排序

# ---- 读输入寄存器地址（功能码 0x04，全部只读、不会动泵）----
REG_COMPANY = 1018   # 公司信息, 10字节 ASCII, 期望 "LeadFluid"
REG_PRODUCT = 1023   # 产品信息, 10字节 ASCII, 期望机型字符串
REG_TEMP    = 1000   # 内部温度, Signed Short (2字节)
REG_ALARM   = 1047   # 报警状态, Unsigned Short (2字节): 0正常 1报警


def crc16(data: bytes) -> int:
    """Modbus RTU CRC-16, poly 0xA001, 低字节在前由调用方处理。"""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if (crc & 1) else (crc >> 1)
    return crc & 0xFFFF


def build_request(slave: int, reg: int, count: int) -> bytes:
    body = bytes([slave, 0x04, (reg >> 8) & 0xFF, reg & 0xFF,
                  (count >> 8) & 0xFF, count & 0xFF])
    c = crc16(body)
    return body + bytes([c & 0xFF, (c >> 8) & 0xFF])  # CRC 低字节在前


def crc_ok(frame: bytes) -> bool:
    if len(frame) < 4:
        return False
    payload, recv = frame[:-2], frame[-2:]
    c = crc16(payload)
    return recv == bytes([c & 0xFF, (c >> 8) & 0xFF])


def open_port(port: str, baud: int):
    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    # 清掉 O_NONBLOCK，改为用 select 控制读超时
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)

    iflag, oflag, cflag, lflag, ispeed, ospeed, cc = termios.tcgetattr(fd)
    iflag = 0
    oflag = 0
    lflag = 0
    cflag |= termios.CS8 | termios.CREAD | termios.CLOCAL | termios.PARENB
    cflag &= ~termios.CSTOPB          # 1 停止位
    cflag &= ~termios.CSIZE           # 清数据位字段
    cflag |= termios.CS8             # 8 数据位
    cflag &= ~termios.PARODD         # PARENB=1, PARODD=0 => 偶校验
    iflag |= termios.IGNPAR          # 忽略校验/帧错误，让字节仍能到达（我们自己校验CRC）
    iflag &= ~(termios.IXON | termios.IXOFF | termios.IXANY)
    try:
        cflag &= ~termios.CRTSCTS    # 关硬件流控
    except AttributeError:
        pass
    cc[termios.VMIN] = 0
    cc[termios.VTIME] = 0
    speed = {4800: termios.B4800, 9600: termios.B9600,
             19200: termios.B19200, 38400: termios.B38400}[baud]
    termios.tcsetattr(fd, termios.TCSANOW,
                      [iflag, oflag, cflag, lflag, speed, speed, cc])
    termios.tcflush(fd, termios.TCIOFLUSH)
    return fd


def transact(fd: int, req: bytes, read_ms: int = 400, gap_ms: int = 60) -> bytes:
    """发送一帧，按 RTU 字符间隙收尾，返回原始回包。"""
    termios.tcflush(fd, termios.TCIOFLUSH)
    time.sleep(0.005)  # 起始前的静止时间（RTU 要求 >=3.5 字符）
    os.write(fd, req)
    # tcdrain 等待发送完成
    try:
        termios.tcdrain(fd)
    except termios.error:
        time.sleep(0.01)

    buf = bytearray()
    deadline = time.monotonic() + read_ms / 1000.0
    while time.monotonic() < deadline:
        r, _, _ = select.select([fd], [], [], gap_ms / 1000.0)
        if not r:
            if buf:        # 已有数据且本次 gap 内无新数据 => 帧结束
                break
            continue
        try:
            chunk = os.read(fd, 256)
        except BlockingIOError:
            continue
        if chunk:
            buf.extend(chunk)
    return bytes(buf)


def decode_str(b: bytes) -> str:
    # 泵把 ASCII 按 16 位寄存器“小端”存放：每对字节需交换 (eL -> Le, da -> ad ...)
    sw = bytearray()
    i = 0
    while i + 1 < len(b):
        sw.append(b[i + 1])
        sw.append(b[i])
        i += 2
    if i < len(b):           # 落单的末字节
        sw.append(b[i])
    return bytes(sw).split(b'\x00', 1)[0].decode('ascii', 'replace').strip()


def try_baud(baud: int):
    print(f"\n--- 尝试 {baud} 8E1 ---")
    fd = open_port(PORT, baud)
    try:
        # 先读公司信息做身份确认
        req = build_request(SLAVE, REG_COMPANY, 5)  # 10字节=5寄存器
        print(f"  发送(公司信息@1018): {req.hex(' ')}")
        resp = transact(fd, req)
        if not resp:
            print("  ✗ 无回包")
            return None
        print(f"  收到 {len(resp)} 字节: {resp.hex(' ')}")
        if len(resp) >= 2 and resp[1] == 0x84:
            print(f"  ✗ 泵返回异常码: {resp.hex(' ')}")
            return None
        if not crc_ok(resp):
            print("  ✗ CRC 校验失败（波特率/校验位可能不对）")
            return None

        print("  ✓ CRC 正确 — 链路打通！")
        n = resp[2] if len(resp) > 2 else 0
        company = decode_str(resp[3:3 + n])
        print(f"  公司信息 = {company!r}")

        # 产品信息
        resp2 = transact(fd, build_request(SLAVE, REG_PRODUCT, 5))
        if crc_ok(resp2) and resp2[1] == 0x04:
            prod = decode_str(resp2[3:3 + resp2[2]])
            print(f"  产品信息 = {prod!r}")

        # 内部温度
        resp3 = transact(fd, build_request(SLAVE, REG_TEMP, 1))
        if crc_ok(resp3) and resp3[1] == 0x04 and resp3[2] == 2:
            temp = struct.unpack('>h', resp3[3:5])[0]
            print(f"  内部温度 = {temp} ℃")

        # 报警状态
        resp4 = transact(fd, build_request(SLAVE, REG_ALARM, 1))
        if crc_ok(resp4) and resp4[1] == 0x04 and resp4[2] == 2:
            alarm = struct.unpack('>H', resp4[3:5])[0]
            print(f"  报警状态 = {alarm} ({'正常' if alarm == 0 else '报警'})")

        return baud
    finally:
        termios.tcflush(fd, termios.TCIOFLUSH)
        os.close(fd)


def main():
    print(f"端口={PORT}  从机地址={SLAVE}")
    for baud in BAUDS:
        ok = try_baud(baud)
        if ok:
            print(f"\n=== 成功: 波特率 {ok}, 8E1 ===")
            return 0
    print("\n✗ 所有波特率均未收到合法回包，需检查：接线(A/B是否接反)、从机地址、模式开关。")
    return 1


if __name__ == "__main__":
    import fcntl  # noqa: 顶层 import 留在 main 之后不影响
    sys.exit(main())
