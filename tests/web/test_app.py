import json
import time
from io import BytesIO

from openpyxl import load_workbook
from lab_device_manager.web.app import create_app
from lab_device_manager.runtime.types import DeviceConfig
from lab_device_manager.db.repository import Repository
from lab_device_manager.instruments.base import StatusSnapshot


def _sample_to_dict(s):
    return {"id": s.id, "run_id": s.run_id, "device_id": s.device_id,
            "ts_ms": s.ts_ms, "state": s.state, "flow_rate": s.flow_rate,
            "delivered_volume": s.delivered_volume, "temp_c": s.temp_c,
            "metrics": json.loads(s.metrics_json) if s.metrics_json else {}}


def _event_to_dict(e):
    return {"id": e.id, "device_id": e.device_id, "run_id": e.run_id,
            "ts_ms": e.ts_ms, "event_type": e.event_type, "severity": e.severity,
            "detail": json.loads(e.detail_json) if e.detail_json else {}}


class StaticEngine:
    def __init__(self, latest, dmap):
        self._latest = latest; self._dmap = dmap
    def latest(self): return dict(self._latest)
    def device_map(self): return dict(self._dmap)


def _snap(state):
    return StatusSnapshot(timestamp=time.time(), state=state, work_mode="仅注入",
                          device_id="d", acc_volume=12.5, acc_unit="mL", temp_c=40.0,
                          metrics={"syringe_code": 9, "target_volume": 50.0,
                                   "target_unit": "mL", "inject_rate": 5.0,
                                   "pause_delay_ms": 5000, "repeat_count": 3, "force": 100})


def _app(tmp_path, login_password: str = ""):
    repo = Repository(":memory:")
    did = repo.upsert_device("pump-1", "tyd02", alias="注射泵")
    eng = StaticEngine({did: _snap("running")}, {did: DeviceConfig(name="pump-1", type="tyd02", alias="注射泵")})
    return create_app(eng, repo, secret_key="test-secret", login_password=login_password), repo, did


def test_status_lists_devices(tmp_path):
    app, repo, did = _app(tmp_path)
    r = app.test_client().get("/api/status")
    data = r.get_json()
    assert data["devices"][0]["name"] == "pump-1"
    assert data["devices"][0]["latest"]["state"] == "running"


def test_runs_and_tag(tmp_path):
    app, repo, did = _app(tmp_path)
    rid = repo.open_run(did, 1, 1751000000_000, {})
    repo.close_run(rid, 1751000060_000, "completed", 12.5, "mL", 12.5, "mL", 0)
    untagged = app.test_client().get("/api/runs/untagged").get_json()
    assert len(untagged) == 1 and untagged[0]["tagged"] is False
    app.test_client().post(f"/api/runs/{rid}/tag", json={"operator": "alice", "project_tag": "陶瓷膜-A"})
    assert app.test_client().get("/api/runs/untagged").get_json() == []


def test_runs_csv_export(tmp_path):
    app, repo, did = _app(tmp_path)
    rid = repo.open_run(did, 1, 1751000000_000, {})
    repo.close_run(rid, 1751000060_000, "completed", 12.5, "mL", 12.5, "mL", 0)
    r = app.test_client().get("/api/runs/export.csv")
    assert r.status_code == 200
    assert "text/csv" in r.content_type
    assert r.headers.get("Content-Disposition") == "attachment; filename=pump_runs.csv"
    body = r.data.decode("utf-8")
    assert "end_status" in body and "completed" in body


def test_runs_include_setpoints_and_actual(tmp_path):
    app, repo, did = _app(tmp_path)
    rid = repo.open_run(did, 1, 1751000000_000, {"syringe_code": 9, "target_volume": 50.0})
    repo.close_run(rid, 1751000060_000, "completed", 50.0, "mL", 150.0, "mL", 0)
    r = app.test_client().get("/api/runs").get_json()[0]
    assert r["actual_volume"] == 50.0
    assert r["setpoints"]["syringe_code"] == 9


def test_csv_export_neutralizes_formula_injection(tmp_path):
    """CSV injection (OWASP): user-entered fields starting with = + - @ must be
    prefixed so spreadsheet apps don't evaluate them as formulas."""
    app, repo, did = _app(tmp_path)
    rid = repo.open_run(did, 1, 1751000000_000, {})
    repo.close_run(rid, 1751000060_000, "completed", 12.5, "mL", 12.5, "mL", 0)
    repo.tag_run(rid, "=cmd|'/c calc'!A1", "+2+2", "-3", "@sum(A1)")
    body = app.test_client().get("/api/runs/export.csv").data.decode("utf-8")
    assert "'=cmd" in body      # = neutralized
    assert "'+2+2" in body      # + neutralized
    assert "'-3" in body        # - neutralized
    assert "'@sum" in body      # @ neutralized


def test_device_page_serves_html(tmp_path):
    app, repo, did = _app(tmp_path)
    r = app.test_client().get("/device/" + str(did))
    assert r.status_code == 200
    assert b"device.js" in r.data


def test_device_detail_includes_metrics_in_latest(tmp_path):
    """_snap_to_dict must expose metrics so the dashboard config line + detail
    page can render mode/syringe/target without a second round-trip."""
    app, repo, did = _app(tmp_path)
    data = app.test_client().get("/api/devices/" + str(did)).get_json()
    assert data["device"]["name"] == "pump-1"
    assert data["latest"]["metrics"]["syringe_code"] == 9
    assert data["latest"]["metrics"]["target_volume"] == 50.0
    assert data["metrics"]["syringe_code"] == 9


def test_whd_realtime_data_reads_latest_snapshot_without_polling_or_writing():
    repo = Repository(":memory:")
    device_id = repo.upsert_device("whd-1", "whd46", alias="环境温湿度")
    snapshot = StatusSnapshot(
        timestamp=time.time(),
        state="running",
        work_mode="monitoring",
        device_id="whd",
        temp_c=25.0,
        metrics={
            "channels": [
                {"temp": 24.0, "humid": 40.0},
                {"temp": 25.0, "humid": 41.0},
                {"temp": 26.0, "humid": 42.0},
            ],
            "avg_humid_rh": 41.0,
        },
    )

    class PollingMustNotRun:
        is_connected = True

        def read_channels(self):
            raise AssertionError("GET realtime-data must not poll the serial adapter")

    class WHDEngine(StaticEngine):
        def _get_adapter(self, requested_id):
            assert requested_id == device_id
            return PollingMustNotRun()

    engine = WHDEngine(
        {device_id: snapshot},
        {
            device_id: DeviceConfig(
                name="whd-1", type="whd46", alias="环境温湿度"
            )
        },
    )
    app = create_app(engine, repo, secret_key="test-secret")

    response = app.test_client().get(
        f"/api/devices/{device_id}/realtime-data"
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["channels"][0] == {
        "channel": 1,
        "temp_c": 24.0,
        "humid_rh": 40.0,
    }
    assert repo.list_samples_for_device(device_id) == []


def test_whd_connect_hands_verified_port_to_managed_engine(monkeypatch):
    repo = Repository(":memory:")
    device_id = repo.upsert_device(
        "whd-1",
        "whd46",
        alias="环境温湿度",
    )

    class ManagedEngine(StaticEngine):
        def __init__(self):
            super().__init__(
                {},
                {
                    device_id: DeviceConfig(
                        name="whd-1",
                        type="whd46",
                        alias="环境温湿度",
                        serial_port="/dev/old",
                        parity="NONE",
                    )
                },
            )
            self.reconnected = None

        def reconnect_device(self, requested_id, port):
            self.reconnected = (requested_id, port)
            return {"ok": True, "port": port}

    engine = ManagedEngine()
    monkeypatch.setattr(
        "lab_device_manager.web.whd46.probe_port",
        lambda *args, **kwargs: {
            "ok": True,
            "port": "/dev/new",
            "error": "",
            "detail": "",
        },
    )
    app = create_app(engine, repo, secret_key="test-secret")

    response = app.test_client().post(
        f"/api/devices/{device_id}/connect",
        json={"port": "/dev/new"},
    )

    assert response.status_code == 200
    assert response.get_json()["port"] == "/dev/new"
    assert engine.reconnected == (device_id, "/dev/new")


def test_device_detail_filters_runs_by_device_id(tmp_path):
    """SECURITY: /api/devices/<id> must only return that device's runs — never
    another device's. (Regression: previously returned repo.list_runs for ALL
    devices.)"""
    app, repo, did = _app(tmp_path)
    other = repo.upsert_device("pump-2", "tyd02")
    r1 = repo.open_run(did, 1, 1751000000_000, {})
    r2 = repo.open_run(other, 1, 1751000100_000, {})
    repo.close_run(r1, 1751000060_000, "completed", 12.5, "mL", 12.5, "mL", 0)
    repo.close_run(r2, 1751000160_000, "completed", 99.0, "mL", 99.0, "mL", 0)
    data = app.test_client().get("/api/devices/" + str(did)).get_json()
    run_ids = [r["id"] for r in data["runs"]]
    assert run_ids == [r1]              # only this device's run
    assert all(r["device_id"] == did for r in data["runs"])


def test_runs_filter_by_project_tag(tmp_path):
    app, repo, did = _app(tmp_path)
    rid = repo.open_run(did, 1, 1751000000_000, {})
    repo.close_run(rid, 1751000060_000, "completed", 12.5, "mL", 12.5, "mL", 0)
    repo.tag_run(rid, "alice", "陶瓷膜-A", "", "")
    data = app.test_client().get("/api/runs?project_tag=陶瓷膜-A").get_json()
    assert len(data) == 1 and data[0]["project_tag"] == "陶瓷膜-A"
    data = app.test_client().get("/api/runs?project_tag=不存在").get_json()
    assert data == []


def test_run_detail_includes_samples_and_events(tmp_path):
    app, repo, did = _app(tmp_path)
    rid = repo.open_run(did, 1, 1751000000_000, {})
    repo.add_sample(rid, did, 1751000001_000, "running", 1.2, 1.0, 33.0, "{}")
    repo.add_event(did, rid, 1751000000_000, "start", "info", "{}")
    repo.close_run(rid, 1751000060_000, "completed", 12.5, "mL", 12.5, "mL", 0)
    data = app.test_client().get(f"/api/runs/{rid}").get_json()
    assert data["run"]["id"] == rid
    assert len(data["samples"]) == 1
    assert data["samples"][0]["flow_rate"] == 1.2
    assert len(data["events"]) == 1
    assert data["events"][0]["event_type"] == "start"


def test_run_detail_404_for_missing_run(tmp_path):
    app, repo, did = _app(tmp_path)
    r = app.test_client().get("/api/runs/99999")
    assert r.status_code == 404


def test_runs_export_xlsx(tmp_path):
    app, repo, did = _app(tmp_path)
    rid = repo.open_run(did, 1, 1751000000_000,
                       {"syringe_code": 9, "target_volume": 50.0})
    repo.add_sample(rid, did, 1751000001_000, "running", 1.2, 1.0, 33.0, "{}")
    repo.close_run(rid, 1751000060_000, "completed", 12.5, "mL", 12.5, "mL", 0)
    r = app.test_client().get("/api/runs/export.xlsx")
    assert r.status_code == 200
    assert r.headers.get("Content-Disposition") == "attachment; filename=pump_runs.xlsx"
    wb = load_workbook(BytesIO(r.data))
    assert "runs" in wb.sheetnames and "samples" in wb.sheetnames
    ws = wb["runs"]
    assert ws.cell(row=1, column=1).value == "id"
    assert any(ws.cell(row=row, column=1).value == rid for row in range(2, ws.max_row + 1))


def test_run_samples_endpoint(tmp_path):
    app, repo, did = _app(tmp_path)
    rid = repo.open_run(did, 1, 1751000000_000, {})
    repo.add_sample(rid, did, 1751000001_000, "running", 1.2, 1.0, 33.0, "{}")
    repo.close_run(rid, 1751000060_000, "completed", 12.5, "mL", 12.5, "mL", 0)
    data = app.test_client().get(f"/api/runs/{rid}/samples").get_json()
    assert len(data) == 1
    assert data[0]["flow_rate"] == 1.2
    assert app.test_client().get("/api/runs/99999/samples").status_code == 404


def test_run_events_endpoint(tmp_path):
    app, repo, did = _app(tmp_path)
    rid = repo.open_run(did, 1, 1751000000_000, {})
    repo.add_event(did, rid, 1751000000_000, "start", "info", "{}")
    repo.close_run(rid, 1751000060_000, "completed", 12.5, "mL", 12.5, "mL", 0)
    data = app.test_client().get(f"/api/runs/{rid}/events").get_json()
    assert len(data) == 1
    assert data[0]["event_type"] == "start"
    assert app.test_client().get("/api/runs/99999/events").status_code == 404


def test_runs_query_params_validated(tmp_path):
    app, repo, did = _app(tmp_path)
    client = app.test_client()
    assert client.get("/api/runs?limit=abc").status_code == 400
    assert client.get("/api/runs?offset=-1").status_code == 400
    assert client.get("/api/runs?device_id=abc").status_code == 400
    assert client.get("/api/runs?start_ms=abc").status_code == 400
    assert client.get("/api/runs?limit=1001").status_code == 400
    # valid params still work
    assert client.get("/api/runs?limit=10&offset=0").status_code == 200


def test_run_report_pdf(tmp_path):
    app, repo, did = _app(tmp_path)
    rid = repo.open_run(did, 1, 1751000000_000, {"target_volume": 50.0})
    repo.add_sample(rid, did, 1751000001_000, "running", 1.2, 1.0, 33.0, "{}")
    repo.add_event(did, rid, 1751000000_000, "start", "info", "{}")
    repo.close_run(rid, 1751000060_000, "completed", 12.5, "mL", 12.5, "mL", 0)
    r = app.test_client().get(f"/api/runs/{rid}/report.pdf")
    assert r.status_code == 200
    assert r.content_type == "application/pdf"
    assert r.data[:4] == b"%PDF"


def test_run_page_serves_html(tmp_path):
    app, repo, did = _app(tmp_path)
    rid = repo.open_run(did, 1, 1751000000_000, {})
    r = app.test_client().get(f"/run/{rid}")
    assert r.status_code == 200
    assert b"run.js" in r.data


def test_shared_premium_theme_is_served_by_every_operator_page(tmp_path):
    app, repo, did = _app(tmp_path)
    client = app.test_client()

    theme = client.get("/static/puricore-theme.css")
    assert theme.status_code == 200
    assert b"--pc-accent" in theme.data
    assert b"prefers-reduced-motion" in theme.data

    for page in (
        "index.html",
        "device.html",
        "run.html",
        "sensor.html",
        "login.html",
        "experiment.html",
    ):
        response = client.get(f"/static/{page}")
        assert response.status_code == 200
        assert b"/static/puricore-theme.css" in response.data


def test_login_page_serves_identity_mode_when_password_is_disabled(tmp_path):
    app, repo, did = _app(tmp_path, login_password="")
    client = app.test_client()

    page = client.get("/login")
    mode = client.get("/api/auth-mode")

    assert page.status_code == 200
    assert b"\xe8\xb4\xa6\xe5\x8f\xb7" in page.data  # "账号"
    assert mode.status_code == 200
    assert mode.get_json() == {"password_required": False}


def test_identity_mode_login_sets_named_operator_without_password(tmp_path):
    app, repo, did = _app(tmp_path, login_password="")
    client = app.test_client()

    response = client.post("/login", json={"username": "王小明"})

    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "operator": "王小明"}
    assert client.get("/api/session").get_json() == {
        "authenticated": True,
        "operator": "王小明",
        "password_required": False,
    }


def test_index_requires_login_when_enabled(tmp_path):
    app, repo, did = _app(tmp_path, login_password="secret")
    r = app.test_client().get("/")
    assert r.status_code == 302
    assert r.headers["Location"] == "/login"


def test_device_page_requires_login_when_enabled(tmp_path):
    app, repo, did = _app(tmp_path, login_password="secret")
    r = app.test_client().get(f"/device/{did}")
    assert r.status_code == 302
    assert r.headers["Location"] == "/login"


def test_run_page_requires_login_when_enabled(tmp_path):
    app, repo, did = _app(tmp_path, login_password="secret")
    rid = repo.open_run(did, 1, 1751000000_000, {})
    r = app.test_client().get(f"/run/{rid}")
    assert r.status_code == 302
    assert r.headers["Location"] == "/login"


def test_login_page_served_when_enabled(tmp_path):
    app, repo, did = _app(tmp_path, login_password="secret")
    client = app.test_client()
    r = client.get("/login")
    assert r.status_code == 200
    assert b"\xe5\xaf\x86\xe7\xa0\x81" in r.data  # "密码"
    assert client.get("/api/auth-mode").get_json() == {
        "password_required": True
    }


def test_login_with_valid_password_creates_session(tmp_path):
    app, repo, did = _app(tmp_path, login_password="secret")
    client = app.test_client()
    r = client.post(
        "/login",
        json={"username": "王小明", "password": "secret"},
    )
    assert r.status_code == 200
    assert r.get_json()["ok"] is True
    with client.session_transaction() as sess:
        assert sess.get("logged_in") is True


def test_login_session_carries_operator_name(tmp_path):
    app, repo, did = _app(tmp_path, login_password="secret")
    client = app.test_client()

    r = client.post(
        "/login",
        json={"username": "王小明", "password": "secret"},
    )

    assert r.status_code == 200
    assert client.get("/api/session").get_json()["operator"] == "王小明"
    with client.session_transaction() as sess:
        assert sess.get("username") == "王小明"


def test_login_with_invalid_password_rejected(tmp_path):
    app, repo, did = _app(tmp_path, login_password="secret")
    client = app.test_client()
    r = client.post(
        "/login",
        json={"username": "王小明", "password": "wrong"},
    )
    assert r.status_code == 401
    assert r.get_json()["ok"] is False
    with client.session_transaction() as sess:
        assert sess.get("logged_in") is None


def test_login_rejects_missing_operator_name(tmp_path):
    app, repo, did = _app(tmp_path, login_password="secret")
    client = app.test_client()

    r = client.post("/login", json={"username": " ", "password": "secret"})

    assert r.status_code == 400
    assert r.get_json()["error"] == "operator name is required"
    with client.session_transaction() as sess:
        assert sess.get("logged_in") is None


def test_authenticated_user_can_access_protected_routes(tmp_path):
    app, repo, did = _app(tmp_path, login_password="secret")
    client = app.test_client()
    client.post(
        "/login",
        json={"username": "王小明", "password": "secret"},
    )
    r = client.get("/")
    assert r.status_code == 200
    r = client.get(f"/device/{did}")
    assert r.status_code == 200
    r = client.get("/api/status")
    assert r.status_code == 200
    assert r.get_json()["devices"][0]["name"] == "pump-1"


def test_logout_clears_session(tmp_path):
    app, repo, did = _app(tmp_path, login_password="secret")
    client = app.test_client()
    client.post(
        "/login",
        json={"username": "王小明", "password": "secret"},
    )
    r = client.post("/logout")
    assert r.status_code == 302
    assert r.headers["Location"] == "/login"
    with client.session_transaction() as sess:
        assert "logged_in" not in sess


def test_api_routes_require_login_when_enabled(tmp_path):
    """When login_password is set, API routes must return 401 if not authenticated."""
    app, repo, did = _app(tmp_path, login_password="secret")
    r = app.test_client().get("/api/status")
    assert r.status_code == 401
    assert r.get_json()["error"] == "unauthorized"
