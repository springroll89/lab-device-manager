from __future__ import annotations
import struct
import time
from lab_device_manager.instruments.base import StatusSnapshot

VOLUME_UNITS = {0: "nL", 1: "uL", 2: "mL", 3: "L"}
WORK_MODES = {0: "仅注入", 1: "仅抽取", 2: "抽取/注入", 3: "注入/抽取", 4: "连续"}

# Syringe code map (4021). Extend as more codes are verified on real hardware.
SYRINGE_NAMES = {
    9: "BD Glass 100ml",
}

# Inner diameter (mm) for known syringe codes. Used to compute mm/step.
# BD Glass 100ml ≈ 26.7 mm — refine once measured/calibrated.
SYRINGE_INNER_DIAMETERS = {
    9: 26.7,
}


def _ul_per_step(target_volume: float, target_unit: str, steps: int) -> Optional[float]:
    """Compute delivered volume per step in μL/step."""
    if steps <= 0 or target_volume is None:
        return None
    to_ul = {"nL": 0.001, "uL": 1.0, "mL": 1000.0, "L": 1_000_000.0}
    ul = target_volume * to_ul.get(target_unit, 1000.0)
    return ul / steps


def _mm_per_step(ul_per_step: Optional[float], inner_diam_mm: Optional[float]) -> Optional[float]:
    """Convert μL/step to mm/step using syringe inner diameter."""
    if ul_per_step is None or not inner_diam_mm or inner_diam_mm <= 0:
        return None
    area = 3.141592653589793 * (inner_diam_mm / 2.0) ** 2  # mm²
    return ul_per_step / area  # μL = mm³


def decode_ascii(raw: bytes) -> str:
    """Pump stores ASCII as 16-bit little-endian words: swap each byte pair."""
    sw = bytearray()
    i = 0
    while i + 1 < len(raw):
        sw.append(raw[i + 1])
        sw.append(raw[i])
        i += 2
    if i < len(raw):
        sw.append(raw[i])
    return bytes(sw).split(b"\x00", 1)[0].decode("ascii", "replace").strip()


def _reorder32(raw: bytes, wordorder: str) -> bytes:
    if wordorder == "ABCD":
        return bytes(raw[:4])
    if wordorder == "CDAB":
        return bytes([raw[2], raw[3], raw[0], raw[1]])
    raise ValueError(f"bad wordorder {wordorder}")


def decode_float(raw: bytes, wordorder: str) -> float:
    return struct.unpack(">f", _reorder32(raw, wordorder))[0]


def decode_uint32(raw: bytes, wordorder: str) -> int:
    return struct.unpack(">I", _reorder32(raw, wordorder))[0]


def decode_uint16(raw: bytes) -> int:
    return struct.unpack(">H", raw[:2])[0]


def decode_int16(raw: bytes) -> int:
    return struct.unpack(">h", raw[:2])[0]


def derive_state(run: int, pause: int, dispense: int, alarm: int) -> str:
    if alarm:
        return "alarm"
    if pause == 1:
        return "paused"
    if run == 1 or dispense == 1:
        return "running"
    return "stopped"


def _read_mode_setpoints(client, mode, wo):
    """Read target volume + flow rate(s) from the active mode-specific data structure."""
    result = {"target_volume": None, "target_unit": "",
              "inject_rate": None, "inject_rate_unit": "",
              "extract_rate": None, "extract_rate_unit": ""}
    RATE_UNITS = {0: "nL/min", 1: "uL/min", 2: "mL/min"}
    try:
        # 4008 selects the active parameter group (0/1/2). Each group is offset
        # by the structure size documented for the current work mode.
        group = decode_uint16(client.read_holding_registers(4008, 1))
        if group not in (0, 1, 2):
            group = 0
        if mode == 0:      # 仅注入 @4128, 6 regs per group
            base = 4128 + 6 * group
            s = client.read_holding_registers(base, 6)
            result["target_volume"] = decode_float(s[0:4], wo)
            result["inject_rate"] = decode_float(s[4:8], wo)
            result["target_unit"] = VOLUME_UNITS.get(decode_uint16(s[8:10]), "")
            result["inject_rate_unit"] = RATE_UNITS.get(decode_uint16(s[10:12]), "")
        elif mode == 1:    # 仅抽取 @4146, 6 regs per group
            base = 4146 + 6 * group
            s = client.read_holding_registers(base, 6)
            result["target_volume"] = decode_float(s[0:4], wo)
            result["inject_rate"] = decode_float(s[4:8], wo)
            result["target_unit"] = VOLUME_UNITS.get(decode_uint16(s[8:10]), "")
            result["inject_rate_unit"] = RATE_UNITS.get(decode_uint16(s[10:12]), "")
        elif mode in (2, 3):  # 抽取注入@4164 / 注入抽取@4194, 10 regs per group
            base = (4164 if mode == 2 else 4194) + 10 * group
            s = client.read_holding_registers(base, 10)
            result["target_volume"] = decode_float(s[0:4], wo)
            result["inject_rate"] = decode_float(s[4:8], wo)
            result["extract_rate"] = decode_float(s[8:12], wo)
            result["target_unit"] = VOLUME_UNITS.get(decode_uint16(s[12:14]), "")
            result["inject_rate_unit"] = RATE_UNITS.get(decode_uint16(s[14:16]), "")
            result["extract_rate_unit"] = RATE_UNITS.get(decode_uint16(s[16:18]), "")
        elif mode == 4:    # 连续 @4015
            s = client.read_holding_registers(4015, 2)
            result["inject_rate"] = decode_float(s[0:4], wo)
            # Continuous-mode rate unit is in holding register 4022.
            result["inject_rate_unit"] = RATE_UNITS.get(
                decode_uint16(client.read_holding_registers(4022, 1)), "")
    except Exception:
        pass   # mode structure unreadable → leave None
    return result


class TYD02Adapter:
    """Reads TYD02 status over Modbus into a StatusSnapshot. READ-ONLY."""

    REG_TEMP = 1000
    REG_INJECT_RPM = 1002          # float, 2 regs
    REG_CUR_STEPS = 1004           # u32 (cur steps) + next u32 (req steps)
    REG_CYCLES = 1012
    REG_COMPANY = 1018             # 5 regs
    REG_PRODUCT = 1023             # 5 regs
    REG_ACC_VOL = 1032             # float; block 1032..1042 (11 regs)
    REG_ELAPSED_MS = 1043          # u32 (elapsed) + u32 (remaining)
    REG_ALARM = 1047
    REG_MODE = 4017                # holding
    REG_RUN = 4126                 # holding
    REG_PAUSE = 4024               # holding
    REG_DISPENSE = 4025            # holding

    def __init__(self, client, slave: int = 1, wordorder: str = "CDAB"):
        self.client = client
        self.slave = slave
        self.wordorder = wordorder
        self._device_id = None

    def identity(self) -> str:
        if self._device_id is None:
            co = decode_ascii(self.client.read_input_registers(self.REG_COMPANY, 5))
            pr = decode_ascii(self.client.read_input_registers(self.REG_PRODUCT, 5))
            self._device_id = f"{co} {pr}".strip()
        return self._device_id

    def detect_wordorder(self) -> str:
        """Pick ABCD/CDAB by checking current<=required steps under each order."""
        raw = self.client.read_input_registers(self.REG_CUR_STEPS, 4)
        for order in ("CDAB", "ABCD"):
            cur = decode_uint32(raw[0:4], order)
            req = decode_uint32(raw[4:8], order)
            if 0 <= cur <= req:
                return order
        return self.wordorder

    def read_status(self) -> StatusSnapshot:
        c = self.client
        wo = self.wordorder
        temp = decode_int16(c.read_input_registers(self.REG_TEMP, 1))
        inject_rpm = decode_float(c.read_input_registers(self.REG_INJECT_RPM, 2), wo)
        steps = c.read_input_registers(self.REG_CUR_STEPS, 4)
        cur_steps = decode_uint32(steps[0:4], wo)
        req_steps = decode_uint32(steps[4:8], wo)
        # 1008 = current run total time (ms); 1043/1045 = elapsed/remaining ms.
        total_time_raw = c.read_input_registers(1008, 2)
        total_time_ms = decode_uint32(total_time_raw, wo)
        cycles = decode_uint16(c.read_input_registers(self.REG_CYCLES, 1))
        vb = c.read_input_registers(self.REG_ACC_VOL, 11)
        acc = decode_float(vb[0:4], wo)
        acc_u = VOLUME_UNITS.get(decode_uint16(vb[4:6]), "")
        con = decode_float(vb[6:10], wo)
        con_u = VOLUME_UNITS.get(decode_uint16(vb[10:12]), "")
        rem = decode_float(vb[12:16], wo)
        rem_u = VOLUME_UNITS.get(decode_uint16(vb[16:18]), "")
        tb = c.read_input_registers(self.REG_ELAPSED_MS, 4)
        elapsed = decode_uint32(tb[0:4], wo)
        remaining = decode_uint32(tb[4:8], wo)
        alarm = decode_uint16(c.read_input_registers(self.REG_ALARM, 1))
        mode = decode_uint16(c.read_holding_registers(self.REG_MODE, 1))
        run = decode_uint16(c.read_holding_registers(self.REG_RUN, 1))
        pause = decode_uint16(c.read_holding_registers(self.REG_PAUSE, 1))
        dispense = decode_uint16(c.read_holding_registers(self.REG_DISPENSE, 1))
        # The step counter reports cur_steps == req_steps on this firmware, so
        # step-based progress is unreliable. Use elapsed/total_run_time instead.
        progress = None
        if total_time_ms and elapsed is not None:
            progress = 100.0 * elapsed / total_time_ms
        elif req_steps:
            progress = 100.0 * cur_steps / req_steps
        metrics: dict = {}
        # --- rich config (mode structure + process settings + syringe) ---
        ms = _read_mode_setpoints(c, mode, wo)
        pause_h = decode_uint16(c.read_holding_registers(4093, 1))
        pause_m = decode_uint16(c.read_holding_registers(4094, 1))
        pause_s = decode_uint16(c.read_holding_registers(4095, 1))
        pause_ms_raw = decode_uint16(c.read_holding_registers(4096, 1))
        syr_code = decode_uint16(c.read_holding_registers(4021, 1))
        syr_cap_raw = c.read_holding_registers(4090, 2)
        syr_unit = decode_uint16(c.read_holding_registers(4092, 1))
        syr_inner_raw = c.read_holding_registers(4088, 2)
        syr_inner_mm = decode_float(syr_inner_raw, wo)
        # Custom inner diameter register is often unconfigured (0.001 mm); fall back
        # to the known-code table so step-length calculation is meaningful.
        effective_inner_mm = syr_inner_mm if syr_inner_mm and syr_inner_mm > 1.0 else SYRINGE_INNER_DIAMETERS.get(syr_code)
        ul_per_step = _ul_per_step(ms["target_volume"], ms["target_unit"], req_steps)
        mm_per_step = _mm_per_step(ul_per_step, effective_inner_mm)
        metrics["target_volume"] = ms["target_volume"]
        metrics["target_unit"] = ms["target_unit"]
        metrics["inject_rate"] = ms["inject_rate"]
        metrics["inject_rate_unit"] = ms["inject_rate_unit"]
        metrics["extract_rate"] = ms["extract_rate"]
        metrics["extract_rate_unit"] = ms["extract_rate_unit"]
        metrics["pause_delay_ms"] = pause_h * 3600000 + pause_m * 60000 + pause_s * 1000 + pause_ms_raw
        metrics["repeat_count"] = decode_uint16(c.read_holding_registers(4097, 1))
        metrics["force"] = decode_uint16(c.read_holding_registers(4027, 1))
        metrics["stall_alarm_enabled"] = bool(decode_uint16(c.read_holding_registers(4087, 1)))
        metrics["syringe_code"] = syr_code
        metrics["syringe_name"] = SYRINGE_NAMES.get(syr_code, f"Code {syr_code}")
        metrics["syringe_capacity"] = decode_float(syr_cap_raw, wo)
        metrics["syringe_unit_code"] = syr_unit
        metrics["syringe_inner_diameter_mm"] = syr_inner_mm
        metrics["step_length_ul_per_step"] = ul_per_step
        metrics["step_length_mm_per_step"] = mm_per_step
        # diagnostics to help calibrate progress on real hardware
        metrics["_raw_cur_steps"] = cur_steps
        metrics["_raw_req_steps"] = req_steps
        metrics["_raw_total_time_ms"] = total_time_ms
        metrics["_raw_elapsed_ms"] = elapsed
        metrics["_raw_remaining_ms"] = remaining
        return StatusSnapshot(
            timestamp=time.time(),
            state=derive_state(run, pause, dispense, alarm),
            work_mode=WORK_MODES.get(mode, str(mode)),
            device_id=self.identity(),
            flow_rpm=inject_rpm,
            acc_volume=acc, acc_unit=acc_u,
            consumed_volume=con, consumed_unit=con_u,
            remaining_volume=rem, remaining_unit=rem_u,
            elapsed_ms=elapsed, remaining_ms=remaining,
            cycles=cycles, progress_pct=progress,
            temp_c=float(temp), alarm=bool(alarm),
            metrics=metrics,
        )
