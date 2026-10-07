import json
import re
from datetime import datetime, timedelta, timezone

from app.services import BotService, InboundMessage


def msg(text, channel="telegram", user="42", message_id=None):
    return InboundMessage(channel, user, text, user, True, message_id)


def code(text):
    return re.search(r"(?:확인) ([0-9a-f]{16})", text)[1]


def test_watchlist_manual_holdings_and_validation(settings, store):
    service = BotService(settings, store)
    assert "추가" in service.handle(msg("관심종목 추가 005930 삼성전자"))
    assert "삼성전자" in service.handle(msg("관심종목 보기"))
    assert "숫자 6자리" in service.handle(msg("관심종목 추가 abc"))
    preview = service.handle(msg("보유량 설정 005930 0"))
    assert store.get("manual_holdings", "005930") is None
    assert "적용" in service.handle(msg("확인 " + code(preview)))
    assert store.get("manual_holdings", "005930")["quantity"] == 0
    assert "이미 사용" in service.handle(msg("확인 " + code(preview)))
    assert "제거" in service.handle(msg("관심종목 제거 005930"))
    assert not store.items("watchlist")


def test_configuration_confirmation_bound_to_channel_and_expiry(
    settings, store, strategy
):
    service = BotService(settings, store)
    preview = service.handle(msg("설정 변경 " + json.dumps(strategy.dict())))
    confirm_code = code(preview)
    assert "만료" in service.handle(msg("확인 " + confirm_code, "kakao", "k1"))
    assert store.get("settings", "strategy") is None
    assert "적용" in service.handle(msg("확인 " + confirm_code))
    assert store.get("settings", "strategy") == strategy.dict()
    preview = service.handle(msg("알림 끄기"))
    with store.connect() as db:
        db.execute(
            "UPDATE confirmations SET expires_at=? WHERE code=?",
            (
                (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
                code(preview),
            ),
        )
    assert "만료" in service.handle(msg("확인 " + code(preview)))
    assert store.get("settings", "notifications") is None


def test_duplicate_message_and_private_identity(settings, store):
    service = BotService(settings, store)
    first = service.handle(msg("보유량 설정 005930 5", message_id="123"))
    second = service.handle(msg("보유량 설정 005930 5", message_id="123"))
    assert first == second
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM confirmations").fetchone()[0] == 1
    assert service.handle(InboundMessage("telegram", "42", "도움말", "99")) is None
    assert service.handle(msg("관심종목 추가 005930", user="other")) is None
    assert not store.items("watchlist")


def test_refresh_queue_coalescing_and_lease(settings, store):
    service = BotService(settings, store)
    assert service.handle(msg("브리핑 갱신")) == service.handle(msg("브리핑 갱신"))
    assert store.claim_job() == 1
    assert store.claim_job() is None
    owner = store.acquire_lease("collection")
    assert owner and store.acquire_lease("collection") is None
    preview = service.handle(msg("보유량 설정 005930 1"))
    assert "갱신 중" in service.handle(msg("확인 " + code(preview)))
    store.release_lease("collection", owner)
    assert "적용" in service.handle(msg("확인 " + code(preview)))


def test_account_tracking_confirmation_and_masking(settings, store):
    store.put(
        "accounts", "a", {"number": "12345678", "product": "01", "tracked": False}
    )
    service = BotService(settings, store)
    assert "12345678" not in service.handle(msg("추적 계좌 보기"))
    preview = service.handle(msg("추적 계좌 추가 a"))
    assert not store.get("accounts", "a")["tracked"]
    service.handle(msg("확인 " + code(preview)))
    assert store.get("accounts", "a")["tracked"]
    preview = service.handle(msg("추적 계좌 제거 a"))
    service.handle(msg("확인 " + code(preview)))
    assert not store.get("accounts", "a")["tracked"]


def test_orders_remain_disabled_and_respect_stop_and_caps(settings, store):
    store.put("accounts", "a", {"number": "12345678", "product": "01", "tracked": True})
    store.put(
        "settings",
        "order_limits",
        {
            "symbol_amount": 10000,
            "daily_amount": 20000,
            "price_deviation": 0.01,
            "valid_seconds": 120,
        },
    )
    service = BotService(settings, store)
    assert "초과" in service.handle(msg("주문 요청 a 매수 005930 20 1000"))
    preview = service.handle(msg("주문 요청 a 매수 005930 1 1000"))
    assert "제출 불가" in preview and "미검증" in preview
    assert "호출하지 않았습니다" in service.handle(msg("주문 확인 " + code(preview)))
    stop = service.handle(msg("당일 주문 중지"))
    service.handle(msg("확인 " + code(stop)))
    assert "중지 상태" in service.handle(msg("주문 요청 a 매수 005930 1 1000"))
    assert "지원하지 않습니다" in service.handle(msg("주문 취소 123"))


def test_disconnect_clears_secrets_and_stops_tracking(settings, store):
    store.put("credentials", "kis", {"encrypted": "fake-cipher"})
    store.put("credentials", "kis-token", {"encrypted": "fake-token"})
    store.put("accounts", "a", {"number": "12345678", "product": "01", "tracked": True})
    store.put("recommendations", "old", {"history": "retained"})
    service = BotService(settings, store)
    preview = service.handle(msg("증권사 연결 해제 한국투자증권"))
    service.handle(msg("확인 " + code(preview)))
    assert not store.items("credentials")
    assert not store.get("accounts", "a")["tracked"]
    assert store.get("recommendations", "old")
