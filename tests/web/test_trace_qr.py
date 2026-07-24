from lab_device_manager.web.trace_labels import trace_qr_payload


def test_trace_qr_payload_is_an_actionable_https_url():
    payload = trace_qr_payload(
        "https://lab.puricore.example/",
        "20260724-CEM-01-IP01-01",
    )

    assert payload == (
        "https://lab.puricore.example/scan/"
        "20260724-CEM-01-IP01-01"
    )
