from io import BytesIO

from PIL import Image
import zxingcpp

from lab_device_manager.web.trace_labels import qr_png, trace_qr_payload


def test_trace_qr_payload_is_offline_text_with_experimenter():
    payload = trace_qr_payload(
        {
            "item_type": "final_product",
            "display_name": "CEM 功能膜成品",
            "item_code": "20260724-CEM-01-FP-01",
            "status": "completed",
            "created_at_ms": 1784822400000,
            "created_by": "备用账号",
        },
        {
            "batch_id": "20260724-CEM-01",
            "operator": "实验员01",
        },
    )

    assert payload.startswith("PURICORE实验实物\n")
    assert "类型：最终成品" in payload
    assert "名称：CEM 功能膜成品" in payload
    assert "编号：20260724-CEM-01-FP-01" in payload
    assert "批次：20260724-CEM-01" in payload
    assert "状态：已完成" in payload
    assert "实验人：实验员01" in payload
    assert "http" not in payload


def test_qr_png_round_trips_utf8_offline_text():
    payload = "\n".join(
        [
            "PURICORE实验实物",
            "类型：最终成品",
            "名称：CEM 功能膜成品",
            "编号：20260724-CEM-01-FP-01",
            "实验人：实验员01",
        ]
    )

    decoded = zxingcpp.read_barcodes(
        Image.open(BytesIO(qr_png(payload)))
    )

    assert len(decoded) == 1
    assert decoded[0].text == payload
