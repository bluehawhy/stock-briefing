from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_dashboard_wireguard_listener_and_private_http_default():
    compose = (ROOT / "compose.yaml").read_text()
    caddy = (ROOT / "deploy" / "Caddyfile.example").read_text()
    env_example = (ROOT / ".env.example").read_text()

    assert 'DASHBOARD_ALLOW_HTTP_PRIVATE: "1"' in compose
    assert '"127.0.0.1:8001:8000"' in compose
    assert "DASHBOARD_ALLOW_HTTP_PRIVATE=1" in env_example
    assert ":8081 {" in caddy
    assert "path /stock-briefing /stock-briefing/*" in caddy
    assert "reverse_proxy 127.0.0.1:8001" in caddy
