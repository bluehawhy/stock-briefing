from fastapi.testclient import TestClient

from app.main import app


def test_dashboard_uses_stock_briefing_path_and_private_wireguard_http(monkeypatch, tmp_path):
    password = "a-long-random-dashboard-password-123"
    monkeypatch.setenv("DASHBOARD_PASSWORD", password)
    monkeypatch.setenv("DASHBOARD_ALLOW_HTTP_PRIVATE", "1")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "dashboard.db"))

    client = TestClient(app, base_url="http://10.8.0.2", auth=("admin", password))
    page = client.get("/stock-briefing/")

    assert page.status_code == 200
    assert "/stock-briefing/assets/dashboard.css" in page.text
    assert "/stock-briefing/assets/dashboard.js" in page.text
    assert page.headers["cache-control"] == "no-store"
    assert client.get("/stock-briefing/assets/dashboard.js").status_code == 200
    assert client.get("/stock-briefing/api/overview").status_code == 200
    assert client.get("/dashboard/").status_code == 404


def test_dashboard_private_http_requires_opt_in(monkeypatch):
    password = "a-long-random-dashboard-password-123"
    monkeypatch.setenv("DASHBOARD_PASSWORD", password)
    monkeypatch.delenv("DASHBOARD_ALLOW_HTTP_PRIVATE", raising=False)

    client = TestClient(app, base_url="http://10.8.0.2", auth=("admin", password))

    assert client.get("/stock-briefing/").status_code == 403
