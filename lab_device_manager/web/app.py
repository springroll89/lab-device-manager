from __future__ import annotations
import csv
import io
import json as _json
import time
from io import BytesIO

import functools
from datetime import timedelta
from flask import Flask, Response, abort, jsonify, redirect, request, send_from_directory, session, url_for
from openpyxl import Workbook
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

from lab_device_manager.config import _load_or_create_secret
from lab_device_manager.db.models import fmt_ts_ms

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


def create_app(engine, repo, secret_key: str = "", login_password: str = ""):
    app = Flask(__name__, static_folder="static", static_url_path="/static")
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

    @app.before_request
    def require_login():
        if not login_password:
            return None
        public_endpoints = {"login_page", "login", "static"}
        if request.endpoint in public_endpoints:
            return None
        if session.get("logged_in"):
            return None
        return _unauthorized_response()

    @app.get("/login")
    def login_page():
        if not login_password:
            return redirect(url_for("index"))
        return send_from_directory(app.static_folder, "login.html")

    @app.post("/login")
    def login():
        body = request.get_json(silent=True) or {}
        if body.get("password") == login_password:
            session["logged_in"] = True
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": "invalid password"}), 401

    @app.post("/logout")
    def logout():
        session.pop("logged_in", None)
        return redirect(url_for("login_page"))

    @app.get("/")
    @login_required
    def index():
        return send_from_directory(app.static_folder, "index.html")

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
        channels_data = metrics.get("channels", [])
        channels = []
        for i, ch in enumerate(channels_data):
            channels.append({
                "channel": i + 1,
                "temp_c": ch.get("temp"),
                "humid_rh": ch.get("humid"),
            })
        return jsonify({
            "device_id": device_id,
            "device_name": dc.name,
            "device_alias": dc.alias,
            "state": snap.state,
            "timestamp": snap.timestamp,
            "channels": channels,
            "avg_temp_c": snap.temp_c,
            "avg_humid_rh": metrics.get("ch1_humid_rh"),
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

        adapter = engine._get_adapter(device_id)
        if adapter is None and hasattr(engine, '_extra_adapters'):
            adapter = engine._extra_adapters.get(device_id)

        if adapter is None or not adapter.is_connected:
            return jsonify({
                "device_id": device_id,
                "device_name": dc.name,
                "device_alias": dc.alias,
                "state": "offline",
                "channels": [],
            })

        try:
            data = adapter.read_channels()
            channels = data.get("channels", [])
            
            if len(channels) == 0:
                return jsonify({
                    "device_id": device_id,
                    "device_name": dc.name,
                    "device_alias": dc.alias,
                    "state": "error",
                    "error": "未读取到通道数据",
                    "channels": [],
                })
            
            import json as _json
            avg_temp = data.get("avg_temp_c", 0)
            avg_humid = data.get("avg_humid_rh", 0)
            
            repo.add_sample(
                run_id=None,
                device_id=device_id,
                ts_ms=int(time.time() * 1000),
                state="running",
                flow_rate=None,
                delivered_volume=None,
                temp_c=avg_temp,
                metrics_json=_json.dumps({
                    "channels": channels,
                    "ch1_temp_c": channels[0].get("temp_c"),
                    "ch1_humid_rh": channels[0].get("humid_rh"),
                    "ch2_temp_c": channels[1].get("temp_c"),
                    "ch2_humid_rh": channels[1].get("humid_rh"),
                    "ch3_temp_c": channels[2].get("temp_c"),
                    "ch3_humid_rh": channels[2].get("humid_rh"),
                })
            )
            
            return jsonify({
                "device_id": device_id,
                "device_name": dc.name,
                "device_alias": dc.alias,
                "state": "running",
                "channels": channels,
            })
        except Exception as e:
            return jsonify({
                "device_id": device_id,
                "device_name": dc.name,
                "device_alias": dc.alias,
                "state": "error",
                "error": str(e),
                "channels": [],
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
