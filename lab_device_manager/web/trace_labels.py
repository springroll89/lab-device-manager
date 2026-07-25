from __future__ import annotations

from datetime import datetime
from html import escape
from io import BytesIO
from urllib.parse import quote

import qrcode
from qrcode.constants import ERROR_CORRECT_M


STATUS_LABELS = {
    "active": "可用",
    "stored": "暂存中",
    "consumed": "已消耗",
    "completed": "已完成",
    "disposed": "已报废",
    "available": "可用",
    "empty": "已用完",
    "quarantined": "隔离待检查",
    "expired": "已过期",
}

TYPE_LABELS = {
    "batch": "实验批次",
    "intermediate": "中间品",
    "final_product": "最终成品",
}


def trace_qr_payload(item: dict, experiment: dict) -> str:
    experimenter = (
        experiment.get("operator") or item.get("created_by") or "—"
    )
    return "\n".join(
        [
            "PURICORE实验实物",
            f"类型：{TYPE_LABELS.get(item['item_type'], item['item_type'])}",
            f"名称：{item['display_name']}",
            f"编号：{item['item_code']}",
            f"批次：{experiment['batch_id']}",
            f"状态：{STATUS_LABELS.get(item['status'], item['status'])}",
            f"生成：{_time_text(item.get('created_at_ms'))}",
            f"实验人：{experimenter}",
        ]
    )


def storage_location_qr_payload(location: dict) -> str:
    return "\n".join(
        [
            "PURICORE存储位置",
            "类型：存储位置",
            f"名称：{location['display_name']}",
            f"编号：{location['location_code']}",
            f"保存条件：{location.get('storage_condition') or '按实验要求'}",
        ]
    )


def material_container_qr_payload(container: dict) -> str:
    quantity = "未录入"
    if container.get("quantity_remaining") is not None:
        quantity = (
            f"{container['quantity_remaining']} "
            f"{container.get('unit') or ''}"
        ).strip()
    return "\n".join(
        [
            "PURICORE原材料",
            "类型：原材料",
            f"名称：{container['material_name']}",
            f"编号：{container['container_code']}",
            f"供应商批号：{container.get('supplier_lot') or '—'}",
            f"有效期：{container.get('expires_on') or '—'}",
            f"登记余量：{quantity}",
            f"实验人：{container.get('created_by') or '—'}",
        ]
    )


def qr_png(payload: str) -> bytes:
    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_M,
        box_size=14,
        border=4,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    image = qr.make_image(
        fill_color="black", back_color="white"
    ).convert("RGB")
    if image.size[0] < 600:
        image = image.resize((600, 600))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _time_text(value) -> str:
    if value in (None, ""):
        return "—"
    return datetime.fromtimestamp(int(value) / 1000).strftime(
        "%Y-%m-%d %H:%M"
    )


def _page_shell(title: str, labels: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{escape(title)}</title>
  <style>
    *{{box-sizing:border-box}}
    body{{margin:0;background:#eef1f1;color:#071117;
      font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}}
    .toolbar{{position:sticky;top:0;z-index:2;display:flex;align-items:center;
      justify-content:space-between;gap:16px;padding:14px 20px;background:#071117;color:#f5fbfa}}
    .toolbar strong{{font-size:15px}}
    .toolbar button{{min-height:42px;padding:9px 18px;border:0;border-radius:9px;
      background:#91dfc3;color:#071117;font:inherit;font-weight:700;cursor:pointer}}
    .sheet{{display:grid;gap:12mm;justify-content:center;padding:14mm}}
    .label{{width:60mm;height:48mm;background:#fff;border:0.35mm solid #0b171d;
      display:grid;grid-template-columns:minmax(0,1fr) 23mm;gap:2.5mm;padding:3mm;
      overflow:hidden;page-break-after:always}}
    .label:last-child{{page-break-after:auto}}
    .brand{{font-size:6.5pt;font-weight:800;letter-spacing:.18em}}
    .kind{{margin-top:1.6mm;font-size:7pt;color:#476066}}
    h1{{margin:1mm 0;font-size:12pt;line-height:1.15}}
    .code{{font:800 8.5pt ui-monospace,SFMono-Regular,Menlo,monospace;
      overflow-wrap:anywhere}}
    .meta{{margin-top:1.5mm;display:grid;gap:.7mm;font-size:6.5pt;line-height:1.2}}
    .meta b{{font-weight:700}}
    .qr{{display:flex;min-width:0;min-height:0;flex-direction:column;
      align-items:center;justify-content:flex-start;padding-top:1mm}}
    .qr img{{display:block;width:24mm;height:24mm;flex:0 0 24mm}}
    .qr small{{display:block;width:100%;min-height:8mm;margin-top:1.2mm;
      font-size:5.5pt;line-height:1.25;text-align:center;overflow-wrap:anywhere;
      word-break:break-all}}
    @page{{size:60mm 48mm;margin:0}}
    @media print{{
      body{{background:#fff}}
      .toolbar{{display:none}}
      .sheet{{display:block;padding:0}}
      .label{{border:0}}
    }}
  </style>
</head>
<body>
  <div class="toolbar">
    <strong>{escape(title)}</strong>
    <button type="button" onclick="window.print()">打印标签</button>
  </div>
  <main class="sheet">{labels}</main>
</body>
</html>"""


def trace_labels_html(
    items: list[dict], experiment: dict, copies: int = 1
) -> str:
    labels = []
    for item in items:
        quantity = ""
        if item.get("quantity") is not None:
            quantity = (
                f"<div><b>数量</b> {escape(str(item['quantity']))} "
                f"{escape(str(item.get('unit') or ''))}</div>"
            )
        location = ""
        if item.get("storage_location_code"):
            location = (
                f"<div><b>位置</b> "
                f"{escape(item['storage_location_code'])}</div>"
            )
        hold = ""
        if item.get("hold_until_ms"):
            hold = (
                f"<div><b>最晚使用</b> "
                f"{escape(_time_text(item['hold_until_ms']))}</div>"
            )
        label = f"""
        <article class="label">
          <div>
            <div class="brand">PURICORE</div>
            <div class="kind">{escape(TYPE_LABELS.get(item['item_type'], item['item_type']))}</div>
            <h1>{escape(item['display_name'])}</h1>
            <div class="code">{escape(item['item_code'])}</div>
            <div class="meta">
              <div><b>批次</b> {escape(experiment['batch_id'])}</div>
              <div><b>状态</b> {escape(STATUS_LABELS.get(item['status'], item['status']))}</div>
              {quantity}{location}{hold}
              <div><b>生成</b> {escape(_time_text(item['created_at_ms']))}</div>
              <div><b>实验人</b> {escape(experiment.get('operator') or item.get('created_by') or '—')}</div>
            </div>
          </div>
          <div class="qr">
            <img src="/api/trace/qr.png?code={quote(item['item_code'])}" alt="追溯二维码">
            <small>{escape(item['item_code'])}</small>
          </div>
        </article>"""
        labels.extend([label] * copies)
    return _page_shell(
        f"{experiment['batch_id']} · 实物标签",
        "".join(labels),
    )


def _storage_location_label(location: dict) -> str:
    return f"""
    <article class="label">
      <div>
        <div class="brand">PURICORE</div>
        <div class="kind">存储位置</div>
        <h1>{escape(location['display_name'])}</h1>
        <div class="code">{escape(location['location_code'])}</div>
        <div class="meta">
          <div><b>保存条件</b> {escape(location.get('storage_condition') or '按实验要求')}</div>
          <div>存入时依次扫描实物标签与本位置标签</div>
        </div>
      </div>
      <div class="qr">
        <img src="/api/trace/qr.png?code={quote(location['location_code'])}" alt="位置二维码">
        <small>{escape(location['location_code'])}</small>
      </div>
    </article>"""


def storage_location_labels_html(
    locations: list[dict], copies: int = 1
) -> str:
    labels = "".join(
        _storage_location_label(location) * copies
        for location in locations
    )
    return _page_shell("PURICORE · 存储位置标签", labels)


def storage_location_label_html(
    location: dict, copies: int = 1
) -> str:
    return _page_shell(
        f"{location['display_name']} · 位置标签",
        _storage_location_label(location) * copies,
    )


def _material_container_label(container: dict) -> str:
    quantity = "未录入"
    if container.get("quantity_remaining") is not None:
        quantity = (
            f"{container['quantity_remaining']} "
            f"{container.get('unit') or ''}"
        ).strip()
    return f"""
    <article class="label">
      <div>
        <div class="brand">PURICORE</div>
        <div class="kind">原材料标签</div>
        <h1>{escape(container['material_name'])}</h1>
        <div class="code">{escape(container['container_code'])}</div>
        <div class="meta">
          <div><b>供应商批号</b> {escape(container.get('supplier_lot') or '—')}</div>
          <div><b>有效期</b> {escape(container.get('expires_on') or '—')}</div>
          <div><b>登记余量</b> {escape(quantity)}</div>
          <div><b>状态</b> {escape(STATUS_LABELS.get(container['status'], container['status']))}</div>
          <div><b>实验人</b> {escape(container.get('created_by') or '—')}</div>
        </div>
      </div>
      <div class="qr">
        <img src="/api/trace/qr.png?code={quote(container['container_code'])}" alt="原材料二维码">
        <small>{escape(container['container_code'])}</small>
      </div>
    </article>"""


def material_container_labels_html(
    containers: list[dict], copies: int = 1
) -> str:
    labels = "".join(
        _material_container_label(container) * copies
        for container in containers
    )
    return _page_shell("PURICORE · 原材料标签", labels)


def material_container_label_html(
    container: dict, copies: int = 1
) -> str:
    return _page_shell(
        f"{container['container_code']} · 原材料标签",
        _material_container_label(container) * copies,
    )
