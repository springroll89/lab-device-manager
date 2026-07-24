from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import time
from datetime import date
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

from lab_device_manager.config import load_config
from lab_device_manager.db.repository import Repository


def _number(value) -> str:
    number = float(value)
    return str(int(number)) if number.is_integer() else f"{number:g}"


def build_inventory_markdown(summary: dict, today_text: str) -> str:
    sections = [
        (
            "🔴 低库存",
            summary.get("low_stock_items") or [],
            lambda item: (
                f"- {item['material_name']}："
                f"{_number(item['quantity_remaining'])} {item['unit']}"
                f"（下限 {_number(item['min_threshold'])}）"
            ),
        ),
        (
            "🟡 库存超量",
            summary.get("over_stock_items") or [],
            lambda item: (
                f"- {item['material_name']}："
                f"{_number(item['quantity_remaining'])} {item['unit']}"
                f"（上限 {_number(item['max_threshold'])}）"
            ),
        ),
        (
            "🟠 30 天内临期",
            summary.get("expiring_items") or [],
            lambda item: (
                f"- {item['material_name']}（有效期 {item['expires_on']}）"
            ),
        ),
        (
            "🔴 已过期",
            summary.get("expired_items") or [],
            lambda item: (
                f"- {item['material_name']}（有效期 {item['expires_on']}）"
            ),
        ),
    ]
    lines = [f"## 📦 Puricore 库存日报 — {today_text}", ""]
    alert_count = 0
    for label, items, formatter in sections:
        alert_count += len(items)
        lines.extend([f"#### {label}：{len(items)} 项", ""])
        lines.extend(formatter(item) for item in items)
        lines.append("")
    if not alert_count:
        lines.append("> ✅ 一切正常，无需关注。")
    return "\n".join(lines).strip() + "\n"


def sign_dingtalk_url(
    webhook_url: str,
    secret: str,
    timestamp_ms: int | None = None,
) -> str:
    timestamp = int(timestamp_ms or time.time() * 1000)
    digest = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}\n{secret}".encode("utf-8"),
        hashlib.sha256,
    ).digest()
    signature = base64.b64encode(digest).decode("ascii")
    parsed = urlparse(webhook_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update({"timestamp": str(timestamp), "sign": signature})
    return urlunparse(parsed._replace(query=urlencode(query)))


def send_dingtalk_report(
    webhook_url: str,
    markdown: str,
    *,
    secret: str = "",
    app_url: str = "",
    today_text: str,
    timeout_seconds: float = 10,
) -> dict:
    parsed = urlparse(webhook_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("钉钉 Webhook 必须是 HTTPS 地址")
    target = (
        sign_dingtalk_url(webhook_url, secret)
        if secret
        else webhook_url
    )
    action_card = {
        "title": f"Puricore 库存日报 — {today_text}",
        "text": markdown,
        "btnOrientation": "0",
    }
    if app_url:
        action_card.update(
            {"singleTitle": "查看库存详情", "singleURL": app_url}
        )
    request = Request(
        target,
        data=json.dumps(
            {"msgtype": "actionCard", "actionCard": action_card},
            ensure_ascii=False,
        ).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
        if int(payload.get("errcode", 0)) != 0:
            raise RuntimeError(
                f"钉钉发送失败：{payload.get('errmsg') or '未知错误'}"
            )
        return payload


def _has_alerts(summary: dict) -> bool:
    return any(
        summary.get(key)
        for key in (
            "low_stock_items",
            "over_stock_items",
            "expiring_items",
            "expired_items",
        )
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="生成或发送库存日报")
    parser.add_argument("--config", help="配置文件路径")
    parser.add_argument(
        "--send",
        action="store_true",
        help="发送到钉钉；省略时仅在终端预览",
    )
    args = parser.parse_args(argv)
    config = load_config(args.config)
    repo = Repository(config.db_path)
    try:
        today_text = date.today().isoformat()
        summary = repo.inventory.get_summary(today_text)
        markdown = build_inventory_markdown(summary, today_text)
    finally:
        repo.close()
    if not args.send:
        print(markdown)
        return 0
    if not _has_alerts(summary):
        print("今日没有库存告警，未发送钉钉消息。")
        return 0
    webhook = os.environ.get(
        "LAB_DINGTALK_WEBHOOK_URL",
        os.environ.get("DINGTALK_WEBHOOK_URL", ""),
    )
    if not webhook:
        parser.error(
            "发送前请设置 LAB_DINGTALK_WEBHOOK_URL 环境变量"
        )
    secret = os.environ.get(
        "LAB_DINGTALK_SECRET",
        os.environ.get("DINGTALK_SECRET", ""),
    )
    app_url = os.environ.get(
        "LAB_INVENTORY_APP_URL",
        f"{config.public_base_url}/inventory"
        if config.public_base_url
        else "",
    )
    send_dingtalk_report(
        webhook,
        markdown,
        secret=secret,
        app_url=app_url,
        today_text=today_text,
    )
    print("库存日报已发送。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
