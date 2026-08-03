from __future__ import annotations
import csv
import io
import json as _json
import time
from datetime import datetime
from io import BytesIO

import sqlite3
from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    redirect,
    request,
    send_from_directory,
    stream_with_context,
    url_for,
)
from openpyxl import Workbook
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.graphics.shapes import Circle, Drawing, Line, PolyLine, Rect, String
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from lab_device_manager.config import _load_or_create_secret
from lab_device_manager.db.models import fmt_ts_ms
from lab_device_manager.experiments.service import (
    PROCESS_DEVICE_BINDINGS,
    R201Error,
    R201Service,
    STEP_LABELS,
)
from lab_device_manager.inventory import InventoryError, InventoryService
from lab_device_manager.inventory.pubchem import lookup_chemical
from lab_device_manager.inventory.regulatory_catalog import (
    lookup_hazardous_catalog,
)
from lab_device_manager.runtime.communication import communication_health
from lab_device_manager.web.auth import AuthManager
from lab_device_manager.web.barcode import (
    BarcodeImageError,
    MAX_BARCODE_IMAGE_BYTES,
    decode_barcode_image,
)
from lab_device_manager.web.trace_labels import (
    material_container_label_html,
    material_container_labels_html,
    material_container_qr_payload,
    qr_png,
    storage_location_qr_payload,
    storage_location_label_html,
    storage_location_labels_html,
    trace_qr_payload,
    trace_labels_html,
)
from lab_device_manager.web.whd46 import create_whd46_blueprint

_MAX_RUN_LIMIT = 1000


def _label_request_args() -> tuple[list[int], int]:
    try:
        requested_ids = [
            int(value)
            for value in request.args.get("ids", "").split(",")
            if value.strip()
        ]
        copies = int(request.args.get("copies", "1"))
    except ValueError as exc:
        raise R201Error("ids and copies must be integers") from exc
    if not requested_ids:
        raise R201Error("ids must contain at least one item")
    if len(requested_ids) > 100:
        raise R201Error("no more than 100 labels can be printed at once")
    if any(item_id <= 0 for item_id in requested_ids):
        raise R201Error("ids must be positive integers")
    if not 1 <= copies <= 20:
        raise R201Error("copies must be between 1 and 20")
    return list(dict.fromkeys(requested_ids)), copies


def _snap_to_dict(snap):
    return {
        "state": snap.state, "work_mode": snap.work_mode, "flow_rpm": snap.flow_rpm,
        "acc_volume": snap.acc_volume, "acc_unit": snap.acc_unit,
        "consumed_volume": snap.consumed_volume, "consumed_unit": snap.consumed_unit,
        "remaining_volume": snap.remaining_volume, "remaining_unit": snap.remaining_unit,
        "temp_c": snap.temp_c,
        "progress_pct": snap.progress_pct, "remaining_ms": snap.remaining_ms,
        "alarm": snap.alarm, "ts_ms": int(snap.timestamp * 1000),
        "metrics": dict(snap.metrics) if snap.metrics else {},
    }


def _run_to_dict(run):
    d = {
        "id": run.id, "device_id": run.device_id,
        "started_ms": run.started_ms, "ended_ms": run.ended_ms,
        "duration_ms": run.duration_ms, "end_status": run.end_status,
        "operator": run.operator,
        "operator_user_id": run.operator_user_id,
        "project_tag": run.project_tag,
        "tagged": run.tagged, "alarm_count": run.alarm_count,
        "result_acc_volume": run.result_acc_volume, "result_acc_unit": run.result_acc_unit,
        "started_display": fmt_ts_ms(run.started_ms) if run.started_ms else "",
    }
    d["actual_volume"] = run.actual_volume
    d["actual_unit"] = run.actual_unit
    # parse setpoints_json for the API consumer
    try:
        d["setpoints"] = _json.loads(run.setpoints_json) if run.setpoints_json else {}
    except Exception:
        d["setpoints"] = {}
    return d


def _sample_to_dict(s):
    d = {"id": s.id, "run_id": s.run_id, "device_id": s.device_id,
         "ts_ms": s.ts_ms, "state": s.state, "flow_rate": s.flow_rate,
         "delivered_volume": s.delivered_volume, "temp_c": s.temp_c}
    try:
        d["metrics"] = _json.loads(s.metrics_json) if s.metrics_json else {}
    except Exception:
        d["metrics"] = {}
    return d


def _event_to_dict(e):
    d = {"id": e.id, "device_id": e.device_id, "run_id": e.run_id,
         "ts_ms": e.ts_ms, "event_type": e.event_type, "severity": e.severity}
    try:
        d["detail"] = _json.loads(e.detail_json) if e.detail_json else {}
    except Exception:
        d["detail"] = {}
    return d


def _csv_safe(v):
    """Neutralize CSV formula injection (OWASP): if a string cell starts with a
    formula character, prefix a single quote so spreadsheet apps treat it as
    text."""
    if isinstance(v, str) and v and v[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


def _xlsx_safe(v):
    """Same idea as _csv_safe: prevent formula injection in Excel cells."""
    if isinstance(v, str) and v and v[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


def _build_pdf_report(run, samples, events):
    """Build a single-run PDF report with metadata, flow curve, and event timeline."""
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4)
    styles = getSampleStyleSheet()
    story = []
    story.append(Paragraph(f"运行报告 #{run.id}", styles["Title"]))
    story.append(Spacer(1, 12))

    meta = [
        ["设备", str(run.device_id)],
        ["开始", fmt_ts_ms(run.started_ms) if run.started_ms else "-"],
        ["结束", fmt_ts_ms(run.ended_ms) if run.ended_ms else "-"],
        ["状态", run.end_status or "-"],
        ["操作人", run.operator or "-"],
        ["项目", run.project_tag or "-"],
        ["本次液量", f"{run.actual_volume} {run.actual_unit}" if run.actual_volume is not None else "-"],
    ]
    t = Table(meta, colWidths=[120, 360])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
    ]))
    story.append(t)
    story.append(Spacer(1, 18))

    if samples:
        story.append(Paragraph("流速曲线", styles["Heading2"]))
        # Render a simple table of flow-rate values instead of a chart image.
        flow_rows = [["时间", "流速"]]
        for s in samples:
            flow_rows.append([fmt_ts_ms(s.ts_ms), s.flow_rate if s.flow_rate is not None else "-"])
        flow_table = Table(flow_rows, colWidths=[240, 240])
        flow_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ]))
        story.append(flow_table)
        story.append(Spacer(1, 18))

    story.append(Paragraph("事件时间线", styles["Heading2"]))
    ev_rows = [["时间", "事件", "级别"]]
    for e in events:
        ev_rows.append([fmt_ts_ms(e.ts_ms), e.event_type, e.severity])
    ev_table = Table(ev_rows, colWidths=[160, 160, 160])
    ev_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
    ]))
    story.append(ev_table)

    doc.build(story)
    buf.seek(0)
    return buf.getvalue()


def _trend_drawing(
    points: list[tuple[int, float]],
    unit: str,
    target_min=None,
    target_max=None,
) -> Drawing:
    width, height = 510, 155
    left, right, top, bottom = 48, 12, 18, 28
    drawing = Drawing(width, height)
    if not points:
        drawing.add(
            String(
                width / 2,
                height / 2,
                "暂无数据",
                fontName="STSong-Light",
                fontSize=9,
                textAnchor="middle",
                fillColor=colors.HexColor("#677783"),
            )
        )
        return drawing
    x_values = [float(point[0]) for point in points]
    y_values = [float(point[1]) for point in points]
    configured = [
        float(value)
        for value in (target_min, target_max)
        if value is not None
    ]
    all_y = y_values + configured
    min_x, max_x = min(x_values), max(x_values)
    min_y, max_y = min(all_y), max(all_y)
    if min_y == max_y:
        min_y -= 1
        max_y += 1
    margin_y = (max_y - min_y) * 0.12
    min_y -= margin_y
    max_y += margin_y
    chart_width = width - left - right
    chart_height = height - top - bottom

    def x_pos(value):
        return left + (
            (value - min_x) / max(1.0, max_x - min_x)
        ) * chart_width

    def y_pos(value):
        return bottom + (
            (value - min_y) / max(0.001, max_y - min_y)
        ) * chart_height

    if len(configured) == 2:
        low, high = sorted(configured)
        drawing.add(
            Rect(
                left,
                y_pos(low),
                chart_width,
                max(1, y_pos(high) - y_pos(low)),
                strokeColor=None,
                fillColor=colors.HexColor("#E4F5EF"),
            )
        )
    drawing.add(
        Line(
            left,
            bottom,
            width - right,
            bottom,
            strokeColor=colors.HexColor("#7C8992"),
        )
    )
    drawing.add(
        Line(
            left,
            bottom,
            left,
            height - top,
            strokeColor=colors.HexColor("#7C8992"),
        )
    )
    coordinates = [
        (x_pos(x_value), y_pos(y_value))
        for x_value, y_value in zip(x_values, y_values)
    ]
    drawing.add(
        PolyLine(
            coordinates,
            strokeColor=colors.HexColor("#13795B"),
            strokeWidth=1.8,
        )
    )
    for x_value, y_value in coordinates:
        drawing.add(
            Circle(
                x_value,
                y_value,
                2.4,
                strokeColor=colors.HexColor("#13795B"),
                fillColor=colors.white,
            )
        )
    drawing.add(
        String(
            4,
            height - top,
            f"{max_y:.1f} {unit}",
            fontName="STSong-Light",
            fontSize=7,
            fillColor=colors.HexColor("#58666F"),
        )
    )
    drawing.add(
        String(
            4,
            bottom - 2,
            f"{min_y:.1f} {unit}",
            fontName="STSong-Light",
            fontSize=7,
            fillColor=colors.HexColor("#58666F"),
        )
    )
    drawing.add(
        String(
            left,
            8,
            fmt_ts_ms(int(min_x)),
            fontName="STSong-Light",
            fontSize=6,
            fillColor=colors.HexColor("#58666F"),
        )
    )
    drawing.add(
        String(
            width - right,
            8,
            fmt_ts_ms(int(max_x)),
            fontName="STSong-Light",
            fontSize=6,
            textAnchor="end",
            fillColor=colors.HexColor("#58666F"),
        )
    )
    return drawing


def _build_experiment_pdf(detail: dict) -> bytes:
    """Build the first R-201 electronic batch record for parallel validation."""
    buf = BytesIO()
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=32,
        rightMargin=32,
        topMargin=32,
        bottomMargin=32,
    )
    styles = getSampleStyleSheet()
    for name in ("Title", "Heading1", "Heading2", "BodyText"):
        styles[name].fontName = "STSong-Light"
    experiment = detail["experiment"]
    story = [
        Paragraph("R-201 湿化学合成电子批记录", styles["Title"]),
        Paragraph(
            f"Batch ID：{experiment['batch_id']}　"
            f"体系：{experiment['membrane_system']}　"
            f"状态：{experiment['status']}",
            styles["BodyText"],
        ),
        Spacer(1, 12),
    ]
    meta = [
        ["配方", f"{experiment['recipe_no']} / {experiment['recipe_version']}"],
        ["SOP", f"{experiment['sop_code']} / {experiment['sop_version']}"],
        [
            "目标粘度",
            f"{experiment['target_viscosity_min_mpas']}–"
            f"{experiment['target_viscosity_max_mpas']} mPa.s",
        ],
        ["操作员 / 复核员", f"{experiment['operator']} / {experiment['reviewer']}"],
        ["验证模式", experiment["validation_mode"]],
        ["参数快照 SHA-256", experiment.get("snapshot_sha256") or "—"],
        ["下一步", experiment["next_step"]],
        ["下游路线", experiment["downstream_route_variant"]],
        ["判定", experiment.get("disposition") or "—"],
    ]
    table = Table(meta, colWidths=[110, 400])
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "STSong-Light"),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#E8EEF4")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#9AA7B2")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("PADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.extend([table, Spacer(1, 14), Paragraph("配方参数", styles["Heading2"])])
    parameter_rows = [["代码", "名称", "目标", "实际", "单位", "来源"]]
    for item in detail["recipe_parameters"]:
        parameter_rows.append(
            [
                item["parameter_code"],
                item["display_name"],
                item["target_value"] if item["target_value"] is not None else "—",
                item["actual_value"] if item["actual_value"] is not None else "—",
                item["unit"],
                item["source"],
            ]
        )
    parameter_table = Table(
        parameter_rows,
        colWidths=[85, 110, 55, 55, 45, 160],
        repeatRows=1,
    )
    parameter_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "STSong-Light"),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DCE6EF")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#AAB4BD")),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("PADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.extend([parameter_table, Spacer(1, 14), Paragraph("物料使用", styles["Heading2"])])
    material_rows = [
        ["物料", "容器编号", "批号", "有效期", "理论量", "实际量", "外观"]
    ]
    for item in detail["materials"]:
        material_rows.append(
            [
                item["material_name"],
                item.get("container_code") or "—",
                item["lot_no"],
                item["expires_at"] or "—",
                (
                    f"{item['theoretical_value']} {item['unit']}"
                    if item["theoretical_value"] is not None
                    else "—"
                ),
                f"{item['actual_value']} {item['unit']}",
                item["appearance"] or "—",
            ]
        )
    material_table = Table(
        material_rows,
        colWidths=[68, 82, 70, 66, 68, 68, 88],
        repeatRows=1,
    )
    material_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "STSong-Light"),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DCE6EF")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#AAB4BD")),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("PADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.extend([material_table, Spacer(1, 14), Paragraph("步骤执行记录", styles["Heading2"])])
    step_rows = [["步骤", "状态", "开始", "结束", "结果摘要"]]
    for step in detail["steps"]:
        result = step.get("result") or {}
        summary = "；".join(f"{key}={value}" for key, value in list(result.items())[:4])
        step_rows.append(
            [
                f"{step['step_code']} {STEP_LABELS.get(step['step_code'], '')}",
                step["status"],
                fmt_ts_ms(step["started_effective_at_ms"]),
                fmt_ts_ms(step["ended_effective_at_ms"]) if step["ended_effective_at_ms"] else "—",
                summary or "—",
            ]
        )
    step_table = Table(step_rows, colWidths=[125, 55, 92, 92, 150], repeatRows=1)
    step_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "STSong-Light"),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DCE6EF")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#AAB4BD")),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("PADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.extend([step_table, Spacer(1, 14), Paragraph("反应温度趋势", styles["Heading2"])])
    temperature_points = [
        (item["ts_ms"], item["value"])
        for item in detail["temperature_series"]
    ]
    story.extend(
        [
            _trend_drawing(
                temperature_points,
                "℃",
                experiment["spec_snapshot"].get("reaction_temp_min_c"),
                experiment["spec_snapshot"].get("reaction_temp_max_c"),
            ),
            Paragraph(
                "数据完整性："
                f"{detail['telemetry_integrity_status']}；"
                f"样本 {len(temperature_points)} 条；"
                f"20 分钟检查点 {len(detail['temperature_checkpoints'])} 个；"
                f"缺口 {len(detail['telemetry_gaps'])} 段。",
                styles["BodyText"],
            ),
            Spacer(1, 14),
        ]
    )
    viscosity_rows = [["时间", "粘度 mPa.s", "扭矩 %", "样品温度 ℃", "转子/转速", "有效"]]
    for item in detail["measurements"]:
        if item["measurement_type"] != "viscosity":
            continue
        values = item["values"]
        viscosity_rows.append(
            [
                fmt_ts_ms(item["effective_at_ms"]),
                values.get("viscosity_mpas", "—"),
                values.get("torque_pct", "—"),
                values.get("sample_temp_c", "—"),
                f"{values.get('rotor', '—')} / {values.get('rpm', '—')}",
                "是" if item["valid"] else "否",
            ]
        )
    viscosity_points = [
        (
            item["effective_at_ms"],
            item["values"]["viscosity_mpas"],
        )
        for item in detail["measurements"]
        if item["measurement_type"] == "viscosity"
        and item["values"].get("viscosity_mpas") is not None
    ]
    viscosity_table = Table(viscosity_rows, colWidths=[105, 75, 55, 75, 85, 45], repeatRows=1)
    viscosity_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "STSong-Light"),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DCE6EF")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#AAB4BD")),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("PADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.extend(
        [
            KeepTogether(
                [
                    Paragraph("粘度监测", styles["Heading2"]),
                    _trend_drawing(
                        viscosity_points,
                        "mPa.s",
                        experiment["target_viscosity_min_mpas"],
                        experiment["target_viscosity_max_mpas"],
                    ),
                    viscosity_table,
                ]
            ),
            Spacer(1, 14),
            Paragraph("设备数据源", styles["Heading2"]),
        ]
    )
    source_rows = [["角色", "设备 ID", "指标/通道", "关联时间", "方式"]]
    for item in detail["data_sources"]:
        source_rows.append(
            [
                item["device_role"],
                item["device_id"],
                f"{item['metric_key']} / {item['channel_selector'] or '—'}",
                fmt_ts_ms(item["linked_at_ms"]),
                item["link_method"],
            ]
        )
    source_table = Table(
        source_rows,
        colWidths=[90, 55, 135, 145, 75],
        repeatRows=1,
    )
    source_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "STSong-Light"),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DCE6EF")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#AAB4BD")),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("PADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.extend(
        [
            source_table,
            Spacer(1, 14),
            Paragraph("偏差", styles["Heading2"]),
        ]
    )
    deviation_rows = [["编号", "级别", "状态", "描述"]]
    for item in detail["deviations"]:
        deviation_rows.append(
            [item["deviation_no"], item["severity"], item["status"], item["description"]]
        )
    deviation_table = Table(deviation_rows, colWidths=[125, 55, 65, 265], repeatRows=1)
    deviation_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "STSong-Light"),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DCE6EF")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#AAB4BD")),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("PADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.extend(
        [
            deviation_table,
            Spacer(1, 14),
            Paragraph("审计时间轴", styles["Heading2"]),
        ]
    )
    audit_rows = [["有效时间", "接收时间", "事件", "操作者", "时钟状态"]]
    for item in detail["events"]:
        audit_rows.append(
            [
                fmt_ts_ms(item["effective_at_ms"]),
                fmt_ts_ms(item["received_at_server_ms"]),
                item["event_type"],
                item["actor"],
                item["clock_sync_status"],
            ]
        )
    audit_table = Table(
        audit_rows,
        colWidths=[110, 110, 120, 85, 75],
        repeatRows=1,
    )
    audit_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "STSong-Light"),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DCE6EF")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#AAB4BD")),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("PADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.extend(
        [
            audit_table,
            Spacer(1, 12),
            Paragraph(
                "本记录处于 parallel_validation 模式，正式放行仍以受控纸质签名为准。",
                styles["BodyText"],
            ),
        ]
    )

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont("STSong-Light", 7)
        canvas.setFillColor(colors.HexColor("#6C7880"))
        canvas.drawString(32, 18, experiment["batch_id"])
        canvas.drawRightString(
            A4[0] - 32,
            18,
            f"第 {document.page} 页 | parallel_validation",
        )
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    buf.seek(0)
    return buf.getvalue()


def create_app(
    engine,
    repo,
    secret_key: str = "",
    public_base_url: str = "",
):
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    app.extensions["puricore_engine"] = engine
    app.config["MAX_CONTENT_LENGTH"] = 3 * 1024 * 1024
    r201 = R201Service(repo)
    inventory = InventoryService(repo.inventory)
    app.secret_key = secret_key if secret_key else _load_or_create_secret()
    auth = AuthManager(app, repo)
    app.config["PUBLIC_BASE_URL"] = str(public_base_url or "").rstrip("/")
    app.register_blueprint(create_whd46_blueprint(engine, repo))
    login_required = auth.login_required
    _current_operator = auth.current_operator

    def _current_user_id():
        user = auth.current_user() or {}
        user_id = user.get("id")
        return user_id if isinstance(user_id, int) and user_id > 0 else None

    def _stamp_actor(body: dict, field: str = "actor") -> dict:
        body[field] = _current_operator()
        body[f"{field}_user_id"] = _current_user_id()
        return body

    def _trace_base_url() -> str:
        return app.config["PUBLIC_BASE_URL"] or request.url_root.rstrip("/")

    def _device_capture(experiment_id: int):
        captured_at_ms = int(time.time() * 1000)
        latest = engine.latest()
        detail = r201.get_experiment(experiment_id)
        role_candidates = {}
        for binding in detail["data_sources"]:
            if binding["unlinked_at_ms"] is not None:
                continue
            role_candidates.setdefault(
                binding["device_role"],
                set(),
            ).add(binding["device_id"])
        role_device_ids = {
            role: next(iter(device_ids))
            for role, device_ids in role_candidates.items()
            if len(device_ids) == 1
        }
        measurement_reservations = [
            item
            for item in repo.experiments.list_device_reservations(
                experiment_id
            )
            if item["purpose"] == "measurement"
            and item["device_type"] == "viscometer"
        ]
        if len(measurement_reservations) == 1:
            role_device_ids["viscometer"] = measurement_reservations[0][
                "device_id"
            ]
        devices = []
        for device_id, config in engine.device_map().items():
            snapshot = latest.get(device_id)
            snapshot_data = _snap_to_dict(snapshot) if snapshot else None
            recent_samples = repo.list_samples_for_device(
                device_id, limit=1
            )
            sampled_at_ms = (
                snapshot_data.get("ts_ms") if snapshot_data else None
            )
            devices.append(
                {
                    "device_id": device_id,
                    "name": config.name,
                    "alias": config.alias,
                    "type": config.type,
                    "sample_id": (
                        recent_samples[0].id
                        if recent_samples
                        else None
                    ),
                    "age_ms": (
                        max(0, captured_at_ms - sampled_at_ms)
                        if sampled_at_ms is not None
                        else None
                    ),
                    "snapshot": snapshot_data,
                }
            )
        return {
            "captured_at_server_ms": captured_at_ms,
            "role_device_ids": role_device_ids,
            "devices": devices,
        }

    def _ensure_automatic_sources(experiment_id: int):
        detail = r201.get_experiment(experiment_id)
        experiment = detail["experiment"]
        active_by_role = {}
        for item in detail["data_sources"]:
            if item["unlinked_at_ms"] is None:
                active_by_role.setdefault(
                    item["device_role"],
                    set(),
                ).add(item["device_id"])
        device_map = engine.device_map()
        latest = engine.latest()
        now_ms = int(time.time() * 1000)
        configured_by_type = {}
        fresh_by_type = {}
        for device_id, config in device_map.items():
            configured_by_type.setdefault(config.type, []).append(device_id)
            snapshot = latest.get(device_id)
            if (
                snapshot is not None
                and snapshot.state != "offline"
                and now_ms - int(snapshot.timestamp * 1000) <= 15_000
            ):
                fresh_by_type.setdefault(config.type, []).append(device_id)

        for device_type, role_metrics in PROCESS_DEVICE_BINDINGS.items():
            if device_type == "viscometer":
                continue
            exclusive_steps = {
                "stirrer": {
                    "R201-10",
                    "R201-20",
                    "R201-30",
                    "R201-31",
                    "R201-32",
                    "R201-40",
                    "R201-50",
                    "R201-60",
                },
                "tyd02": {"R201-30", "R201-31"},
            }
            if (
                device_type in exclusive_steps
                and experiment["current_step_code"]
                not in exclusive_steps[device_type]
            ):
                continue
            roles = tuple(role_metrics)
            bound_ids = {
                device_id
                for role in roles
                for device_id in active_by_role.get(role, set())
            }
            if len(bound_ids) == 1:
                selected_device_id = next(iter(bound_ids))
            elif bound_ids:
                continue
            else:
                fresh_candidates = fresh_by_type.get(device_type, [])
                configured_candidates = configured_by_type.get(
                    device_type,
                    [],
                )
                if len(fresh_candidates) == 1:
                    selected_device_id = fresh_candidates[0]
                elif len(configured_candidates) == 1:
                    selected_device_id = configured_candidates[0]
                else:
                    continue
            if device_type in ("stirrer", "tyd02"):
                try:
                    repo.experiments.reserve_process_device(
                        experiment_id,
                        selected_device_id,
                        device_type,
                        experiment["operator"],
                        experiment.get("operator_user_id"),
                        now_ms,
                    )
                except RuntimeError:
                    continue
            for role, metric_key in role_metrics.items():
                if active_by_role.get(role):
                    continue
                r201.add_data_source(
                    experiment_id,
                    {
                        "client_event_id": (
                            f"auto-bind-{experiment_id}-"
                            f"{selected_device_id}-{role}-{metric_key}"
                        ),
                        "actor": experiment["operator"],
                        "actor_user_id": experiment.get(
                            "operator_user_id"
                        ),
                        "device_id": selected_device_id,
                        "device_role": role,
                        "metric_key": metric_key,
                        "linked_at_ms": experiment["created_at_ms"],
                        "link_method": "automatic",
                        "confidence": 1.0,
                        "source_type": "derived",
                    },
                )
                active_by_role.setdefault(role, set()).add(
                    selected_device_id
                )

    @app.get("/")
    @login_required
    def index():
        return send_from_directory(app.static_folder, "index.html")

    @app.get("/experiments")
    @app.get("/experiments/<int:experiment_id>")
    @login_required
    def experiments_page(experiment_id=None):
        return send_from_directory(app.static_folder, "experiment.html")

    @app.get("/measurement-station")
    @login_required
    def measurement_station_page():
        return send_from_directory(
            app.static_folder, "measurement-station.html"
        )

    @app.get("/materials")
    @app.get("/inventory")
    @login_required
    def materials_page():
        return send_from_directory(app.static_folder, "materials.html")

    @app.get("/hazardous-waste")
    @login_required
    def hazardous_waste_page():
        return send_from_directory(
            app.static_folder, "hazardous-waste.html"
        )

    def _r201_error(exc: Exception):
        if isinstance(exc, R201Error):
            payload = {"error": str(exc)}
            if exc.details:
                payload["details"] = exc.details
            return jsonify(payload), exc.status_code
        if isinstance(exc, sqlite3.IntegrityError):
            message = str(exc)
            if "experiment.batch_id" in message:
                return jsonify({"error": "batch_id already exists"}), 409
            return jsonify({"error": "database constraint failed"}), 409
        raise exc

    def _inventory_error(exc: InventoryError):
        return jsonify({"error": str(exc)}), exc.status_code

    def _device_surface(data_sources: list[dict]) -> dict:
        latest = engine.latest()
        device_map = engine.device_map()
        reservations = {
            item["device_id"]: item
            for item in repo.experiments.list_device_reservations()
        }
        devices = []
        for device_id, config in device_map.items():
            snapshot = latest.get(device_id)
            devices.append(
                {
                    "id": device_id,
                    "name": config.name,
                    "alias": config.alias,
                    "type": config.type,
                    "latest": _snap_to_dict(snapshot) if snapshot else None,
                    "communication": communication_health(snapshot),
                    "reservation": reservations.get(device_id),
                }
            )
        device_by_id = {item["id"]: item for item in devices}

        def process_device(role):
            bindings = [
                item
                for item in data_sources
                if item["device_role"] == role
                and item["unlinked_at_ms"] is None
            ]
            if not bindings:
                return {"bound": False, "ambiguous": False}
            device_ids = {item["device_id"] for item in bindings}
            if len(device_ids) != 1:
                return {
                    "bound": False,
                    "ambiguous": True,
                    "candidate_device_ids": sorted(device_ids),
                    "bindings": bindings,
                }
            selected_id = next(iter(device_ids))
            binding = next(
                item
                for item in reversed(bindings)
                if item["device_id"] == selected_id
            )
            device = device_by_id.get(binding["device_id"])
            latest_snapshot = device.get("latest") if device else None
            return {
                "bound": True,
                "ambiguous": False,
                "binding": binding,
                "device": (
                    {
                        "id": device["id"],
                        "name": device["name"],
                        "alias": device["alias"],
                        "type": device["type"],
                    }
                    if device
                    else None
                ),
                "latest": latest_snapshot,
            }

        return {
            "available_devices": devices,
            "process_status": {
                "acid_pump": process_device("acid_pump"),
                "reaction_temp": process_device("reaction_temp"),
                "stirrer": process_device("stirrer"),
                "viscometer": process_device("viscometer"),
                "environment": process_device("environment"),
            },
        }

    def _experiment_detail(experiment_id: int):
        _ensure_automatic_sources(experiment_id)
        detail = r201.get_experiment(experiment_id)
        detail.update(_device_surface(detail["data_sources"]))
        return detail

    @app.get("/api/experiments")
    def api_experiments():
        return jsonify(r201.list_experiments())

    @app.get("/api/workbench")
    def api_workbench():
        user = auth.current_user() or {}
        reservations = repo.experiments.list_device_reservations()
        by_experiment = {}
        for reservation in reservations:
            by_experiment.setdefault(
                reservation["experiment_id"], []
            ).append(reservation)
        experiments = [
            {
                **item,
                "device_reservations": by_experiment.get(item["id"], []),
            }
            for item in r201.list_experiments()
            if item["status"] in ("draft", "in_progress", "pending_review")
            and (
                user.get("role") in ("super_admin", "supervisor")
                or item.get("operator_user_id") == user.get("id")
            )
        ]
        return jsonify(
            {
                "experiments": experiments,
                "active_count": len(
                    [
                        item
                        for item in experiments
                        if item["status"] in ("draft", "in_progress")
                    ]
                ),
                "reservations": reservations,
            }
        )

    @app.get("/api/experiments/next-batch-id")
    def api_next_batch_id():
        try:
            batch_id = r201.suggest_batch_id(
                request.args.get("membrane_system", ""),
                request.args.get("date", ""),
            )
            return jsonify({"batch_id": batch_id})
        except R201Error as exc:
            return _r201_error(exc)

    @app.post("/api/experiments")
    def api_create_experiment():
        try:
            body = dict(request.get_json(silent=True) or {})
            body["operator"] = _current_operator()
            body["operator_user_id"] = _current_user_id()
            body.setdefault("reviewer", "")
            if body["reviewer"] and _current_user_id() is not None:
                matches = repo.accounts.find_active_users_by_display_name(
                    body["reviewer"]
                )
                if (
                    len(matches) != 1
                    or matches[0]["role"]
                    not in ("super_admin", "supervisor")
                ):
                    raise R201Error(
                        "复核员必须对应唯一且有效的主管账号"
                    )
                body["reviewer_user_id"] = matches[0]["id"]
            created = r201.create_experiment(body)
            return jsonify(created), 201
        except (R201Error, sqlite3.IntegrityError) as exc:
            return _r201_error(exc)

    @app.delete("/api/experiments/<int:experiment_id>")
    @auth.roles_required("super_admin")
    @auth.csrf_required
    def api_delete_experiment(experiment_id):
        try:
            return jsonify(r201.delete_experiment(experiment_id))
        except R201Error as exc:
            return _r201_error(exc)

    @app.get("/api/experiments/<int:experiment_id>")
    def api_experiment_detail(experiment_id):
        try:
            return jsonify(_experiment_detail(experiment_id))
        except R201Error as exc:
            return _r201_error(exc)

    @app.get("/api/experiments/<int:experiment_id>/revision")
    def api_experiment_revision(experiment_id):
        try:
            return jsonify(r201.experiment_revision(experiment_id))
        except R201Error as exc:
            return _r201_error(exc)

    @app.get("/api/experiments/<int:experiment_id>/traceability")
    def api_experiment_traceability(experiment_id):
        try:
            return jsonify(r201.get_traceability(experiment_id))
        except R201Error as exc:
            return _r201_error(exc)

    @app.post("/api/experiments/<int:experiment_id>/trace-items")
    def api_create_trace_items(experiment_id):
        body = dict(request.get_json(silent=True) or {})
        _stamp_actor(body)
        try:
            return jsonify(
                r201.create_trace_items(experiment_id, body)
            ), 201
        except R201Error as exc:
            return _r201_error(exc)

    @app.post("/api/trace-items/<int:trace_item_id>/store")
    def api_store_trace_item(trace_item_id):
        body = dict(request.get_json(silent=True) or {})
        _stamp_actor(body)
        try:
            return jsonify(
                r201.transition_trace_item(
                    trace_item_id, "store", body
                )
            )
        except R201Error as exc:
            return _r201_error(exc)

    @app.post("/api/trace-items/<int:trace_item_id>/retrieve")
    def api_retrieve_trace_item(trace_item_id):
        body = dict(request.get_json(silent=True) or {})
        _stamp_actor(body)
        try:
            return jsonify(
                r201.transition_trace_item(
                    trace_item_id, "retrieve", body
                )
            )
        except R201Error as exc:
            return _r201_error(exc)

    @app.post("/api/experiments/<int:experiment_id>/trace-labels")
    def api_request_trace_labels(experiment_id):
        body = dict(request.get_json(silent=True) or {})
        _stamp_actor(body)
        try:
            jobs = r201.request_trace_labels(experiment_id, body)
            item_ids = ",".join(
                str(item_id) for item_id in body.get("item_ids") or []
            )
            copies = int(body.get("copies", 1))
            return jsonify(
                {
                    "jobs": jobs,
                    "print_url": url_for(
                        "api_print_trace_labels",
                        experiment_id=experiment_id,
                        item_ids=item_ids,
                        copies=copies,
                    ),
                }
            ), 201
        except (R201Error, TypeError, ValueError) as exc:
            if isinstance(exc, R201Error):
                return _r201_error(exc)
            return _r201_error(R201Error(str(exc)))

    @app.get(
        "/api/experiments/<int:experiment_id>/trace-labels/print"
    )
    def api_print_trace_labels(experiment_id):
        try:
            requested_ids = [
                int(value)
                for value in request.args.get("item_ids", "").split(",")
                if value
            ]
            copies = int(request.args.get("copies", "1"))
            if not requested_ids:
                raise R201Error("item_ids must contain at least one item")
            if not 1 <= copies <= 20:
                raise R201Error("copies must be between 1 and 20")
            traceability = r201.get_traceability(experiment_id)
            by_id = {item["id"]: item for item in traceability["items"]}
            items = []
            for item_id in requested_ids:
                if item_id not in by_id:
                    raise R201Error(
                        "trace item does not belong to experiment", 404
                    )
                items.append(by_id[item_id])
            experiment = r201.get_experiment(experiment_id)["experiment"]
            return Response(
                trace_labels_html(items, experiment, copies),
                mimetype="text/html",
            )
        except (R201Error, TypeError, ValueError) as exc:
            if isinstance(exc, R201Error):
                return _r201_error(exc)
            return _r201_error(R201Error(str(exc)))

    @app.get("/api/storage-locations")
    def api_storage_locations():
        return jsonify(inventory.list_storage_locations())

    @app.get("/api/storage-locations/labels")
    def api_print_storage_location_labels():
        try:
            requested_ids, copies = _label_request_args()
            locations = []
            for location_id in requested_ids:
                location = repo.experiments.get_storage_location(location_id)
                if location is None:
                    raise R201Error("storage location not found", 404)
                locations.append(location)
            return Response(
                storage_location_labels_html(locations, copies),
                mimetype="text/html",
            )
        except R201Error as exc:
            return _r201_error(exc)

    @app.post("/api/storage-locations")
    @auth.roles_required("super_admin", "supervisor")
    @auth.csrf_required
    def api_create_storage_location():
        body = dict(request.get_json(silent=True) or {})
        _stamp_actor(body)
        try:
            return jsonify(inventory.save_storage_location(body)), 201
        except InventoryError as exc:
            return _inventory_error(exc)

    @app.patch("/api/storage-locations/<int:location_id>")
    @auth.roles_required("super_admin", "supervisor")
    @auth.csrf_required
    def api_update_storage_location(location_id):
        body = dict(request.get_json(silent=True) or {})
        _stamp_actor(body)
        try:
            return jsonify(
                inventory.save_storage_location(
                    body, location_id=location_id
                )
            )
        except InventoryError as exc:
            return _inventory_error(exc)

    @app.post("/api/storage-locations/<int:location_id>/print")
    def api_request_location_label(location_id):
        body = dict(request.get_json(silent=True) or {})
        _stamp_actor(body)
        try:
            job = r201.request_location_label(location_id, body)
            return jsonify(
                {
                    "job": job,
                    "print_url": url_for(
                        "api_print_storage_location_label",
                        location_id=location_id,
                        copies=body.get("copies", 1),
                    ),
                }
            ), 201
        except R201Error as exc:
            return _r201_error(exc)

    @app.get("/api/storage-locations/<int:location_id>/label")
    def api_print_storage_location_label(location_id):
        location = repo.experiments.get_storage_location(location_id)
        if location is None:
            return _r201_error(
                R201Error("storage location not found", 404)
            )
        try:
            copies = int(request.args.get("copies", "1"))
        except ValueError:
            return _r201_error(R201Error("copies must be an integer"))
        if not 1 <= copies <= 20:
            return _r201_error(
                R201Error("copies must be between 1 and 20")
            )
        return Response(
            storage_location_label_html(location, copies),
            mimetype="text/html",
        )

    @app.get("/api/trace/lookup")
    def api_trace_lookup():
        try:
            return jsonify(
                r201.lookup_trace_code(request.args.get("code", ""))
            )
        except R201Error as exc:
            return _r201_error(exc)

    @app.get("/api/scan/resolve")
    def api_scan_resolve():
        try:
            return jsonify(
                r201.resolve_scan_code(request.args.get("code", ""))
            )
        except R201Error as exc:
            return _r201_error(exc)

    @app.post("/api/scan/decode")
    def api_scan_decode():
        if request.content_length and request.content_length > (
            MAX_BARCODE_IMAGE_BYTES + 64 * 1024
        ):
            return jsonify({"error": "image exceeds 2 MB"}), 413
        upload = request.files.get("image")
        if upload is None:
            return jsonify({"error": "image is required"}), 400
        try:
            decoded = decode_barcode_image(
                upload.stream.read(MAX_BARCODE_IMAGE_BYTES + 1)
            )
        except BarcodeImageError as exc:
            return jsonify({"error": str(exc)}), 400
        if decoded is None:
            return jsonify({"error": "no barcode found"}), 422
        return jsonify(decoded)

    @app.get("/scan/<path:code>")
    def scan_entry(code):
        try:
            found = r201.resolve_scan_code(code)
        except R201Error as exc:
            return _r201_error(exc)
        if found["kind"] == "trace_item":
            item = found["item"]
            return redirect(
                url_for(
                    "experiments_page",
                    experiment_id=item["experiment_id"],
                    scan=code,
                )
            )
        if found["kind"] == "material_container":
            return redirect(url_for("materials_page", scan=code))
        return redirect(url_for("experiments_page", scan=code))

    @app.get("/api/inventory/summary")
    def api_inventory_summary():
        try:
            return jsonify(
                inventory.get_summary(request.args.get("today"))
            )
        except InventoryError as exc:
            return _inventory_error(exc)

    @app.get("/api/inventory/items")
    def api_inventory_items():
        try:
            return jsonify(inventory.list_items(request.args))
        except InventoryError as exc:
            return _inventory_error(exc)

    @app.post("/api/inventory/items")
    @auth.roles_required("super_admin", "supervisor")
    @auth.csrf_required
    def api_create_inventory_item():
        body = dict(request.get_json(silent=True) or {})
        _stamp_actor(body, "created_by")
        body.setdefault(
            "client_event_id",
            f"inventory-register-{time.time_ns()}",
        )
        try:
            return jsonify(inventory.create_item(body)), 201
        except InventoryError as exc:
            return _inventory_error(exc)

    @app.get("/api/inventory/items/<int:item_id>")
    def api_inventory_item_detail(item_id):
        try:
            return jsonify(inventory.get_item(item_id))
        except InventoryError as exc:
            return _inventory_error(exc)

    @app.patch("/api/inventory/items/<int:item_id>")
    @auth.roles_required("super_admin", "supervisor")
    @auth.csrf_required
    def api_update_inventory_item(item_id):
        body = dict(request.get_json(silent=True) or {})
        _stamp_actor(body)
        try:
            return jsonify(
                inventory.update_item(item_id, body)
            )
        except InventoryError as exc:
            return _inventory_error(exc)

    @app.get("/api/inventory/pubchem")
    def api_inventory_pubchem():
        try:
            return jsonify(lookup_chemical(request.args.get("cas", "")))
        except ValueError as exc:
            return _inventory_error(InventoryError(str(exc), 404))

    @app.get("/api/inventory/regulatory-lookup")
    def api_inventory_regulatory_lookup():
        try:
            return jsonify(
                lookup_hazardous_catalog(request.args.get("cas", ""))
            )
        except ValueError as exc:
            return _inventory_error(InventoryError(str(exc)))

    @app.post("/api/inventory/items/<int:item_id>/movements")
    @auth.csrf_required
    def api_inventory_movement(item_id):
        body = dict(request.get_json(silent=True) or {})
        _stamp_actor(body)
        body.setdefault(
            "client_event_id",
            f"inventory-movement-{item_id}-{time.time_ns()}",
        )
        try:
            action = str(body.get("action") or "")
            user = auth.current_user() or {}
            if (
                action in {
                    "received",
                    "adjusted",
                    "quarantined",
                    "disposed",
                }
                and user.get("role") not in {
                    "super_admin",
                    "supervisor",
                }
            ):
                return jsonify({"error": "forbidden"}), 403
            return jsonify(inventory.record_movement(item_id, body))
        except InventoryError as exc:
            return _inventory_error(exc)

    @app.post(
        "/api/inventory/items/<int:item_id>/movement-approvals"
    )
    @auth.csrf_required
    def api_request_inventory_movement_approval(item_id):
        body = dict(request.get_json(silent=True) or {})
        _stamp_actor(body)
        body.setdefault(
            "client_event_id",
            f"inventory-approval-{item_id}-{time.time_ns()}",
        )
        try:
            approval = inventory.request_movement_approval(item_id, body)
            return jsonify({"approval": approval}), 202
        except InventoryError as exc:
            return _inventory_error(exc)

    @app.get("/api/inventory/movement-approvals")
    @auth.roles_required("super_admin", "supervisor")
    def api_inventory_movement_approvals():
        try:
            return jsonify(
                {
                    "approvals": inventory.list_movement_approvals(
                        request.args.get("status") or "pending"
                    )
                }
            )
        except InventoryError as exc:
            return _inventory_error(exc)

    @app.post(
        "/api/inventory/movement-approvals/<int:approval_id>/decision"
    )
    @auth.roles_required("super_admin", "supervisor")
    @auth.csrf_required
    def api_decide_inventory_movement_approval(approval_id):
        body = dict(request.get_json(silent=True) or {})
        _stamp_actor(body)
        try:
            return jsonify(
                inventory.decide_movement_approval(approval_id, body)
            )
        except InventoryError as exc:
            return _inventory_error(exc)

    @app.get("/api/inventory/movements")
    def api_inventory_movements():
        try:
            return jsonify(inventory.list_movements(request.args))
        except (InventoryError, TypeError, ValueError) as exc:
            if isinstance(exc, InventoryError):
                return _inventory_error(exc)
            return _inventory_error(InventoryError("分页参数无效"))

    @app.get("/api/inventory/hazardous-waste")
    def api_hazardous_waste_list():
        try:
            return jsonify(
                {
                    "waste": inventory.list_hazardous_waste(
                        request.args.get("status") or ""
                    )
                }
            )
        except InventoryError as exc:
            return _inventory_error(exc)

    @app.post("/api/inventory/hazardous-waste")
    @auth.roles_required("super_admin", "supervisor")
    @auth.csrf_required
    def api_create_hazardous_waste():
        body = dict(request.get_json(silent=True) or {})
        _stamp_actor(body)
        body.setdefault(
            "client_event_id", f"hazardous-waste-{time.time_ns()}"
        )
        try:
            return jsonify(inventory.create_hazardous_waste(body)), 201
        except InventoryError as exc:
            return _inventory_error(exc)

    @app.get("/api/inventory/hazardous-waste/<int:waste_id>")
    def api_hazardous_waste_detail(waste_id):
        try:
            return jsonify(inventory.get_hazardous_waste(waste_id))
        except InventoryError as exc:
            return _inventory_error(exc)

    @app.post(
        "/api/inventory/hazardous-waste/<int:waste_id>/events"
    )
    @auth.csrf_required
    def api_hazardous_waste_event(waste_id):
        body = dict(request.get_json(silent=True) or {})
        _stamp_actor(body)
        body.setdefault(
            "client_event_id",
            f"hazardous-waste-event-{waste_id}-{time.time_ns()}",
        )
        action = str(body.get("action") or "")
        user = auth.current_user() or {}
        if (
            action in {"seal", "complete_transfer"}
            and user.get("role") not in {"super_admin", "supervisor"}
        ):
            return jsonify({"error": "forbidden"}), 403
        try:
            return jsonify(
                inventory.update_hazardous_waste_state(
                    waste_id, action, body
                )
            )
        except InventoryError as exc:
            return _inventory_error(exc)

    @app.post(
        "/api/inventory/hazardous-waste/<int:waste_id>/transfers"
    )
    @auth.roles_required("super_admin", "supervisor")
    @auth.csrf_required
    def api_hazardous_waste_transfer(waste_id):
        body = dict(request.get_json(silent=True) or {})
        _stamp_actor(body)
        body.setdefault(
            "client_event_id",
            f"hazardous-waste-transfer-{waste_id}-{time.time_ns()}",
        )
        try:
            return jsonify(
                inventory.register_hazardous_waste_transfer(waste_id, body)
            ), 201
        except InventoryError as exc:
            return _inventory_error(exc)

    @app.get("/api/inventory/hazardous-waste.csv")
    def api_hazardous_waste_csv():
        try:
            waste_rows = inventory.list_hazardous_waste(
                request.args.get("status") or ""
            )
        except InventoryError as exc:
            return _inventory_error(exc)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "危废编号",
                "危废名称",
                "国家危废类别/代码",
                "物理形态",
                "危险特性",
                "主要成分",
                "数量",
                "单位",
                "包装容器",
                "暂存库位",
                "状态",
                "开始收集时间",
                "封口时间",
                "转移完成时间",
                "登记人",
                "国家电子联单编号",
                "承运单位及资质",
                "接收处置单位及许可证",
                "利用/处置方式",
            ]
        )
        for waste in waste_rows:
            detail = inventory.get_hazardous_waste(waste["id"])
            transfer = (
                detail["transfers"][0] if detail["transfers"] else {}
            )
            writer.writerow(
                [
                    _csv_safe(waste["waste_code"]),
                    _csv_safe(waste["waste_name"]),
                    _csv_safe(
                        " / ".join(
                            part
                            for part in (
                                waste["waste_category_code"],
                                waste.get("waste_category_name"),
                            )
                            if part
                        )
                    ),
                    waste["physical_state"],
                    _csv_safe(
                        " / ".join(waste["hazard_characteristics"])
                    ),
                    _csv_safe(waste["composition"]),
                    waste["quantity"],
                    _csv_safe(waste["unit"]),
                    _csv_safe(waste["package_type"]),
                    _csv_safe(
                        f"{waste['location_name']} "
                        f"({waste['location_code']})"
                    ),
                    waste["status"],
                    fmt_ts_ms(waste["started_at_ms"]),
                    (
                        fmt_ts_ms(waste["sealed_at_ms"])
                        if waste.get("sealed_at_ms")
                        else ""
                    ),
                    (
                        fmt_ts_ms(waste["transferred_at_ms"])
                        if waste.get("transferred_at_ms")
                        else ""
                    ),
                    _csv_safe(waste["created_by"]),
                    _csv_safe(
                        transfer.get("national_manifest_no") or ""
                    ),
                    _csv_safe(
                        " / ".join(
                            part
                            for part in (
                                transfer.get("transporter_name"),
                                transfer.get("transporter_license_no"),
                            )
                            if part
                        )
                    ),
                    _csv_safe(
                        " / ".join(
                            part
                            for part in (
                                transfer.get("recipient_name"),
                                transfer.get("recipient_permit_no"),
                            )
                            if part
                        )
                    ),
                    _csv_safe(transfer.get("disposal_method") or ""),
                ]
            )
        return Response(
            "\ufeff" + buffer.getvalue(),
            mimetype="text/csv",
            headers={
                "Content-Disposition": (
                    "attachment; filename=puricore-hazardous-waste.csv"
                )
            },
        )

    @app.get("/api/inventory/movements.csv")
    def api_inventory_movements_csv():
        filters = dict(request.args)
        filters["limit"] = 200
        filters["offset"] = 0
        try:
            first_page = inventory.list_movements(filters)
            movements = list(first_page["movements"])
            while len(movements) < first_page["total"]:
                filters["offset"] = len(movements)
                page = inventory.list_movements(filters)
                if not page["movements"]:
                    break
                movements.extend(page["movements"])
        except (InventoryError, TypeError, ValueError) as exc:
            if isinstance(exc, InventoryError):
                return _inventory_error(exc)
            return _inventory_error(InventoryError("流水筛选参数无效"))
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "时间",
                "操作类型",
                "系统编号",
                "物品名称",
                "数量变化",
                "结余",
                "单位",
                "操作人",
                "第二确认人",
                "确认时间",
                "实验批次",
                "备注",
            ]
        )
        for movement in movements:
            writer.writerow(
                [
                    datetime.fromtimestamp(
                        movement["effective_at_ms"] / 1000
                    ).strftime("%Y-%m-%d %H:%M:%S"),
                    movement["action"],
                    _csv_safe(movement["container_code"]),
                    _csv_safe(movement["material_name"]),
                    movement["delta"],
                    movement["quantity_after"],
                    _csv_safe(movement.get("unit") or ""),
                    _csv_safe(movement["actor"]),
                    _csv_safe(movement.get("approved_by") or ""),
                    (
                        fmt_ts_ms(movement["approved_at_ms"])
                        if movement.get("approved_at_ms")
                        else ""
                    ),
                    _csv_safe(movement.get("batch_id") or ""),
                    _csv_safe(movement.get("note") or ""),
                ]
            )
        return Response(
            "\ufeff" + buffer.getvalue(),
            mimetype="text/csv",
            headers={
                "Content-Disposition": (
                    "attachment; filename=puricore-inventory-audit.csv"
                )
            },
        )

    @app.get("/api/inventory/export.csv")
    def api_inventory_export_csv():
        try:
            items = inventory.list_items(request.args)
        except InventoryError as exc:
            return _inventory_error(exc)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "系统编号",
                "名称",
                "类别",
                "当前库存",
                "单位",
                "位置",
                "负责人",
                "供应商批号",
                "有效期",
                "状态",
                "是否管制",
                "危化判定",
                "特殊管制",
                "CAS号",
                "危险性",
                "GHS图示",
                "储存组",
                "来源单位",
                "交付/领用凭证编号",
                "交付凭证引用",
                "接收时间",
                "接收人",
                "验收人",
                "来源方许可/备案编号",
                "来源方许可/备案凭证",
                "目录判定来源",
                "目录版本",
                "目录序号",
                "SDS链接",
                "SDS版本",
                "SDS核验人",
                "双人控制",
                "双人控制依据",
            ]
        )
        for item in items:
            writer.writerow(
                [
                    _csv_safe(item["container_code"]),
                    _csv_safe(item["material_name"]),
                    item["category"],
                    item["quantity_remaining"],
                    _csv_safe(item["unit"]),
                    _csv_safe(item.get("location") or ""),
                    _csv_safe(item.get("owner") or ""),
                    _csv_safe(item.get("supplier_lot") or ""),
                    item.get("expires_on") or "",
                    item["status"],
                    "是" if item["is_controlled"] else "否",
                    item.get("hazardous_status") or "not_assessed",
                    _csv_safe(
                        " / ".join(item.get("controlled_categories") or [])
                    ),
                    _csv_safe(item.get("cas_no") or ""),
                    _csv_safe(" / ".join(item.get("hazards") or [])),
                    _csv_safe(
                        " / ".join(item.get("ghs_pictograms") or [])
                    ),
                    item.get("storage_group") or "",
                    _csv_safe(item.get("source_organization") or ""),
                    _csv_safe(item.get("handover_document_no") or ""),
                    _csv_safe(item.get("handover_document_ref") or ""),
                    (
                        fmt_ts_ms(item["received_at_ms"])
                        if item.get("received_at_ms")
                        else ""
                    ),
                    _csv_safe(item.get("received_by") or ""),
                    _csv_safe(item.get("accepted_by") or ""),
                    _csv_safe(item.get("regulatory_filing_no") or ""),
                    _csv_safe(item.get("regulatory_filing_ref") or ""),
                    _csv_safe(item.get("catalog_source") or ""),
                    _csv_safe(item.get("catalog_version") or ""),
                    _csv_safe(item.get("catalog_entry_no") or ""),
                    _csv_safe(item.get("sds_url") or ""),
                    _csv_safe(item.get("sds_revision") or ""),
                    _csv_safe(item.get("sds_verified_by") or ""),
                    "是" if item.get("dual_control_required") else "否",
                    _csv_safe(item.get("dual_control_reason") or ""),
                ]
            )
        return Response(
            "\ufeff" + buffer.getvalue(),
            mimetype="text/csv",
            headers={
                "Content-Disposition": (
                    "attachment; filename=puricore-inventory.csv"
                )
            },
        )

    @app.get("/api/inventory/disposal.csv")
    def api_inventory_disposal_csv():
        try:
            items = inventory.list_items({"status": "quarantined"})
        except InventoryError as exc:
            return _inventory_error(exc)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            ["名称", "危险性", "主要成分", "负责人", "数量", "单位", "位置"]
        )
        for item in items:
            writer.writerow(
                [
                    _csv_safe(item["material_name"]),
                    _csv_safe("/".join(item.get("hazards") or [])),
                    _csv_safe(item.get("note") or ""),
                    _csv_safe(item.get("owner") or ""),
                    item["quantity_remaining"],
                    _csv_safe(item.get("unit") or ""),
                    _csv_safe(item.get("location") or ""),
                ]
            )
        return Response(
            "\ufeff" + buffer.getvalue(),
            mimetype="text/csv",
            headers={
                "Content-Disposition": (
                    "attachment; filename=puricore-disposal.csv"
                )
            },
        )

    @app.get("/api/material-containers")
    def api_material_containers():
        try:
            return jsonify(inventory.list_items({"category": "chemical"}))
        except InventoryError as exc:
            return _inventory_error(exc)

    @app.get("/api/material-containers/labels")
    def api_material_container_labels():
        try:
            requested_ids, copies = _label_request_args()
            containers = []
            for container_id in requested_ids:
                container = repo.inventory.get_item(container_id)
                if container is None:
                    raise R201Error("material container not found", 404)
                containers.append(container)
            return Response(
                material_container_labels_html(containers, copies),
                mimetype="text/html",
            )
        except R201Error as exc:
            return _r201_error(exc)

    @app.post("/api/material-containers")
    @auth.roles_required("super_admin", "supervisor")
    @auth.csrf_required
    def api_create_material_container():
        body = dict(request.get_json(silent=True) or {})
        _stamp_actor(body, "created_by")
        body = {
            "code": body.get("container_code"),
            "external_barcode": body.get("external_barcode"),
            "name": body.get("material_name"),
            "category": "chemical",
            "quantity": body.get("quantity_remaining", 0),
            "unit": body.get("unit") or "未指定",
            "supplier": body.get("supplier"),
            "lot_no": body.get("supplier_lot"),
            "expiry_date": body.get("expires_on"),
            "created_by": body.get("created_by"),
            "created_by_user_id": body.get("created_by_user_id"),
            "client_event_id": body.get("client_event_id"),
        }
        if not body.get("client_event_id"):
            body["client_event_id"] = (
                f"material-register-{body.get('code', '')}"
            )
        try:
            return jsonify(inventory.create_item(body)), 201
        except InventoryError as exc:
            return _inventory_error(exc)

    @app.get("/api/material-containers/<int:container_id>")
    def api_material_container_detail(container_id):
        try:
            detail = inventory.get_item(container_id)
            legacy_events = []
            for movement in detail["movements"]:
                event = dict(movement)
                event["event_type"] = {
                    "experiment_used": "used",
                    "issued": "used",
                    "received": "adjusted",
                    "imported": "registered",
                }.get(movement["action"], movement["action"])
                event["quantity"] = (
                    abs(movement["delta"])
                    if movement["delta"] is not None
                    else None
                )
                legacy_events.append(event)
            return jsonify(
                {
                    "container": detail["item"],
                    "events": legacy_events,
                }
            )
        except InventoryError as exc:
            return _inventory_error(exc)

    @app.get("/api/material-containers/<int:container_id>/label")
    def api_material_container_label(container_id):
        container = repo.experiments.get_material_container_by_id(
            container_id
        )
        if container is None:
            return _r201_error(
                R201Error("material container not found", 404)
            )
        try:
            copies = int(request.args.get("copies", "1"))
        except ValueError:
            return _r201_error(R201Error("copies must be an integer"))
        if not 1 <= copies <= 20:
            return _r201_error(
                R201Error("copies must be between 1 and 20")
            )
        return Response(
            material_container_label_html(container, copies),
            mimetype="text/html",
        )

    @app.get("/api/measurement-station")
    def api_measurement_station():
        user = auth.current_user() or {}
        experiments = [
            item
            for item in r201.list_experiments()
            if item["status"] in ("draft", "in_progress")
            and item["current_step_code"] == "R201-50"
            and (
                (
                    repo.experiments.get_active_step(item["id"])
                    or {}
                ).get("step_code")
                == "R201-50"
            )
            and (
                user.get("role") in ("super_admin", "supervisor")
                or item.get("operator_user_id") == user.get("id")
            )
        ]
        viscometers = [
            {
                "id": device_id,
                "name": config.name,
                "alias": config.alias,
                "latest": (
                    _snap_to_dict(engine.latest().get(device_id))
                    if engine.latest().get(device_id)
                    else None
                ),
            }
            for device_id, config in engine.device_map().items()
            if config.type == "viscometer"
        ]
        return jsonify(
            {
                "experiments": experiments,
                "viscometers": viscometers,
                "reservations": repo.experiments.list_device_reservations(),
            }
        )

    @app.post("/api/measurement-station/claim")
    def api_claim_measurement_station():
        body = dict(request.get_json(silent=True) or {})
        _stamp_actor(body)
        try:
            device_id = int(body.get("device_id"))
            config = engine.device_map().get(device_id)
            if config is None or config.type != "viscometer":
                raise R201Error("请选择已配置的粘度计", 400)
            return jsonify(
                r201.claim_viscometer(
                    int(body.get("experiment_id")),
                    device_id,
                    body,
                )
            )
        except (R201Error, TypeError, ValueError) as exc:
            if isinstance(exc, R201Error):
                return _r201_error(exc)
            return _r201_error(R201Error("experiment_id and device_id are required"))

    @app.post("/api/measurement-station/release")
    def api_release_measurement_station():
        body = dict(request.get_json(silent=True) or {})
        _stamp_actor(body)
        try:
            device_id = int(body.get("device_id"))
            config = engine.device_map().get(device_id)
            if config is None or config.type != "viscometer":
                raise R201Error("请选择已配置的粘度计", 400)
            return jsonify(
                r201.release_viscometer(
                    int(body.get("experiment_id")),
                    device_id,
                    body,
                )
            )
        except (R201Error, TypeError, ValueError) as exc:
            if isinstance(exc, R201Error):
                return _r201_error(exc)
            return _r201_error(R201Error("experiment_id and device_id are required"))

    @app.get("/api/trace/qr.png")
    @app.get("/api/trace/qr.svg")
    def api_trace_qr():
        try:
            found = r201.resolve_scan_code(
                request.args.get("code", "")
            )
            if found["kind"] == "trace_item":
                item = found["item"]
                experiment = r201.get_experiment(
                    item["experiment_id"]
                )["experiment"]
                code = item["item_code"]
                payload = trace_qr_payload(item, experiment)
            elif found["kind"] == "storage_location":
                location = found["location"]
                code = location["location_code"]
                payload = storage_location_qr_payload(location)
            else:
                material = found["material"]
                code = material["container_code"]
                payload = material_container_qr_payload(material)
            response = Response(qr_png(payload), mimetype="image/png")
            response.headers["X-QR-Code"] = code
            return response
        except R201Error as exc:
            return _r201_error(exc)

    @app.post("/api/experiments/<int:experiment_id>/steps/<step_code>/start")
    def api_start_experiment_step(experiment_id, step_code):
        body = dict(request.get_json(silent=True) or {})
        _stamp_actor(body)
        body["device_capture"] = _device_capture(experiment_id)
        try:
            return jsonify(
                r201.start_step(
                    experiment_id,
                    step_code,
                    body.get("row_version"),
                    body,
                )
            )
        except R201Error as exc:
            return _r201_error(exc)

    @app.post(
        "/api/experiments/<int:experiment_id>/steps/"
        "<step_code>/completion-preview"
    )
    def api_preview_experiment_step(experiment_id, step_code):
        body = dict(request.get_json(silent=True) or {})
        _stamp_actor(body)
        body["device_capture"] = _device_capture(experiment_id)
        try:
            return jsonify(
                r201.preview_step_completion(
                    experiment_id,
                    step_code,
                    body.get("row_version"),
                    body.get("result") or {},
                    body,
                )
            )
        except R201Error as exc:
            return _r201_error(exc)

    @app.post("/api/experiments/<int:experiment_id>/steps/<step_code>/complete")
    def api_complete_experiment_step(experiment_id, step_code):
        body = dict(request.get_json(silent=True) or {})
        _stamp_actor(body)
        body["device_capture"] = _device_capture(experiment_id)
        try:
            return jsonify(
                r201.complete_step(
                    experiment_id,
                    step_code,
                    body.get("row_version"),
                    body.get("result") or {},
                    body,
                )
            )
        except R201Error as exc:
            return _r201_error(exc)

    @app.post("/api/experiments/<int:experiment_id>/measurements/viscosity")
    def api_record_viscosity(experiment_id):
        body = dict(request.get_json(silent=True) or {})
        _stamp_actor(body)
        body["device_capture"] = _device_capture(experiment_id)
        selected_viscometer = (
            body["device_capture"]
            .get("role_device_ids", {})
            .get("viscometer")
        )
        if selected_viscometer is None:
            candidates = [
                device_id
                for device_id, config in engine.device_map().items()
                if config.type == "viscometer"
            ]
            if len(candidates) == 1:
                selected_viscometer = candidates[0]
        try:
            if selected_viscometer is not None:
                r201.claim_viscometer(
                    experiment_id, int(selected_viscometer), body
                )
            return jsonify(
                r201.record_viscosity(
                    experiment_id, body
                )
            ), 201
        except R201Error as exc:
            return _r201_error(exc)
        finally:
            if selected_viscometer is not None:
                try:
                    r201.release_viscometer(
                        experiment_id, int(selected_viscometer), body
                    )
                except R201Error:
                    pass

    @app.post("/api/experiments/<int:experiment_id>/data-sources")
    def api_add_experiment_data_source(experiment_id):
        body = dict(request.get_json(silent=True) or {})
        _stamp_actor(body)
        try:
            created = r201.add_data_source(
                experiment_id, body
            )
            return jsonify(created), 201
        except R201Error as exc:
            return _r201_error(exc)

    @app.post("/api/experiments/<int:experiment_id>/device-bindings")
    def api_select_experiment_device(experiment_id):
        body = dict(request.get_json(silent=True) or {})
        try:
            device_id = int(body.get("device_id"))
        except (TypeError, ValueError):
            return _r201_error(R201Error("device_id is required"))
        config = engine.device_map().get(device_id)
        if config is None:
            return _r201_error(R201Error("device not found", 404))
        body["device_id"] = device_id
        body["device_type"] = config.type
        _stamp_actor(body)
        try:
            selection = r201.select_process_device(
                experiment_id,
                body,
            )
            return jsonify(
                {
                    "selection": selection,
                    "detail": _experiment_detail(experiment_id),
                }
            )
        except R201Error as exc:
            return _r201_error(exc)

    @app.post("/api/experiments/<int:experiment_id>/deviations")
    def api_open_experiment_deviation(experiment_id):
        body = dict(request.get_json(silent=True) or {})
        _stamp_actor(body, "opened_by")
        try:
            created = r201.open_deviation(
                experiment_id, body
            )
            return jsonify(created), 201
        except R201Error as exc:
            return _r201_error(exc)

    @app.post(
        "/api/experiments/<int:experiment_id>/deviations/"
        "<int:deviation_id>/resolve"
    )
    def api_resolve_experiment_deviation(experiment_id, deviation_id):
        body = dict(request.get_json(silent=True) or {})
        _stamp_actor(body, "reviewed_by")
        try:
            resolved = r201.resolve_deviation(
                experiment_id,
                deviation_id,
                body,
            )
            return jsonify(resolved)
        except R201Error as exc:
            return _r201_error(exc)

    @app.post("/api/experiments/<int:experiment_id>/submit")
    def api_submit_experiment(experiment_id):
        body = request.get_json(silent=True) or {}
        try:
            return jsonify(
                r201.submit(
                    experiment_id,
                    body.get("row_version"),
                    _current_operator(),
                    str(body.get("client_event_id", "")),
                    _current_user_id(),
                )
            )
        except R201Error as exc:
            return _r201_error(exc)

    @app.post("/api/experiments/<int:experiment_id>/review")
    @auth.roles_required("super_admin", "supervisor")
    def api_review_experiment(experiment_id):
        body = request.get_json(silent=True) or {}
        try:
            return jsonify(
                r201.review(
                    experiment_id,
                    body.get("row_version"),
                    str(body.get("action", "")),
                    _current_operator(),
                    str(body.get("client_event_id", "")),
                    body.get("disposition"),
                    _current_user_id(),
                )
            )
        except R201Error as exc:
            return _r201_error(exc)

    @app.post("/api/experiments/<int:experiment_id>/evaluate-telemetry")
    def api_evaluate_experiment_telemetry(experiment_id):
        try:
            return jsonify(r201.evaluate_telemetry(experiment_id))
        except R201Error as exc:
            return _r201_error(exc)

    @app.get("/api/experiments/<int:experiment_id>/stream")
    def api_experiment_stream(experiment_id):
        try:
            r201.experiment_revision(experiment_id)
        except R201Error as exc:
            return _r201_error(exc)
        once = request.args.get("once") == "1"

        @stream_with_context
        def generate():
            previous = None
            heartbeat = 0
            while True:
                try:
                    experiment = repo.experiments.get_experiment(
                        experiment_id
                    )
                    if experiment is None:
                        return
                    surface = _device_surface(
                        repo.experiments.list_data_source_bindings(
                            experiment_id
                        )
                    )
                    payload = {
                        "experiment": experiment,
                        "revision": r201.experiment_revision(
                            experiment_id
                        )["revision"],
                        **surface,
                    }
                    encoded = _json.dumps(
                        payload,
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    if encoded != previous:
                        previous = encoded
                        yield (
                            "retry: 1000\n"
                            "event: snapshot\n"
                            f"data: {encoded}\n\n"
                        )
                    elif heartbeat >= 14:
                        heartbeat = 0
                        yield ": keepalive\n\n"
                    else:
                        heartbeat += 1
                except (R201Error, GeneratorExit):
                    return
                if once:
                    return
                time.sleep(1)

        return Response(
            generate(),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/experiments/<int:experiment_id>/report.pdf")
    def api_experiment_report(experiment_id):
        try:
            pdf = _build_experiment_pdf(_experiment_detail(experiment_id))
        except R201Error as exc:
            return _r201_error(exc)
        return Response(
            pdf,
            mimetype="application/pdf",
            headers={
                "Content-Disposition": (
                    f"attachment; filename=r201_experiment_{experiment_id}.pdf"
                )
            },
        )

    @app.get("/api/status")
    def api_status():
        latest = engine.latest()
        dmap = engine.device_map()
        devices = []
        for did, snap in latest.items():
            dc = dmap.get(did)
            devices.append({"id": did, "name": dc.name if dc else str(did),
                            "alias": dc.alias if dc else "", "type": dc.type if dc else "",
                            "latest": _snap_to_dict(snap),
                            "communication": communication_health(snap)})
        return jsonify({"devices": devices})

    @app.get("/api/time")
    def api_time():
        return jsonify({"server_ms": int(time.time() * 1000)})

    @app.get("/api/runs")
    def api_runs():
        def _int_param(key, default, min_val=0, max_val=None):
            try:
                v = int(request.args.get(key, default))
            except (TypeError, ValueError):
                abort(400, description=f"{key} must be an integer")
            if v < min_val or (max_val is not None and v > max_val):
                abort(400, description=f"{key} out of range")
            return v
        def _int_or_none(key):
            v = request.args.get(key)
            if v is None:
                return None
            try:
                return int(v)
            except (TypeError, ValueError):
                abort(400, description=f"{key} must be an integer")
        def _bool_or_none(key):
            v = request.args.get(key)
            if v is None:
                return None
            return v.lower() in ("1", "true", "yes")
        runs = repo.list_runs(
            limit=_int_param("limit", 50, max_val=_MAX_RUN_LIMIT),
            offset=_int_param("offset", 0),
            device_id=_int_or_none("device_id"),
            operator=request.args.get("operator"),
            project_tag=request.args.get("project_tag"),
            experiment_tag=request.args.get("experiment_tag"),
            start_ms=_int_or_none("start_ms"),
            end_ms=_int_or_none("end_ms"),
            end_status=request.args.get("end_status"),
            tagged=_bool_or_none("tagged"),
        )
        return jsonify([_run_to_dict(r) for r in runs])

    @app.get("/api/runs/untagged")
    def api_untagged():
        return jsonify([_run_to_dict(r) for r in repo.list_untagged_runs()])

    @app.get("/api/runs/<int:run_id>")
    def api_run_detail(run_id):
        run = repo.get_run(run_id)
        if run is None:
            return jsonify({"error": "run not found"}), 404
        samples = repo.list_samples_for_run(run_id)
        events = repo.list_events_for_run(run_id)
        return jsonify({
            "run": _run_to_dict(run),
            "samples": [_sample_to_dict(s) for s in samples],
            "events": [_event_to_dict(e) for e in events],
        })

    @app.get("/api/runs/<int:run_id>/samples")
    def api_run_samples(run_id):
        if repo.get_run(run_id) is None:
            return jsonify({"error": "run not found"}), 404
        return jsonify([_sample_to_dict(s) for s in repo.list_samples_for_run(run_id)])

    @app.get("/api/runs/<int:run_id>/events")
    def api_run_events(run_id):
        if repo.get_run(run_id) is None:
            return jsonify({"error": "run not found"}), 404
        return jsonify([_event_to_dict(e) for e in repo.list_events_for_run(run_id)])

    @app.get("/api/runs/export.csv")
    def api_runs_export():
        runs = repo.list_runs(limit=100000)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id", "device_id", "started", "ended", "duration_s",
                    "end_status", "operator", "project_tag", "experiment_tag",
                    "remark", "result_acc_volume", "result_acc_unit",
                    "alarm_count", "tagged",
                    "actual_volume", "actual_unit",
                    "syringe_code", "target_volume", "inject_rate",
                    "pause_delay_ms", "repeat_count", "force"])
        for r in runs:
            try:
                sp = _json.loads(r.setpoints_json) if r.setpoints_json else {}
            except Exception:
                sp = {}
            row = [r.id, r.device_id,
                   fmt_ts_ms(r.started_ms) if r.started_ms else "",
                   fmt_ts_ms(r.ended_ms) if r.ended_ms else "",
                   (r.duration_ms / 1000) if r.duration_ms is not None else "",
                   r.end_status or "", r.operator or "", r.project_tag or "",
                   r.experiment_tag or "", r.remark or "",
                   r.result_acc_volume if r.result_acc_volume is not None else "",
                   r.result_acc_unit or "",
                   r.alarm_count if r.alarm_count is not None else "",
                   r.tagged,
                   r.actual_volume if r.actual_volume is not None else "",
                   r.actual_unit or "",
                   sp.get("syringe_code", ""),
                   sp.get("target_volume", ""),
                   sp.get("inject_rate", ""),
                   sp.get("pause_delay_ms", ""),
                   sp.get("repeat_count", ""),
                   sp.get("force", "")]
            w.writerow([_csv_safe(c) for c in row])   # neutralize CSV formula injection
        return Response(buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=pump_runs.csv"})

    @app.get("/api/runs/export.xlsx")
    def api_runs_export_xlsx():
        def _int_param(key, default, min_val=0, max_val=None):
            try:
                value = int(request.args.get(key, default))
            except (TypeError, ValueError):
                abort(400, description=f"{key} must be an integer")
            if value < min_val or (
                max_val is not None and value > max_val
            ):
                abort(400, description=f"{key} out of range")
            return value

        def _int_or_none(key):
            v = request.args.get(key)
            if v is None:
                return None
            try:
                return int(v)
            except (TypeError, ValueError):
                abort(400, description=f"{key} must be an integer")
        def _bool_or_none(key):
            v = request.args.get(key)
            if v is None:
                return None
            return v.lower() in ("1", "true", "yes")
        runs = repo.list_runs(
            limit=_int_param("limit", 100000, min_val=1, max_val=100000),
            offset=_int_param("offset", 0),
            device_id=_int_or_none("device_id"),
            operator=request.args.get("operator"),
            project_tag=request.args.get("project_tag"),
            experiment_tag=request.args.get("experiment_tag"),
            start_ms=_int_or_none("start_ms"),
            end_ms=_int_or_none("end_ms"),
            end_status=request.args.get("end_status"),
            tagged=_bool_or_none("tagged"),
        )
        wb = Workbook()
        ws_runs = wb.active
        ws_runs.title = "runs"
        run_headers = ["id", "device_id", "started", "ended", "duration_s", "end_status",
                       "operator", "project_tag", "experiment_tag", "remark",
                       "actual_volume", "actual_unit", "result_acc_volume", "result_acc_unit",
                       "alarm_count", "tagged", "work_mode", "target_volume"]
        ws_runs.append(run_headers)
        sample_headers = ["run_id", "ts", "state", "flow_rate", "delivered_volume", "temp_c"]
        ws_samples = wb.create_sheet(title="samples")
        ws_samples.append(sample_headers)
        for r in runs:
            sp = {}
            try:
                sp = _json.loads(r.setpoints_json) if r.setpoints_json else {}
            except Exception:
                pass
            ws_runs.append([_xlsx_safe(c) for c in [
                r.id, r.device_id,
                fmt_ts_ms(r.started_ms) if r.started_ms else "",
                fmt_ts_ms(r.ended_ms) if r.ended_ms else "",
                (r.duration_ms / 1000) if r.duration_ms is not None else "",
                r.end_status or "", r.operator or "", r.project_tag or "",
                r.experiment_tag or "", r.remark or "",
                r.actual_volume if r.actual_volume is not None else "",
                r.actual_unit or "",
                r.result_acc_volume if r.result_acc_volume is not None else "",
                r.result_acc_unit or "",
                r.alarm_count if r.alarm_count is not None else "",
                r.tagged,
                r.work_mode or sp.get("work_mode", ""),
                r.target_volume if r.target_volume is not None else sp.get("target_volume", ""),
            ]])
            for s in repo.list_samples_for_run(r.id):
                ws_samples.append([_xlsx_safe(c) for c in [
                    s.run_id, fmt_ts_ms(s.ts_ms), s.state or "",
                    s.flow_rate if s.flow_rate is not None else "",
                    s.delivered_volume if s.delivered_volume is not None else "",
                    s.temp_c if s.temp_c is not None else "",
                ]])
        buf = BytesIO()
        wb.save(buf)
        buf.seek(0)
        return Response(buf.getvalue(), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": "attachment; filename=pump_runs.xlsx"})

    @app.get("/api/runs/<int:run_id>/report.pdf")
    def api_run_report_pdf(run_id):
        run = repo.get_run(run_id)
        if run is None:
            return jsonify({"error": "run not found"}), 404
        samples = repo.list_samples_for_run(run_id)
        events = repo.list_events_for_run(run_id)
        pdf = _build_pdf_report(run, samples, events)
        return Response(pdf, mimetype="application/pdf",
                        headers={"Content-Disposition": f"attachment; filename=run_{run_id}_report.pdf"})

    @app.post("/api/runs/<int:run_id>/tag")
    def api_tag(run_id):
        if repo.get_run(run_id) is None:
            return jsonify({"error": "run not found"}), 404
        body = request.get_json(silent=True) or {}
        repo.tag_run(
            run_id,
            _current_operator(),
            body.get("project_tag", ""),
            body.get("experiment_tag", ""),
            body.get("remark", ""),
            operator_user_id=_current_user_id(),
        )
        return jsonify({"ok": True})

    @app.get("/device/<int:device_id>")
    @login_required
    def device_page(device_id):
        return send_from_directory(app.static_folder, "device.html")

    @app.get("/run/<int:run_id>")
    @login_required
    def run_page(run_id):
        return send_from_directory(app.static_folder, "run.html")

    @app.get("/sensor/<int:device_id>")
    @login_required
    def sensor_page(device_id):
        return send_from_directory(app.static_folder, "sensor.html")

    @app.get("/api/devices/<int:device_id>")
    def device_detail(device_id):
        latest = engine.latest()
        dmap = engine.device_map()
        snap = latest.get(device_id)
        dc = dmap.get(device_id)
        # SECURITY: filter by device_id — never return another device's runs.
        runs = repo.list_runs_for_device(device_id, limit=20)
        snap_dict = _snap_to_dict(snap) if snap else None
        return jsonify({
            "device": {"id": device_id, "name": dc.name if dc else "", "alias": dc.alias if dc else "",
                       "type": dc.type if dc else ""},
            "latest": snap_dict,
            "communication": communication_health(snap),
            "metrics": snap_dict["metrics"] if snap_dict else {},
            "runs": [_run_to_dict(r) for r in runs],
        })

    return app
