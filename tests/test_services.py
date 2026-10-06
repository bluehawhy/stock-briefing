from datetime import date

from app.config import Settings
from app.services import BotService, InboundMessage
from app.storage import Store


def test_channel_access_and_shared_briefing(tmp_path):
    store = Store(tmp_path / "stock-briefing.db")
    settings = Settings(tmp_path / "stock-briefing.db", "secret", frozenset({"k1"}), "token", frozenset({"42"}))
    service = BotService(settings, store)
    store.save_briefing(date(2026, 10, 7), "기준 거래일: 2026-10-06\n모의 브리핑")

    kakao = service.handle(InboundMessage("kakao", "k1", "오늘 브리핑"))
    telegram = service.handle(InboundMessage("telegram", "42", "오늘 브리핑", "42"))
    assert kakao == telegram
    assert "2026-10-06" in kakao
    assert store.telegram_chat("42") == "42"
    assert service.handle(InboundMessage("telegram", "other", "오늘 브리핑", "999")) is None
    assert store.telegram_chat("other") is None
    assert service.handle(InboundMessage("telegram", "42", "오늘 브리핑", "-10", False)) is None


def test_delivery_reservation_is_once_per_day(tmp_path):
    store = Store(tmp_path / "stock-briefing.db")
    day = date(2026, 10, 7)
    assert store.reserve_delivery(day)
    assert not store.reserve_delivery(day)
    store.finish_delivery(day, "review_required")
    assert not store.reserve_delivery(day)
    assert store.last_delivery() == (day.isoformat(), "review_required")


def test_poll_offset_survives_reopening(tmp_path):
    path = tmp_path / "stock-briefing.db"
    store = Store(path)
    assert store.poll_offset() == 0
    store.set_poll_offset(123)
    assert Store(path).poll_offset() == 123
