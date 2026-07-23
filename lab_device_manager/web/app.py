from __future__ import annotations
import csv
import io
import json as _json
import time
from io import BytesIO

import functools
import sqlite3
from datetime import timedelta
from flask import Flask, Response, abort, jsonify, redirect, request, send_from_directory, session, url_for
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
from lab_device_manager.experiments.service import R201Error, R201Service, STEP_LABELS
from lab_device_manager.web.trace_labels import (
    qr_svg,
    storage_location_label_html,
    trace_labels_html,
)

_MAX_RUN_LIMIT = 1000


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


def _whd_channels(metrics):
    channels = []
    for index, channel in enumerate((metrics or {}).get("channels", [])):
        channels.append(
            {
                "channel": channel.get("channel", index + 1),
                "temp_c": channel.get("temp_c", channel.get("temp")),
                "humid_rh": channel.get("humid_rh", channel.get("humid")),
            }
        )
    return channels


def _run_to_dict(run):
    d = {
        "id": run.id, "device_id": run.device_id,
        "started_ms": run.started_ms, "ended_ms": run.ended_ms,
        "duration_ms": run.duration_ms, "end_status": run.end_status,
        "operator": run.operator, "project_tag": run.project_tag,
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
    formula character, prefix a single quote so spreadsheet apps treat it as text.
    NOTE: the app is bound to 127.0.0.1 (single-user, local lab tool) — add auth
    before exposing it on a network."""
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
    material_rows = [["物料", "批号", "有效期", "理论量", "实际量", "外观"]]
    for item in detail["materials"]:
        material_rows.append(
            [
                item["material_name"],
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
        colWidths=[85, 85, 75, 80, 80, 105],
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


def create_app(engine, repo, secret_key: str = "", login_password: str = ""):
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    r201 = R201Service(repo)
    app.secret_key = secret_key if secret_key else _load_or_create_secret()
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    )

    def login_required(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            if login_password and not session.get("logged_in"):
                return redirect(url_for("login_page"))
            return view(*args, **kwargs)
        return wrapped

    def _unauthorized_response():
        if request.path.startswith("/api/") or request.is_json:
            return jsonify({"error": "unauthorized"}), 401
        return redirect(url_for("login_page"))

    def _current_operator():
        return str(session.get("username") or "本机操作员").strip()

    def _device_capture():
        captured_at_ms = int(time.time() * 1000)
        latest = engine.latest()
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
            "devices": devices,
        }

    def _ensure_automatic_sources(experiment_id: int):
        detail = r201.get_experiment(experiment_id)
        experiment = detail["experiment"]
        existing = {
            (
                item["device_id"],
                item["device_role"],
                item["metric_key"],
            )
            for item in detail["data_sources"]
            if item["unlinked_at_ms"] is None
        }
        role_map = {
            "tyd02": (("acid_pump", "acc_volume"),),
            "stirrer": (
                ("stirrer", "speed"),
                ("reaction_temp", "temp_c"),
            ),
            "viscometer": (("viscometer", "viscosity_mPas"),),
            "whd46": (("environment", "temp_c"),),
        }
        for device_id, config in engine.device_map().items():
            for role, metric_key in role_map.get(config.type, ()):
                key = (device_id, role, metric_key)
                if key in existing:
                    continue
                r201.add_data_source(
                    experiment_id,
                    {
                        "client_event_id": (
                            f"auto-bind-{experiment_id}-{device_id}-"
                            f"{role}-{metric_key}"
                        ),
                        "actor": experiment["operator"],
                        "device_id": device_id,
                        "device_role": role,
                        "metric_key": metric_key,
                        "linked_at_ms": experiment["created_at_ms"],
                        "link_method": "automatic",
                        "confidence": 1.0,
                        "source_type": "derived",
                    },
                )
                existing.add(key)

    @app.before_request
    def require_login():
        if not login_password:
            return None
        public_endpoints = {"login_page", "login", "auth_mode", "static"}
        if request.endpoint in public_endpoints:
            return None
        if session.get("logged_in"):
            return None
        return _unauthorized_response()

    @app.get("/login")
    def login_page():
        return send_from_directory(app.static_folder, "login.html")

    @app.get("/api/auth-mode")
    def auth_mode():
        return jsonify({"password_required": bool(login_password)})

    @app.post("/login")
    def login():
        body = request.get_json(silent=True) or {}
        username = str(body.get("username") or "").strip()[:64]
        if not username:
            return jsonify(
                {"ok": False, "error": "operator name is required"}
            ), 400
        if login_password and body.get("password") != login_password:
            return jsonify(
                {"ok": False, "error": "invalid password"}
            ), 401
        session["logged_in"] = True
        session["username"] = username
        return jsonify({"ok": True, "operator": username})

    @app.post("/logout")
    def logout():
        session.pop("logged_in", None)
        session.pop("username", None)
        return redirect(url_for("login_page"))

    @app.get("/")
    @login_required
    def index():
        return send_from_directory(app.static_folder, "index.html")

    @app.get("/experiments")
    @app.get("/experiments/<int:experiment_id>")
    @login_required
    def experiments_page(experiment_id=None):
        return send_from_directory(app.static_folder, "experiment.html")

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

    def _experiment_detail(experiment_id: int):
        _ensure_automatic_sources(experiment_id)
        detail = r201.get_experiment(experiment_id)
        latest = engine.latest()
        device_map = engine.device_map()
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
                }
            )
        detail["available_devices"] = devices
        device_by_id = {item["id"]: item for item in devices}

        def process_device(role):
            bindings = [
                item
                for item in detail["data_sources"]
                if item["device_role"] == role
                and item["unlinked_at_ms"] is None
            ]
            if not bindings:
                return {"bound": False}
            binding = bindings[-1]
            device = device_by_id.get(binding["device_id"])
            latest_snapshot = device.get("latest") if device else None
            return {
                "bound": True,
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

        detail["process_status"] = {
            "acid_pump": process_device("acid_pump"),
            "reaction_temp": process_device("reaction_temp"),
            "stirrer": process_device("stirrer"),
            "viscometer": process_device("viscometer"),
            "environment": process_device("environment"),
        }
        return detail

    @app.get("/api/experiments")
    def api_experiments():
        return jsonify(r201.list_experiments())

    @app.get("/api/session")
    def api_session():
        return jsonify(
            {
                "authenticated": bool(session.get("username")),
                "operator": _current_operator(),
                "password_required": bool(login_password),
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
            if login_password:
                body["operator"] = _current_operator()
            else:
                body.setdefault("operator", _current_operator())
            body.setdefault("reviewer", "")
            created = r201.create_experiment(body)
            return jsonify(created), 201
        except (R201Error, sqlite3.IntegrityError) as exc:
            return _r201_error(exc)

    @app.get("/api/experiments/<int:experiment_id>")
    def api_experiment_detail(experiment_id):
        try:
            return jsonify(_experiment_detail(experiment_id))
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
        if login_password:
            body["actor"] = _current_operator()
        else:
            body.setdefault("actor", _current_operator())
        try:
            return jsonify(
                r201.create_trace_items(experiment_id, body)
            ), 201
        except R201Error as exc:
            return _r201_error(exc)

    @app.post("/api/trace-items/<int:trace_item_id>/store")
    def api_store_trace_item(trace_item_id):
        body = dict(request.get_json(silent=True) or {})
        if login_password:
            body["actor"] = _current_operator()
        else:
            body.setdefault("actor", _current_operator())
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
        if login_password:
            body["actor"] = _current_operator()
        else:
            body.setdefault("actor", _current_operator())
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
        if login_password:
            body["actor"] = _current_operator()
        else:
            body.setdefault("actor", _current_operator())
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
        return jsonify(r201.list_storage_locations())

    @app.post("/api/storage-locations")
    def api_create_storage_location():
        body = dict(request.get_json(silent=True) or {})
        if login_password:
            body["actor"] = _current_operator()
        else:
            body.setdefault("actor", _current_operator())
        try:
            return jsonify(r201.create_storage_location(body)), 201
        except R201Error as exc:
            return _r201_error(exc)

    @app.post("/api/storage-locations/<int:location_id>/print")
    def api_request_location_label(location_id):
        body = dict(request.get_json(silent=True) or {})
        if login_password:
            body["actor"] = _current_operator()
        else:
            body.setdefault("actor", _current_operator())
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

    @app.get("/api/trace/qr.svg")
    def api_trace_qr():
        try:
            found = r201.lookup_trace_code(
                request.args.get("code", "")
            )
            if found["kind"] == "trace_item":
                code = found["item"]["item_code"]
            else:
                code = found["location"]["location_code"]
            return Response(qr_svg(code), mimetype="image/svg+xml")
        except R201Error as exc:
            return _r201_error(exc)

    @app.post("/api/experiments/<int:experiment_id>/steps/<step_code>/start")
    def api_start_experiment_step(experiment_id, step_code):
        body = dict(request.get_json(silent=True) or {})
        body["device_capture"] = _device_capture()
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
        body["device_capture"] = _device_capture()
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
        body["device_capture"] = _device_capture()
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
        body["device_capture"] = _device_capture()
        try:
            return jsonify(
                r201.record_viscosity(
                    experiment_id, body
                )
            ), 201
        except R201Error as exc:
            return _r201_error(exc)

    @app.post("/api/experiments/<int:experiment_id>/data-sources")
    def api_add_experiment_data_source(experiment_id):
        try:
            created = r201.add_data_source(
                experiment_id, request.get_json(silent=True) or {}
            )
            return jsonify(created), 201
        except R201Error as exc:
            return _r201_error(exc)

    @app.post("/api/experiments/<int:experiment_id>/deviations")
    def api_open_experiment_deviation(experiment_id):
        try:
            created = r201.open_deviation(
                experiment_id, request.get_json(silent=True) or {}
            )
            return jsonify(created), 201
        except R201Error as exc:
            return _r201_error(exc)

    @app.post(
        "/api/experiments/<int:experiment_id>/deviations/"
        "<int:deviation_id>/resolve"
    )
    def api_resolve_experiment_deviation(experiment_id, deviation_id):
        try:
            resolved = r201.resolve_deviation(
                experiment_id,
                deviation_id,
                request.get_json(silent=True) or {},
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
                    str(body.get("actor", "")),
                    str(body.get("client_event_id", "")),
                )
            )
        except R201Error as exc:
            return _r201_error(exc)

    @app.post("/api/experiments/<int:experiment_id>/review")
    def api_review_experiment(experiment_id):
        body = request.get_json(silent=True) or {}
        try:
            return jsonify(
                r201.review(
                    experiment_id,
                    body.get("row_version"),
                    str(body.get("action", "")),
                    str(body.get("reviewer", "")),
                    str(body.get("client_event_id", "")),
                    body.get("disposition"),
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
            detail = _experiment_detail(experiment_id)
        except R201Error as exc:
            return _r201_error(exc)
        payload = {
            "experiment": detail["experiment"],
            "endpoint_ready": detail["endpoint_ready"],
            "available_devices": detail["available_devices"],
            "process_status": detail["process_status"],
            "temperature_series": detail["temperature_series"],
            "temperature_checkpoints": detail["temperature_checkpoints"],
            "reached_temperature": detail["reached_temperature"],
            "telemetry_gaps": detail["telemetry_gaps"],
            "telemetry_integrity_status": detail["telemetry_integrity_status"],
        }
        body = (
            "retry: 1000\n"
            "event: snapshot\n"
            f"data: {_json.dumps(payload, ensure_ascii=False)}\n\n"
        )
        return Response(body, mimetype="text/event-stream")

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
                            "latest": _snap_to_dict(snap)})
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
        def _int_or_none(key):
            v = request.args.get(key)
            return int(v) if v is not None else None
        def _bool_or_none(key):
            v = request.args.get(key)
            if v is None:
                return None
            return v.lower() in ("1", "true", "yes")
        runs = repo.list_runs(
            limit=int(request.args.get("limit", 100000)),
            offset=int(request.args.get("offset", 0)),
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
        body = request.get_json(silent=True) or {}
        repo.tag_run(run_id, body.get("operator", ""), body.get("project_tag", ""),
                     body.get("experiment_tag", ""), body.get("remark", ""))
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
            "metrics": snap_dict["metrics"] if snap_dict else {},
            "runs": [_run_to_dict(r) for r in runs],
        })

    @app.get("/api/devices/<int:device_id>/sensor-data")
    def device_sensor_data(device_id):
        dmap = engine.device_map()
        dc = dmap.get(device_id)
        if not dc or dc.type != "whd46":
            return jsonify({"error": "device not found or not a sensor"}), 404
        latest = engine.latest()
        snap = latest.get(device_id)
        if not snap or snap.state == "offline":
            return jsonify({
                "device_id": device_id,
                "device_name": dc.name,
                "device_alias": dc.alias,
                "state": "offline",
                "channels": [],
                "avg_temp_c": None,
                "avg_humid_rh": None,
            })
        metrics = snap.metrics or {}
        channels = _whd_channels(metrics)
        return jsonify({
            "device_id": device_id,
            "device_name": dc.name,
            "device_alias": dc.alias,
            "state": snap.state,
            "timestamp": snap.timestamp,
            "channels": channels,
            "avg_temp_c": snap.temp_c,
            "avg_humid_rh": metrics.get("avg_humid_rh"),
        })

    @app.get("/api/devices/<int:device_id>/sensor-history")
    def device_sensor_history(device_id):
        dmap = engine.device_map()
        dc = dmap.get(device_id)
        if not dc or dc.type != "whd46":
            return jsonify({"error": "device not found or not a sensor"}), 404
        def _int_param(key, default, min_val=0):
            try:
                return int(request.args.get(key, default))
            except (TypeError, ValueError):
                return default
        limit = _int_param("limit", 100)
        samples = repo.list_samples_for_device(device_id, limit=limit)
        history = []
        for s in samples:
            metrics = {}
            try:
                if s.metrics_json:
                    metrics = _json.loads(s.metrics_json)
            except Exception:
                pass
            channels_data = metrics.get("channels", [])
            channels = []
            for i, ch in enumerate(channels_data):
                channels.append({
                    "channel": i + 1,
                    "temp_c": ch.get("temp_c"),
                    "humid_rh": ch.get("humid_rh"),
                })
            history.append({
                "ts_ms": s.ts_ms,
                "state": s.state,
                "temp_c": s.temp_c,
                "channels": channels,
            })
        return jsonify({
            "device_id": device_id,
            "device_name": dc.name,
            "device_alias": dc.alias,
            "history": history,
        })

    @app.get("/api/serial-ports")
    def serial_ports():
        try:
            from lab_device_manager.instruments.whd46_33 import enum_serial_ports
            ports = enum_serial_ports()
            return jsonify({"ports": ports})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.get("/api/browse-directory")
    def browse_directory():
        import os
        path = request.args.get('path', '')
        try:
            if not path:
                if os.name == 'nt':
                    drives = [f"{d}:\\" for d in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' if os.path.exists(f"{d}:\\")]
                    return jsonify({
                        "current": "",
                        "parent": "",
                        "directories": drives
                    })
                else:
                    return jsonify({
                        "current": "/",
                        "parent": "",
                        "directories": [d for d in os.listdir('/') if os.path.isdir(os.path.join('/', d))]
                    })
            
            if not os.path.isdir(path):
                return jsonify({"error": "invalid path"}), 400
            
            parent = os.path.dirname(path)
            if parent == path:
                parent = ""
            
            directories = []
            try:
                for item in os.listdir(path):
                    full_path = os.path.join(path, item)
                    if os.path.isdir(full_path):
                        directories.append(item)
            except PermissionError:
                pass
            
            directories.sort()
            
            return jsonify({
                "current": path,
                "parent": parent,
                "directories": directories
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.post("/api/devices/<int:device_id>/connect")
    def device_connect(device_id):
        dmap = engine.device_map()
        dc = dmap.get(device_id)
        if not dc or dc.type != "whd46":
            return jsonify({"error": "device not found or not a whd46 sensor"}), 404

        body = request.get_json(silent=True) or {}
        port = body.get("port", "auto")

        from lab_device_manager.instruments.whd46_33 import WHD46Adapter
        adapter = engine._get_adapter(device_id)
        if adapter is None:
            adapter = WHD46Adapter(slave=dc.modbus_addr, baudrate=dc.baudrate, parity=dc.parity)
            if not hasattr(engine, '_extra_adapters'):
                engine._extra_adapters = {}
            engine._extra_adapters[device_id] = adapter

        if port == "auto":
            result = adapter.auto_detect()
        else:
            result = adapter.connect(port)

        if result["ok"]:
            return jsonify({
                "ok": True,
                "port": adapter.connected_port,
                "message": f"已连接到 {adapter.connected_port}"
            })
        else:
            return jsonify({
                "ok": False,
                "error": result["error"],
                "detail": result["detail"]
            }), 400

    @app.post("/api/devices/<int:device_id>/disconnect")
    def device_disconnect(device_id):
        dmap = engine.device_map()
        dc = dmap.get(device_id)
        if not dc or dc.type != "whd46":
            return jsonify({"error": "device not found or not a whd46 sensor"}), 404

        adapter = engine._get_adapter(device_id)
        if adapter is None and hasattr(engine, '_extra_adapters'):
            adapter = engine._extra_adapters.get(device_id)

        if adapter is None:
            return jsonify({"error": "adapter not found"}), 500

        adapter.close()
        return jsonify({"ok": True, "message": "已断开连接"})

    @app.get("/api/devices/<int:device_id>/realtime-data")
    def device_realtime_data(device_id):
        dmap = engine.device_map()
        dc = dmap.get(device_id)
        if not dc or dc.type != "whd46":
            return jsonify({"error": "device not found or not a whd46 sensor"}), 404

        snap = engine.latest().get(device_id)
        if snap is None or snap.state == "offline":
            return jsonify({
                "device_id": device_id,
                "device_name": dc.name,
                "device_alias": dc.alias,
                "state": "offline",
                "channels": [],
            })
        metrics = snap.metrics or {}
        channels = _whd_channels(metrics)
        return jsonify({
            "device_id": device_id,
            "device_name": dc.name,
            "device_alias": dc.alias,
            "state": snap.state,
            "timestamp": snap.timestamp,
            "avg_temp_c": snap.temp_c,
            "avg_humid_rh": metrics.get("avg_humid_rh"),
            "channels": channels,
        })

    @app.get("/api/devices/<int:device_id>/export-csv")
    def device_export_csv(device_id):
        dmap = engine.device_map()
        dc = dmap.get(device_id)
        if not dc or dc.type != "whd46":
            return jsonify({"error": "device not found or not a whd46 sensor"}), 404

        save_path = request.args.get('save_path', '')
        samples = repo.list_samples_for_device(device_id, limit=100000)

        import io
        import csv
        import os
        from datetime import datetime
        
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "时间戳",
            "CH1温度(℃)", "CH1湿度(%RH)",
            "CH2温度(℃)", "CH2湿度(%RH)",
            "CH3温度(℃)", "CH3湿度(%RH)",
        ])

        for s in samples:
            metrics = {}
            try:
                if s.metrics_json:
                    metrics = _json.loads(s.metrics_json)
            except Exception:
                pass
            channels = metrics.get("channels", [])
            row = [datetime.fromtimestamp(s.ts_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")]
            for i in range(3):
                ch = channels[i] if i < len(channels) else {}
                t = ch.get("temp_c")
                h = ch.get("humid_rh")
                row.append(f"{t:.1f}" if t is not None else "")
                row.append(f"{h:.1f}" if h is not None else "")
            writer.writerow(row)

        filename = f"WHD46_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        
        if save_path:
            try:
                os.makedirs(save_path, exist_ok=True)
                full_path = os.path.join(save_path, filename)
                with open(full_path, 'w', encoding='utf-8-sig', newline='') as f:
                    f.write(output.getvalue())
                return jsonify({
                    "ok": True,
                    "message": f"CSV文件已保存到: {full_path}",
                    "filename": filename,
                    "path": full_path
                })
            except Exception as e:
                return jsonify({
                    "ok": False,
                    "error": f"保存文件失败: {str(e)}"
                }), 500
        else:
            csv_content = '\ufeff' + output.getvalue()
            response = app.make_response(csv_content)
            response.headers["Content-Type"] = "text/csv; charset=utf-8"
            response.headers["Content-Disposition"] = f"attachment; filename={filename}"
            return response

    @app.post("/api/devices/<int:device_id>/log-csv")
    def device_log_csv(device_id):
        dmap = engine.device_map()
        dc = dmap.get(device_id)
        if not dc or dc.type != "whd46":
            return jsonify({"error": "device not found or not a whd46 sensor"}), 404

        body = request.get_json(silent=True) or {}
        save_path = body.get("save_path", "data")
        data = body.get("data", {})

        import os
        import csv
        from datetime import datetime

        os.makedirs(save_path, exist_ok=True)
        
        filename = f"WHD46_data_{datetime.now().strftime('%Y%m%d')}.csv"
        full_path = os.path.join(save_path, filename)
        
        file_exists = os.path.exists(full_path)
        
        with open(full_path, 'a', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            
            if not file_exists:
                writer.writerow([
                    "时间戳",
                    "CH1温度(℃)", "CH1湿度(%RH)",
                    "CH2温度(℃)", "CH2湿度(%RH)",
                    "CH3温度(℃)", "CH3湿度(%RH)",
                ])
            
            ts_ms = data.get("ts_ms", datetime.now().timestamp() * 1000)
            channels = data.get("channels", [])
            row = [datetime.fromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")]
            for i in range(3):
                ch = channels[i] if i < len(channels) else {}
                t = ch.get("temp_c")
                h = ch.get("humid_rh")
                row.append(f"{t:.1f}" if t is not None else "")
                row.append(f"{h:.1f}" if h is not None else "")
            writer.writerow(row)

        return jsonify({
            "ok": True,
            "filename": filename,
            "path": full_path
        })

    return app
