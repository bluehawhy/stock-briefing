import json
from datetime import datetime, timedelta, timezone

import pytest

from app.commands import CommandNavigator, MENUS, split_commands
from app.services import BotService, InboundMessage


def message(text, channel="telegram", user="42", message_id=None):
    return InboundMessage(
        channel, user, text, user if channel == "telegram" else None, True, message_id
    )


def test_briefing_menu_is_not_execution_and_number_runs_selected_item(settings, store):
    service = BotService(settings, store)
    today = (
        datetime.now().astimezone(__import__("zoneinfo").ZoneInfo("Asia/Seoul")).date()
    )
    body = "전체 종목\n" + "삼성전자 / SK하이닉스\n" * 300
    store.save_briefing(today, body)
    reply = service.handle(message("브리핑"))
    assert "1. 오늘 브리핑" in reply and "2. 브리핑 갱신" in reply
    assert store.latest_job() is None
    assert service.handle(message("1")) == f"[저장된 브리핑: {today}]\n{body}"
    assert "없거나 만료" in service.handle(message("1"))
    assert service.handle(message("오늘 브리핑")).endswith(body)


@pytest.mark.parametrize("category", MENUS)
def test_all_categories_display_subitems(settings, store, category):
    reply = BotService(settings, store).handle(message(category))
    assert f"[{category}]" in reply
    for command in MENUS[category]:
        assert command in reply
    assert store.latest_job() is None


def test_help_main_navigation_and_argument_guidance(settings, store):
    service = BotService(settings, store)
    help_text = service.handle(message("도움말"))
    assert "메인 카테고리" in help_text and "1. 브리핑" in help_text
    assert "브리핑 갱신" in service.handle(message("선택 1"))
    service.handle(message("관심종목"))
    assert "종목명 또는 6자리" in service.handle(message("2번"))
    assert store.items("watchlist") == []
    assert "추가" in service.handle(message("관심종목 추가 005930 삼성전자"))


@pytest.mark.parametrize(
    "text",
    [
        "오늘 브리핑, 브리핑 갱신",
        "오늘 브리핑\n브리핑 갱신",
        "오늘 브리핑 그리고 브리핑 갱신",
        "오늘 브리핑 브리핑 갱신",
        "오늘 브리핑 / 브리핑 갱신",
    ],
)
def test_multiple_commands_ask_before_any_execution(settings, store, text):
    service = BotService(settings, store)
    result = service.handle(message(text))
    assert "명령어가 2개 입니다. 선택해주세요." in result
    assert store.latest_job() is None
    assert "갱신 요청 #1" in service.handle(message("2"))
    assert store.latest_job()[0] == 1


def test_three_commands_selection_does_not_run_others(settings, store):
    service = BotService(settings, store)
    result = service.handle(
        message("관심종목 추가 005930 삼성전자; 브리핑 갱신; 알림 끄기")
    )
    assert "명령어가 3개" in result
    assert not store.items("watchlist") and store.latest_job() is None
    assert "추가" in service.handle(message("1"))
    assert store.get("settings", "notifications") is None and store.latest_job() is None


def test_nested_names_json_and_unknown_text_not_interpreted_as_commands(
    settings, store, strategy
):
    text = "설정 변경 " + json.dumps(strategy.dict(), indent=2)
    assert split_commands(text) == [text]
    assert split_commands("관심종목 추가 005930 오늘 브리핑") == [
        "관심종목 추가 005930 오늘 브리핑"
    ]
    service = BotService(settings, store)
    reply = service.handle(message(text))
    assert "변경 후" in reply and "명령어가" not in reply
    reply = service.handle(message("브리핑 갱신, 알 수 없는 입력"))
    assert "지원하지 않는" in reply and store.latest_job() is None


def test_pending_choice_is_bound_to_user_channel_expiry_and_cancel(settings, store):
    service = BotService(settings, store)
    service.handle(message("브리핑"))
    assert "없거나 만료" in service.handle(message("2", "kakao", "k1"))
    assert service.handle(message("2", user="stranger")) is None
    assert "1~4" in service.handle(message("9"))
    service.handle(message("취소"))
    assert "없거나 만료" in service.handle(message("2"))
    service.handle(message("브리핑"))
    key = CommandNavigator.key(message(""))
    selection = store.get("command_choices", key)
    selection["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    store.put("command_choices", key, selection)
    assert "만료" in service.handle(message("2"))
    assert store.latest_job() is None


def test_pending_choice_survives_restart_and_duplicate_selection(settings, store):
    service = BotService(settings, store)
    service.handle(message("브리핑", message_id="a"))
    restarted = BotService(settings, store)
    first = restarted.handle(message("2", message_id="b"))
    second = restarted.handle(message("2", message_id="b"))
    assert first == second and "갱신 요청 #1" in first
    assert store.latest_job()[0] == 1


def test_kakao_full_briefing_is_paginated_without_loss(settings, store):
    from zoneinfo import ZoneInfo

    day = datetime.now(ZoneInfo("Asia/Seoul")).date()
    body = "가나다라마바사" * 500
    store.save_briefing(day, body)
    service = BotService(settings, store)
    reply = service.handle(message("오늘 브리핑", "kakao", "k1"))
    chunks = []
    for _ in range(10):
        assert len(reply) <= 1000
        chunks.append(reply.split("\n\n[")[0])
        if "조회 완료" in reply:
            break
        assert "다음" in reply
        reply = service.handle(message("다음", "kakao", "k1"))
    assert "".join(chunks) == f"[저장된 브리핑: {day}]\n{body}"
    assert "없거나 만료" in service.handle(message("다음", "kakao", "k1"))
    # Telegram receives the entire result for its existing multi-message transport.
    assert service.handle(message("오늘 브리핑")).endswith(body)


def test_parameter_commands_in_same_line_are_offered_as_choices(
    settings, store, strategy
):
    service = BotService(settings, store)
    result = service.handle(message("보유량 설정 005930 0 보유량 설정 000660 0"))
    assert "명령어가 2개" in result
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM confirmations").fetchone()[0] == 0
    assert "수동 보유량: 000660" in service.handle(message("2"))
    text = "설정 변경 " + json.dumps(strategy.dict()) + " 알림 끄기"
    result = service.handle(message(text))
    assert "명령어가 2개" in result
    assert store.get("settings", "strategy") is None


def test_new_explicit_command_replaces_old_menu(settings, store):
    service = BotService(settings, store)
    service.handle(message("브리핑"))
    service.handle(message("관심종목 보기"))
    assert "없거나 만료" in service.handle(message("2"))
    assert store.latest_job() is None


def test_name_then_code_dialogue_survives_restart(settings, store):
    service = BotService(settings, store)
    service.handle(message("관심종목"))
    assert "값만" in service.handle(message("2"))
    assert "종목코드만" in service.handle(message("삼성전자", message_id="name"))
    assert "종목코드만" in service.handle(message("삼성전자", message_id="name"))
    assert "6자리" in service.handle(message("5930"))
    assert not store.items("watchlist")
    reply = BotService(settings, store).handle(message("005930", message_id="code"))
    assert "삼성전자 (005930)" in reply
    assert store.get("watchlist", "005930") == {"name": "삼성전자"}
    assert (
        BotService(settings, store).handle(message("005930", message_id="code"))
        == reply
    )


def test_known_names_remove_and_readd(settings, store):
    service = BotService(settings, store)
    service.handle(message("관심종목 추가 005930 삼성전자"))
    service.handle(message("관심종목 제거"))
    assert "제거: 005930" in service.handle(message("삼성전자"))
    assert not store.items("watchlist")
    service.handle(message("관심종목 추가"))
    assert "삼성전자 (005930)" in service.handle(message("삼성전자"))


@pytest.mark.parametrize(
    "text", ["관심종목 이노스페이스 추가", "관심종목 추가 이노스페이스"]
)
def test_stock_name_request_followup(settings, store, text):
    service = BotService(settings, store)
    assert "종목코드만" in service.handle(message(text))
    assert not store.items("watchlist")
    assert "이노스페이스 (462350)" in service.handle(message("462350"))
    assert "제거: 462350" in service.handle(message("관심종목 이노스페이스 제거"))
    assert "이노스페이스 (462350)" in service.handle(message(text))


def test_name_first_request_overrides_input_and_multiple_commands(settings, store):
    service = BotService(settings, store)
    service.handle(message("보유량 설정"))
    assert "추가:" in service.handle(message("관심종목 462350 이노스페이스 추가"))
    assert store.get("command_input", "telegram:42:42") is None
    reply = service.handle(message("관심종목 이노스페이스 삭제, 브리핑 갱신"))
    assert "명령어가 2개" in reply
    assert store.get("watchlist", "462350") is not None
    assert store.latest_job() is None
    assert "제거: 462350" in service.handle(message("1"))


def test_input_numbers_holdings_and_validation(settings, store):
    service = BotService(settings, store)
    service.handle(message("관심종목 추가"))
    assert "추가:" in service.handle(message("005930 삼성전자"))
    service.handle(message("보유량 설정"))
    assert "수량만" in service.handle(message("005930"))
    assert "정수" in service.handle(message("-1"))
    reply = service.handle(message("0"))
    assert "확인" in reply
    assert not store.items("manual_holdings")


def test_input_expiry_cancel_override_and_channel(settings, store):
    service = BotService(settings, store)
    service.handle(message("관심종목 추가"))
    assert "지원하지 않는" in service.handle(
        message("삼성전자", channel="kakao", user="k1")
    )
    service.handle(message("취소"))
    assert store.get("command_input", "telegram:42:42") is None
    service.handle(message("관심종목 추가"))
    service.handle(message("메인"))
    assert store.get("command_input", "telegram:42:42") is None
    service.handle(message("관심종목 추가"))
    pending = store.get("command_input", "telegram:42:42")
    pending["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    store.put("command_input", "telegram:42:42", pending)
    assert "만료" in service.handle(message("005930"))
    service.handle(message("관심종목 추가"))
    assert "명령어가 2개" in service.handle(message("오늘 브리핑, 브리핑 갱신"))
    assert store.get("command_input", "telegram:42:42") is None
    assert not store.items("watchlist")


def test_json_input_and_duplicate_names(settings, store, strategy):
    service = BotService(settings, store)
    service.handle(message("설정 변경"))
    assert "확인" in service.handle(message(json.dumps(strategy.dict(), indent=2)))
    assert store.get("settings", "strategy") is None
    service.handle(message("관심종목 추가 005930 동일이름"))
    service.handle(message("관심종목 추가 000660 동일이름"))
    service.handle(message("관심종목 제거"))
    assert "중복" in service.handle(message("동일이름"))
    assert len(store.items("watchlist")) == 2
    assert "제거: 000660" in service.handle(message("000660"))
