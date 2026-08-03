from __future__ import annotations

import time


COMMUNICATION_LABELS = {
    "communicating": "通讯正常",
    "data_interrupted": "数据中断",
    "gateway_offline": "网关离线",
    "instrument_unresponsive": "仪器无响应",
    "communication_error": "通讯异常",
}
COMMUNICATION_DESCRIPTIONS = {
    "communicating": "已收到仪器有效数据",
    "data_interrupted": "超过 15 秒没有收到新数据",
    "gateway_offline": "无法连接串口服务器或采集通道",
    "instrument_unresponsive": "采集通道已连接，但没有收到仪器有效数据",
    "communication_error": "收到的数据无法通过协议校验",
}


def communication_health(snapshot, now_ms: int | None = None) -> dict:
    """Return connectivity health independently from instrument operation."""
    if snapshot is None:
        code = "gateway_offline"
        detail = "尚未建立采集连接"
        technical_detail = ""
        updated_at_ms = None
    else:
        updated_at_ms = int(snapshot.timestamp * 1000)
        metrics = snapshot.metrics or {}
        technical_detail = str(metrics.get("communication_error", ""))
        if snapshot.state == "offline":
            code = str(
                metrics.get("communication_status", "communication_error")
            )
        else:
            current_ms = now_ms if now_ms is not None else int(time.time() * 1000)
            code = (
                "data_interrupted"
                if current_ms - updated_at_ms > 15_000
                else "communicating"
            )
    if code not in COMMUNICATION_LABELS:
        code = "communication_error"
    if snapshot is not None:
        detail = COMMUNICATION_DESCRIPTIONS[code]
    return {
        "code": code,
        "label": COMMUNICATION_LABELS[code],
        "detail": detail,
        "technical_detail": technical_detail,
        "updated_at_ms": updated_at_ms,
    }
