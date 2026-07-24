import struct
import pytest
from lab_device_manager.instruments.leadfluid_tyd02 import TYD02Adapter, derive_state


def f32(v, wo="CDAB"):
    b = struct.pack(">f", v)
    return bytes([b[2], b[3], b[0], b[1]]) if wo == "CDAB" else b


def u32(v, wo="CDAB"):
    b = struct.pack(">I", v)
    return bytes([b[2], b[3], b[0], b[1]]) if wo == "CDAB" else b


def u16(v):
    return struct.pack(">H", v)


def s16(v):
    return struct.pack(">h", v)


def enc_ascii(s: str) -> bytes:
    b = s.encode("ascii")
    sw = bytearray()
    i = 0
    while i + 1 < len(b):
        sw.append(b[i + 1]); sw.append(b[i]); i += 2
    if i < len(b):
        sw.append(0x00); sw.append(b[i])
    while len(sw) < 10:
        sw.append(0x00); sw.append(0x00)
    return bytes(sw[:10])


class FakeClient:
    def __init__(self, inp, hold):
        self.inp = inp
        self.hold = hold
    def read_input_registers(self, reg, count):
        return self.inp[(reg, count)]
    def read_holding_registers(self, reg, count):
        return self.hold[(reg, count)]


def _adapter_inputs():
    """Standard input + holding register dicts used by read_status. Return (inp, hold).

    Callers may mutate `hold` to add registers before constructing an adapter.
    """
    inp = {
        (1000, 1): s16(42),
        (1002, 2): f32(50.0),
        (1004, 4): u32(500) + u32(1000),       # cur=500 req=1000 -> 50% fallback
        (1008, 2): u32(240000),                # total run time (ms)
        (1012, 1): u16(3),
        (1018, 5): bytes.fromhex("654c64616c4669750064"),   # "LeadFluid"
        (1023, 5): enc_ascii("TYD02"),
        (1032, 11): (f32(12.5) + u16(2) + f32(8.0) + u16(2)
                     + f32(4.5) + u16(2) + f32(30.0)),
        (1043, 4): u32(120000) + u32(60000),
        (1047, 1): u16(0),
    }
    hold = {
        (4008, 1): u16(0),    # active parameter group 0
        (4017, 1): u16(0),    # 仅注入
        (4126, 1): u16(1),    # run
        (4024, 1): u16(0),    # not paused
        (4025, 1): u16(1),    # dispense active
        # --- rich config holding registers (defaults so read_status succeeds) ---
        (4021, 1): u16(0),                 # syringe code
        (4027, 1): u16(0),                 # force
        (4087, 1): u16(0),                 # stall alarm enabled
        (4088, 2): f32(0.001),              # custom syringe inner diameter (mm), usually unconfigured
        (4090, 2): f32(0.0),               # syringe capacity
        (4092, 1): u16(0),                 # syringe unit
        (4093, 1): u16(0),                 # pause h
        (4094, 1): u16(0),                 # pause m
        (4095, 1): u16(0),                 # pause s
        (4096, 1): u16(0),                 # pause ms
        (4097, 1): u16(0),                 # repeat count
        (4128, 6): f32(0.0) + f32(0.0) + u16(0) + u16(0),  # mode-0 structure group 0
    }
    return inp, hold


def _adapter(inp=None, hold=None):
    base_inp, base_hold = _adapter_inputs()
    if inp is not None:
        base_inp.update(inp)
    if hold is not None:
        base_hold.update(hold)
    return TYD02Adapter(FakeClient(base_inp, base_hold), slave=1, wordorder="CDAB")


def test_identity():
    a = _adapter()
    assert a.identity() == "LeadFluid TYD02"


def test_read_status_decodes_fields():
    s = _adapter().read_status()
    assert s.device_id == "LeadFluid TYD02"
    assert s.state == "running"
    assert s.work_mode == "仅注入"
    assert s.temp_c == 42.0
    assert s.flow_rpm == pytest.approx(50.0)
    assert s.progress_pct == 50.0
    assert s.cycles == 3
    assert s.acc_volume == pytest.approx(12.5) and s.acc_unit == "mL"
    assert s.consumed_volume == pytest.approx(8.0)
    assert s.remaining_volume == pytest.approx(4.5)
    assert s.elapsed_ms == 120000 and s.remaining_ms == 60000
    assert s.alarm is False


def test_read_status_rejects_nonfinite_and_out_of_protocol_values():
    inp, hold = _adapter_inputs()
    inp[(1000, 1)] = s16(101)
    inp[(1002, 2)] = f32(float("inf"))
    inp[(1008, 2)] = u32(100)
    inp[(1032, 11)] = (
        f32(float("nan"))
        + u16(2)
        + f32(-1.0)
        + u16(2)
        + f32(float("inf"))
        + u16(2)
        + f32(30.0)
    )
    inp[(1043, 4)] = u32(200) + u32(0)
    hold[(4088, 2)] = f32(41.0)
    hold[(4090, 2)] = f32(201.0)
    hold[(4128, 6)] = (
        f32(float("nan"))
        + f32(-1.0)
        + u16(0)
        + u16(0)
    )

    status = TYD02Adapter(
        FakeClient(inp, hold), slave=1, wordorder="CDAB"
    ).read_status()

    assert status.temp_c is None
    assert status.flow_rpm is None
    assert status.progress_pct is None
    assert status.acc_volume is None
    assert status.consumed_volume is None
    assert status.remaining_volume is None
    assert status.metrics["target_volume"] is None
    assert status.metrics["inject_rate"] is None
    assert status.metrics["syringe_inner_diameter_mm"] is None
    assert status.metrics["syringe_capacity"] is None
    assert status.metrics["step_length_ul_per_step"] is None


def test_derive_state_alarm_wins():
    assert derive_state(1, 1, 1, 1) == "alarm"
    assert derive_state(0, 1, 0, 0) == "paused"
    assert derive_state(1, 0, 0, 0) == "running"
    assert derive_state(0, 0, 0, 0) == "stopped"


def f32cdab(v):
    b = struct.pack(">f", v)
    return bytes([b[2], b[3], b[0], b[1]])


def test_read_status_includes_rich_metrics():
    # 仅注入 (mode 0) reads mode structure @4128 (6 regs); rich config from holding regs.
    inp, hold = _adapter_inputs()
    hold[(4021, 1)] = u16(9)           # syringe code = BD Glass 100ml
    hold[(4027, 1)] = u16(100)         # force
    hold[(4087, 1)] = u16(1)           # stall alarm enabled
    hold[(4090, 2)] = f32cdab(100.0)   # syringe capacity
    hold[(4092, 1)] = u16(2)           # syringe unit = mL
    hold[(4093, 1)] = u16(0)           # pause h
    hold[(4094, 1)] = u16(0)           # pause m
    hold[(4095, 1)] = u16(5)           # pause s
    hold[(4096, 1)] = u16(0)           # pause ms
    hold[(4097, 1)] = u16(3)           # repeat count
    hold[(4128, 6)] = f32cdab(50.0) + f32cdab(5.0) + u16(2) + u16(2)  # 50mL @ 5.0 mL/min
    s = TYD02Adapter(FakeClient(inp, hold), slave=1, wordorder="CDAB").read_status()
    assert s.metrics["syringe_code"] == 9
    assert s.metrics["syringe_name"] == "BD Glass 100ml"
    assert s.metrics["syringe_capacity"] == 100.0
    assert s.metrics["target_volume"] == 50.0
    assert s.metrics["inject_rate"] == 5.0
    assert s.metrics["pause_delay_ms"] == 5000
    assert s.metrics["repeat_count"] == 3
    assert s.metrics["force"] == 100
    assert s.metrics["stall_alarm_enabled"] is True
    assert s.metrics["step_length_ul_per_step"] == pytest.approx(50.0)
    assert s.metrics["step_length_mm_per_step"] == pytest.approx(0.0893, abs=0.0001)
