from __future__ import annotations

from datetime import datetime
from html import escape
from urllib.parse import quote

from reportlab.graphics import renderSVG
from reportlab.graphics.barcode import createBarcodeDrawing


STATUS_LABELS = {
    "active": "可用",
    "stored": "暂存中",
    "consumed": "已消耗",
    "completed": "已完成",
    "disposed": "已报废",
}

TYPE_LABELS = {
    "batch": "实验批次",
    "intermediate": "中间品",
    "final_product": "最终成品",
}


def qr_svg(code: str) -> str:
    drawing = createBarcodeDrawing(
        "QR",
        value=f"PURICORE:{code}",
        width=120,
        height=120,
        barLevel="M",
    )
    return renderSVG.drawToString(drawing)


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
    .qr img{{display:block;width:22mm;height:22mm;flex:0 0 22mm}}
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
              <div><b>生成</b> {escape(_time_text(item['created_at_ms']))} · {escape(item['created_by'])}</div>
            </div>
          </div>
          <div class="qr">
            <img src="/api/trace/qr.svg?code={quote(item['item_code'])}" alt="追溯二维码">
            <small>{escape(item['item_code'])}</small>
          </div>
        </article>"""
        labels.extend([label] * copies)
    return _page_shell(
        f"{experiment['batch_id']} · 实物标签",
        "".join(labels),
    )


def storage_location_label_html(
    location: dict, copies: int = 1
) -> str:
    label = f"""
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
        <img src="/api/trace/qr.svg?code={quote(location['location_code'])}" alt="位置二维码">
        <small>{escape(location['location_code'])}</small>
      </div>
    </article>"""
    return _page_shell(
        f"{location['display_name']} · 位置标签",
        label * copies,
    )
