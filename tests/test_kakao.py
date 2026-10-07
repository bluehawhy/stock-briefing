from fastapi.testclient import TestClient

from app.main import app


def test_kakao_skill_auth_and_response(monkeypatch, tmp_path):
    monkeypatch.setenv("KAKAO_SKILL_SECRET", "long-secret")
    monkeypatch.setenv("KAKAO_ALLOWED_USER_IDS", "k1")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "stock-briefing.db"))
    payload = {"userRequest": {"user": {"id": "k1"}, "utterance": "도움말"}}
    client = TestClient(app)
    assert client.post("/kakao/skill/wrong", json=payload).status_code == 404
    response = client.post("/kakao/skill/long-secret", json=payload)
    assert response.status_code == 200
    assert (
        "오늘 브리핑" in response.json()["template"]["outputs"][0]["simpleText"]["text"]
    )
    payload["userRequest"]["user"]["id"] = "stranger"
    response = client.post("/kakao/skill/long-secret", json=payload)
    assert "권한" in response.json()["template"]["outputs"][0]["simpleText"]["text"]
