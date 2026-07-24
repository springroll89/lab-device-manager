import io
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from lab_device_manager.inventory import alerts
from lab_device_manager.inventory.alerts import (
    build_inventory_markdown,
    send_dingtalk_report,
    sign_dingtalk_url,
)


def test_inventory_markdown_contains_actionable_alert_details():
    summary = {
        "low_stock_items": [
            {
                "material_name": "无水乙醇",
                "quantity_remaining": 2,
                "unit": "L",
                "min_threshold": 3,
            }
        ],
        "over_stock_items": [
            {
                "material_name": "标签纸",
                "quantity_remaining": 20,
                "unit": "卷",
                "max_threshold": 10,
            }
        ],
        "expiring_items": [
            {"material_name": "盐酸", "expires_on": "2026-08-01"}
        ],
        "expired_items": [
            {"material_name": "旧试剂", "expires_on": "2026-06-01"}
        ],
    }

    report = build_inventory_markdown(summary, "2026-07-25")

    assert "Puricore 库存日报 — 2026-07-25" in report
    assert "无水乙醇：2 L（下限 3）" in report
    assert "标签纸：20 卷（上限 10）" in report
    assert "盐酸（有效期 2026-08-01）" in report
    assert "旧试剂（有效期 2026-06-01）" in report
    assert "一切正常" not in report


def test_inventory_markdown_reports_when_there_are_no_alerts():
    summary = {
        "low_stock_items": [],
        "over_stock_items": [],
        "expiring_items": [],
        "expired_items": [],
    }

    report = build_inventory_markdown(summary, "2026-07-25")

    assert "一切正常，无需关注" in report


def test_dingtalk_signed_url_preserves_webhook_and_adds_signature():
    url = sign_dingtalk_url(
        "https://oapi.dingtalk.com/robot/send?access_token=abc",
        "test-secret",
        1_722_470_400_000,
    )
    parsed = urlparse(url)
    params = parse_qs(parsed.query)

    assert parsed.netloc == "oapi.dingtalk.com"
    assert params["access_token"] == ["abc"]
    assert params["timestamp"] == ["1722470400000"]
    assert params["sign"][0]


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def test_dingtalk_sender_validates_url_and_posts_action_card(monkeypatch):
    with pytest.raises(ValueError, match="HTTPS"):
        send_dingtalk_report(
            "http://example.test/hook",
            "report",
            today_text="2026-07-25",
        )

    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data)
        captured["timeout"] = timeout
        return _Response(b'{"errcode":0,"errmsg":"ok"}')

    monkeypatch.setattr(alerts, "urlopen", fake_urlopen)
    result = send_dingtalk_report(
        "https://oapi.dingtalk.com/robot/send?access_token=abc",
        "report",
        secret="secret",
        app_url="https://lab.example.test/inventory",
        today_text="2026-07-25",
        timeout_seconds=3,
    )

    assert result["errcode"] == 0
    assert "sign=" in captured["url"]
    assert captured["timeout"] == 3
    assert captured["body"]["actionCard"]["singleTitle"] == "查看库存详情"

    monkeypatch.setattr(
        alerts,
        "urlopen",
        lambda *_args, **_kwargs: _Response(
            b'{"errcode":310000,"errmsg":"invalid sign"}'
        ),
    )
    with pytest.raises(RuntimeError, match="invalid sign"):
        send_dingtalk_report(
            "https://oapi.dingtalk.com/robot/send?access_token=abc",
            "report",
            today_text="2026-07-25",
        )


def test_inventory_alert_cli_previews_and_only_sends_when_requested(
    monkeypatch,
    capsys,
):
    summary = {
        "low_stock_items": [],
        "over_stock_items": [],
        "expiring_items": [],
        "expired_items": [],
    }

    class FakeRepository:
        def __init__(self, db_path):
            assert db_path == "inventory.db"
            self.inventory = SimpleNamespace(
                get_summary=lambda _today: summary
            )

        def close(self):
            pass

    monkeypatch.setattr(
        alerts,
        "load_config",
        lambda _path: SimpleNamespace(
            db_path="inventory.db",
            public_base_url="https://lab.example.test",
        ),
    )
    monkeypatch.setattr(alerts, "Repository", FakeRepository)

    assert alerts.main([]) == 0
    assert "一切正常" in capsys.readouterr().out
    assert alerts.main(["--send"]) == 0
    assert "未发送" in capsys.readouterr().out

    summary["low_stock_items"] = [
        {
            "material_name": "乙醇",
            "quantity_remaining": 1,
            "unit": "L",
            "min_threshold": 2,
        }
    ]
    sent = {}
    monkeypatch.setenv(
        "LAB_DINGTALK_WEBHOOK_URL", "https://example.test/hook"
    )
    monkeypatch.setenv("LAB_DINGTALK_SECRET", "secret")
    monkeypatch.setattr(
        alerts,
        "send_dingtalk_report",
        lambda *args, **kwargs: sent.update(
            {"args": args, "kwargs": kwargs}
        ),
    )

    assert alerts.main(["--send"]) == 0
    assert sent["kwargs"]["app_url"].endswith("/inventory")
    assert "已发送" in capsys.readouterr().out
