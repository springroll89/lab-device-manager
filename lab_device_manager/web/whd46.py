"""WHD46-33 专属 Web API Blueprint。"""

from __future__ import annotations

import csv
import io
import json
import os
from datetime import datetime

from flask import Blueprint, Response, jsonify, request

from lab_device_manager.instruments.whd46 import (
    auto_detect_port,
    enum_serial_ports,
    probe_port,
)


def _channels(metrics: dict) -> list[dict]:
    result = []
    for index, channel in enumerate((metrics or {}).get("channels", [])):
        result.append(
            {
                "channel": channel.get("channel", index + 1),
                "temp_c": channel.get("temp_c", channel.get("temp")),
                "humid_rh": channel.get(
                    "humid_rh",
                    channel.get("humid"),
                ),
            }
        )
    return result


def _metrics_from_sample(sample) -> dict:
    try:
        return json.loads(sample.metrics_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}


def _average_humidity(metrics: dict, channels: list[dict]):
    value = (metrics or {}).get("avg_humid_rh")
    if value is not None:
        return value
    values = [
        item.get("humid_rh")
        for item in channels
        if item.get("humid_rh") is not None
    ]
    return sum(values) / len(values) if values else None


def create_whd46_blueprint(engine, repo) -> Blueprint:
    blueprint = Blueprint("whd46", __name__)

    def device_config(device_id: int):
        config = engine.device_map().get(device_id)
        if config is None or config.type != "whd46":
            return None
        return config

    def current_payload(device_id: int):
        config = device_config(device_id)
        if config is None:
            return None
        snapshot = engine.latest().get(device_id)
        if snapshot is None or snapshot.state == "offline":
            return {
                "device_id": device_id,
                "device_name": config.name,
                "device_alias": config.alias,
                "state": "offline",
                "channels": [],
                "avg_temp_c": None,
                "avg_humid_rh": None,
            }
        metrics = snapshot.metrics or {}
        channels = _channels(metrics)
        return {
            "device_id": device_id,
            "device_name": config.name,
            "device_alias": config.alias,
            "state": snapshot.state,
            "timestamp": snapshot.timestamp,
            "channels": channels,
            "avg_temp_c": snapshot.temp_c,
            "avg_humid_rh": _average_humidity(metrics, channels),
        }

    @blueprint.get("/api/devices/<int:device_id>/sensor-data")
    def device_sensor_data(device_id):
        payload = current_payload(device_id)
        if payload is None:
            return jsonify({"error": "device not found or not a sensor"}), 404
        return jsonify(payload)

    @blueprint.get("/api/devices/<int:device_id>/realtime-data")
    def device_realtime_data(device_id):
        payload = current_payload(device_id)
        if payload is None:
            return jsonify(
                {"error": "device not found or not a whd46 sensor"}
            ), 404
        return jsonify(payload)

    @blueprint.get("/api/devices/<int:device_id>/sensor-history")
    def device_sensor_history(device_id):
        config = device_config(device_id)
        if config is None:
            return jsonify({"error": "device not found or not a sensor"}), 404
        try:
            limit = max(1, min(int(request.args.get("limit", 100)), 10_000))
        except (TypeError, ValueError):
            limit = 100
        history = []
        for sample in repo.list_samples_for_device(device_id, limit=limit):
            metrics = _metrics_from_sample(sample)
            history.append(
                {
                    "ts_ms": sample.ts_ms,
                    "state": sample.state,
                    "temp_c": sample.temp_c,
                    "channels": _channels(metrics),
                }
            )
        return jsonify(
            {
                "device_id": device_id,
                "device_name": config.name,
                "device_alias": config.alias,
                "history": history,
            }
        )

    @blueprint.get("/api/serial-ports")
    def serial_ports():
        try:
            return jsonify({"ports": enum_serial_ports()})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @blueprint.get("/api/browse-directory")
    def browse_directory():
        path = request.args.get("path", "")
        try:
            if not path:
                if os.name == "nt":
                    drives = [
                        f"{drive}:\\"
                        for drive in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                        if os.path.exists(f"{drive}:\\")
                    ]
                    return jsonify(
                        {
                            "current": "",
                            "parent": "",
                            "directories": drives,
                        }
                    )
                return jsonify(
                    {
                        "current": "/",
                        "parent": "",
                        "directories": [
                            name
                            for name in os.listdir("/")
                            if os.path.isdir(os.path.join("/", name))
                        ],
                    }
                )
            if not os.path.isdir(path):
                return jsonify({"error": "invalid path"}), 400
            parent = os.path.dirname(path)
            if parent == path:
                parent = ""
            try:
                directories = sorted(
                    name
                    for name in os.listdir(path)
                    if os.path.isdir(os.path.join(path, name))
                )
            except PermissionError:
                directories = []
            return jsonify(
                {
                    "current": path,
                    "parent": parent,
                    "directories": directories,
                }
            )
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @blueprint.post("/api/devices/<int:device_id>/connect")
    def device_connect(device_id):
        config = device_config(device_id)
        if config is None:
            return jsonify(
                {"error": "device not found or not a whd46 sensor"}
            ), 404
        requested_port = str(
            (request.get_json(silent=True) or {}).get("port", "auto")
        )
        current = engine.latest().get(device_id)
        if (
            current is not None
            and current.state != "offline"
            and requested_port in ("auto", config.serial_port)
        ):
            return jsonify(
                {
                    "ok": True,
                    "port": config.serial_port,
                    "message": f"已连接到 {config.serial_port}",
                }
            )

        if requested_port == "auto":
            probe = auto_detect_port(
                slave=config.modbus_addr,
                baudrate=config.baudrate,
                parity=config.parity,
            )
        else:
            probe = probe_port(
                requested_port,
                slave=config.modbus_addr,
                baudrate=config.baudrate,
                parity=config.parity,
            )
        if not probe["ok"]:
            return jsonify(probe), 400
        if not hasattr(engine, "reconnect_device"):
            return jsonify(
                {"error": "engine does not support managed reconnect"}
            ), 501
        result = engine.reconnect_device(device_id, probe["port"])
        if not result.get("ok"):
            return jsonify(
                {
                    "ok": False,
                    "error": "连接失败",
                    "detail": result.get("error", "未知错误"),
                }
            ), 400
        return jsonify(
            {
                "ok": True,
                "port": probe["port"],
                "message": f"已连接到 {probe['port']}",
            }
        )

    @blueprint.post("/api/devices/<int:device_id>/disconnect")
    def device_disconnect(device_id):
        if device_config(device_id) is None:
            return jsonify(
                {"error": "device not found or not a whd46 sensor"}
            ), 404
        if not hasattr(engine, "disconnect_device"):
            return jsonify(
                {"error": "engine does not support managed disconnect"}
            ), 501
        result = engine.disconnect_device(device_id)
        if not result.get("ok"):
            return jsonify(result), 400
        return jsonify({"ok": True, "message": "已断开连接"})

    @blueprint.get("/api/devices/<int:device_id>/export-csv")
    def device_export_csv(device_id):
        if device_config(device_id) is None:
            return jsonify(
                {"error": "device not found or not a whd46 sensor"}
            ), 404
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "时间戳",
                "CH1温度(℃)",
                "CH1湿度(%RH)",
                "CH2温度(℃)",
                "CH2湿度(%RH)",
                "CH3温度(℃)",
                "CH3湿度(%RH)",
            ]
        )
        for sample in repo.list_samples_for_device(
            device_id,
            limit=100_000,
        ):
            channels = _channels(_metrics_from_sample(sample))
            row = [
                datetime.fromtimestamp(sample.ts_ms / 1000).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            ]
            by_channel = {item["channel"]: item for item in channels}
            for channel in range(1, 4):
                item = by_channel.get(channel, {})
                temp = item.get("temp_c")
                humid = item.get("humid_rh")
                row.append(f"{temp:.1f}" if temp is not None else "")
                row.append(f"{humid:.1f}" if humid is not None else "")
            writer.writerow(row)

        filename = f"WHD46_data_{datetime.now():%Y%m%d_%H%M%S}.csv"
        save_path = request.args.get("save_path", "")
        if save_path:
            try:
                os.makedirs(save_path, exist_ok=True)
                full_path = os.path.join(save_path, filename)
                with open(
                    full_path,
                    "w",
                    encoding="utf-8-sig",
                    newline="",
                ) as file:
                    file.write(output.getvalue())
                return jsonify(
                    {
                        "ok": True,
                        "message": f"CSV文件已保存到: {full_path}",
                        "filename": filename,
                        "path": full_path,
                    }
                )
            except Exception as exc:
                return jsonify(
                    {
                        "ok": False,
                        "error": f"保存文件失败: {exc}",
                    }
                ), 500
        return Response(
            "\ufeff" + output.getvalue(),
            mimetype="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename={filename}"
            },
        )

    @blueprint.post("/api/devices/<int:device_id>/log-csv")
    def device_log_csv(device_id):
        if device_config(device_id) is None:
            return jsonify(
                {"error": "device not found or not a whd46 sensor"}
            ), 404
        body = request.get_json(silent=True) or {}
        save_path = body.get("save_path", "data")
        data = body.get("data", {})
        os.makedirs(save_path, exist_ok=True)
        filename = f"WHD46_data_{datetime.now():%Y%m%d}.csv"
        full_path = os.path.join(save_path, filename)
        file_exists = os.path.exists(full_path)
        with open(
            full_path,
            "a",
            encoding="utf-8-sig",
            newline="",
        ) as file:
            writer = csv.writer(file)
            if not file_exists:
                writer.writerow(
                    [
                        "时间戳",
                        "CH1温度(℃)",
                        "CH1湿度(%RH)",
                        "CH2温度(℃)",
                        "CH2湿度(%RH)",
                        "CH3温度(℃)",
                        "CH3湿度(%RH)",
                    ]
                )
            timestamp_ms = data.get(
                "ts_ms",
                datetime.now().timestamp() * 1000,
            )
            row = [
                datetime.fromtimestamp(timestamp_ms / 1000).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            ]
            by_channel = {
                item.get("channel"): item
                for item in data.get("channels", [])
            }
            for channel in range(1, 4):
                item = by_channel.get(channel, {})
                temp = item.get("temp_c")
                humid = item.get("humid_rh")
                row.append(f"{temp:.1f}" if temp is not None else "")
                row.append(f"{humid:.1f}" if humid is not None else "")
            writer.writerow(row)
        return jsonify(
            {
                "ok": True,
                "filename": filename,
                "path": full_path,
            }
        )

    return blueprint
