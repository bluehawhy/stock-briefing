"""Channel-independent category menus and conservative multiple-command parsing."""

from __future__ import annotations

import re
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from functools import lru_cache

MENUS = {
    "브리핑": ("오늘 브리핑", "브리핑 갱신", "브리핑 이력", "제안 보기"),
    "관심종목": ("관심종목 보기", "관심종목 추가", "관심종목 제거"),
    "보유량": ("보유종목", "보유량 설정"),
    "계좌": ("추적 계좌 보기", "추적 계좌 추가", "추적 계좌 제거"),
    "증권사": (
        "증권사 연결 한국투자증권",
        "증권사 연결 미래에셋증권",
        "증권사 연결 해제 한국투자증권",
    ),
    "설정": ("설정 보기", "설정 도움말", "설정 변경", "주문 한도 변경"),
    "알림": ("알림 켜기", "알림 끄기"),
    "주문": (
        "주문 요청",
        "주문 상태",
        "주문 확인",
        "주문 정정",
        "주문 취소",
        "당일 주문 중지",
        "당일 주문 재개",
    ),
    "상태": ("서비스 상태", "연동 상태"),
    "모의 운용": ("모의 운용 기록",),
}
ALIASES = {
    "추적 계좌": "계좌",
    "보유": "보유량",
    "도움말": "메인",
    "메뉴": "메인",
    "메인": "메인",
    "/start": "메인",
    "/help": "메인",
    "/briefing": "브리핑",
}
USAGES = {
    "관심종목 추가": "관심종목 추가 005930 삼성전자",
    "관심종목 제거": "관심종목 제거 005930",
    "보유량 설정": "보유량 설정 005930 0",
    "추적 계좌 추가": "추적 계좌 추가 KIS-계좌ID",
    "추적 계좌 제거": "추적 계좌 제거 KIS-계좌ID",
    "설정 변경": "설정 변경 {JSON} (설정 도움말에서 필드 확인)",
    "주문 한도 변경": '주문 한도 변경 {"symbol_amount":10000,"daily_amount":20000,"price_deviation":0.01,"valid_seconds":120}',
    "주문 요청": "주문 요청 계좌ID 매수|매도 종목코드 수량 지정가격",
    "주문 확인": "주문 확인 확인코드",
    "주문 정정": "주문 정정 (현재 조회용 연결은 정정을 지원하지 않습니다.)",
    "주문 취소": "주문 취소 (현재 조회용 연결은 취소를 지원하지 않습니다.)",
}
INPUT_PROMPTS = {
    "관심종목 추가": "추가할 종목명 또는 6자리 코드를 보내세요.\n예: 삼성전자 / 005930 / 005930 삼성전자",
    "관심종목 제거": "제거할 종목명 또는 6자리 코드를 보내세요.",
    "보유량 설정": "종목코드와 수량을 보내세요.\n예: 005930 10 (코드만 보내면 수량을 이어서 물어봅니다.)",
    "추적 계좌 추가": "추가할 계좌 ID만 보내세요.",
    "추적 계좌 제거": "제거할 계좌 ID만 보내세요.",
    "설정 변경": "설정 JSON만 보내세요. 필드는 '설정 도움말'에서 확인할 수 있습니다.",
    "주문 한도 변경": "주문 한도 JSON만 보내세요.\n예: "
    + USAGES["주문 한도 변경"].removeprefix("주문 한도 변경 "),
    "주문 요청": "계좌ID 매수|매도 종목코드 수량 지정가격을 보내세요.",
    "주문 확인": "확인 코드만 보내세요.",
}

HELP = (
    "메인 카테고리를 선택해주세요.\n"
    + "\n".join(f"{i}. {name}" for i, name in enumerate(MENUS, 1))
    + "\n\n번호 또는 카테고리명을 보내세요.\n"
    "예: 브리핑 → 오늘 브리핑 / 브리핑 갱신 / 브리핑 이력 / 제안 보기\n"
    "'오늘 브리핑'처럼 전체 명령을 보내면 바로 실행합니다.\n"
    "여러 명령은 쉼표·줄바꿈·그리고로 구분하며, 하나를 선택한 뒤 실행합니다.\n"
    "항목 선택 후에는 필요한 값만 보내세요. 명령어를 다시 입력할 필요가 없습니다.\n"
    "예: 관심종목 → 2 → 삼성전자 → 안내에 따라 종목코드 입력\n"
    "'관심종목 이노스페이스 추가'처럼 종목명을 먼저 보내도 됩니다.\n"
    "메인: 처음 메뉴 / 취소: 선택·입력 종료\n"
    "설정·보유량·계좌 변경은 별도 확인 코드가 필요합니다.\n"
    "API 비밀 키는 대화에 보내지 마세요."
)
SIMPLE = (
    set(MENUS)
    | set(ALIASES)
    | {item for options in MENUS.values() for item in options}
    | {"/status", "확인", "취소", "다음"}
)
PARAMETER_PREFIXES = sorted(
    set(USAGES) | {"브리핑 이력", "제안 보기", "확인", "관심종목 삭제"},
    key=len,
    reverse=True,
)
ARGUMENT_COMMANDS = re.compile(
    r"(?:보유량 설정\s+[0-9]{6}\s+[0-9]+"
    r"|관심종목 (?:제거|삭제)\s+[0-9]{6}"
    r"|추적 계좌 (?:추가|제거)\s+\S+"
    r"|브리핑 이력\s+[0-9]{4}-[0-9]{2}-[0-9]{2}"
    r"|제안 보기\s+[0-9]{6}"
    r"|(?:주문 확인|확인)\s+[0-9a-f]{16}"
    r"|주문 요청\s+\S+\s+(?:매수|매도)\s+[0-9]{6}\s+[0-9]+\s+[0-9]+)(?=\s|$)"
)


def split_commands(text: str) -> list[str]:
    """Separate only complete known commands; never scan command names inside JSON/names."""
    text = text.strip()
    segments, current, depth, quoted, escaped = [], [], 0, False, False
    i = 0
    while i < len(text):
        char = text[i]
        if quoted:
            current.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            i += 1
            continue
        if char == '"':
            quoted = True
        elif char in "{[":
            depth += 1
        elif char in "}]":
            depth = max(0, depth - 1)
        separator = (
            re.match(r"\s+(?:그리고|및|하고|또는)\s+|\s+/\s+", text[i:])
            if depth == 0
            else None
        )
        if depth == 0 and (char in ",;\n" or separator):
            segments.append("".join(current).strip())
            current = []
            i += len(separator[0]) if separator else 1
            continue
        current.append(char)
        i += 1
    segments.append("".join(current).strip())
    segments = [s for s in segments if s]
    if len(segments) > 1:
        # Unknown text must not disappear while a recognized command gets executed.
        return segments if all(known_command(s) for s in segments) else [text]
    if text in SIMPLE:
        return [text]
    options = sorted(SIMPLE, key=len, reverse=True)

    @lru_cache(maxsize=64)
    def partition(rest, depth=0):
        if depth > 32:
            return None
        if rest in SIMPLE:
            return (rest,)
        match = ARGUMENT_COMMANDS.match(rest)
        if match:
            consumed = match.end()
            remaining = rest[consumed:].strip()
            tail = partition(remaining, depth + 1) if remaining else ()
            if tail is not None:
                return (rest[:consumed],) + tail
        for prefix in ("설정 변경 ", "주문 한도 변경 "):
            if rest.startswith(prefix):
                argument = rest[len(prefix) :].lstrip()
                try:
                    _, consumed = json.JSONDecoder().raw_decode(argument)
                except ValueError:
                    continue
                remaining = argument[consumed:].strip()
                tail = partition(remaining, depth + 1) if remaining else ()
                if tail is not None:
                    return (prefix + argument[:consumed],) + tail
        for option in options:
            if rest.startswith(option + " "):
                tail = partition(rest[len(option) :].strip(), depth + 1)
                if tail:
                    return (option,) + tail
        return None

    return list(partition(text) or (text,))


def stock_request(text: str):
    """Accept both action-first and name-first watchlist requests."""
    match = re.fullmatch(r"관심종목\s+(추가|제거|삭제)\s+(.+)", text)
    if match:
        action, argument = match.groups()
    else:
        match = re.fullmatch(r"관심종목\s+(.+?)\s+(추가|제거|삭제)", text)
        if not match:
            return None
        argument, action = match.groups()
    return "관심종목 " + ("제거" if action == "삭제" else action), argument.strip()


def known_command(text: str) -> bool:
    return (
        text in SIMPLE
        or any(text.startswith(prefix + " ") for prefix in PARAMETER_PREFIXES)
        or stock_request(text) is not None
    )


class CommandNavigator:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def key(message):
        # Platform, sender and conversation all delimit the pending choice.
        return (
            f"{message.channel}:{message.user_id}:{message.chat_id or message.user_id}"
        )

    def menu(self, message, title, options):
        self.store.put(
            "command_choices",
            self.key(message),
            {
                "options": list(options),
                "expires_at": (
                    datetime.now(timezone.utc) + timedelta(minutes=5)
                ).isoformat(),
            },
        )
        return (
            title
            + "\n"
            + "\n".join(f"{i}. {option}" for i, option in enumerate(options, 1))
            + "\n번호 또는 항목명을 보내세요. (5분 내, 취소 가능)"
        )

    def wait_for_input(self, message, command, **values):
        self.store.put(
            "command_input",
            self.key(message),
            {
                "command": command,
                "expires_at": (
                    datetime.now(timezone.utc) + timedelta(minutes=5)
                ).isoformat(),
                **values,
            },
        )

    def input_reply(self, message, pending, execute):
        text = message.text.strip()
        command = pending["command"]
        if pending.get("name"):
            if not re.fullmatch(r"[0-9]{6}", text):
                return "6자리 종목코드만 보내세요. (취소 가능)"
            arguments = text + " " + pending["name"]
        elif pending.get("symbol"):
            if not re.fullmatch(r"[0-9]+", text):
                return "수량을 0 이상의 정수로 보내세요. (취소 가능)"
            arguments = pending["symbol"] + " " + text
        elif command in {"관심종목 추가", "관심종목 제거"}:
            arguments = text
            if not re.match(r"^[0-9]{6}(?:\s|$)", text):
                if text.isdecimal():
                    return "종목코드는 6자리입니다. 다시 보내세요. (취소 가능)"
                if len(text) > 40:
                    return "종목명은 40자 이내로 보내세요. (취소 가능)"
                names = dict(self.store.items("symbol_names"))
                names.update(dict(self.store.items("watchlist")))
                matches = [
                    code for code, value in names.items() if value["name"] == text
                ]
                if len(matches) == 1:
                    arguments = matches[0] + (
                        " " + text if command.endswith("추가") else ""
                    )
                elif command.endswith("추가"):
                    self.wait_for_input(message, command, name=text)
                    return f"'{text}'의 코드를 확인할 수 없습니다. 6자리 종목코드만 보내세요. (5분 내, 취소 가능)"
                else:
                    return "제거할 종목을 찾을 수 없거나 이름이 중복됩니다. 6자리 코드만 보내세요."
        elif command == "보유량 설정" and re.fullmatch(r"[0-9]{6}", text):
            self.wait_for_input(message, command, symbol=text)
            return "보유 수량만 보내세요. (0 이상의 정수, 5분 내, 취소 가능)"
        else:
            arguments = text
        if not self.store.take("command_input", self.key(message), pending):
            return "입력이 이미 처리되었습니다. 항목을 다시 선택해주세요."
        try:
            return execute(replace(message, text=command + " " + arguments))
        except Exception:
            self.store.put("command_input", self.key(message), pending)
            raise

    def route(self, message, execute):
        text = message.text.strip()
        key = self.key(message)
        if text == "다음":
            pages = self.store.take("reply_pages", key)
            if (
                not pages
                or pages["expires_at"] <= datetime.now(timezone.utc).isoformat()
            ):
                return "이어서 볼 내용이 없거나 만료되었습니다. 원하는 항목을 다시 조회하세요."
            index = pages["next"]
            body = pages["chunks"][index]
            pages["next"] += 1
            if pages["next"] < len(pages["chunks"]):
                self.store.put("reply_pages", key, pages)
            return self.page_label(body, index, len(pages["chunks"]))
        self.store.delete("reply_pages", key)
        if text == "취소":
            self.store.delete("command_choices", key)
            self.store.delete("command_input", key)
            return (
                "선택·입력을 취소했습니다. '메인'으로 카테고리를 다시 볼 수 있습니다."
            )
        pending = self.store.get("command_input", key)
        if pending:
            if known_command(text) or len(split_commands(text)) > 1:
                self.store.delete("command_input", key)
            elif pending["expires_at"] <= datetime.now(timezone.utc).isoformat():
                self.store.delete("command_input", key)
                return "입력 시간이 만료되었습니다. 항목을 다시 선택해주세요."
            else:
                return self.input_reply(message, pending, execute)
        if re.fullmatch(r"(?:선택\s*)?[0-9]+번?", text):
            selection = self.store.get("command_choices", key)
            if (
                not selection
                or selection["expires_at"] <= datetime.now(timezone.utc).isoformat()
            ):
                self.store.delete("command_choices", key)
                return "선택할 메뉴가 없거나 만료되었습니다. '메인'을 입력하세요."
            index = int(re.search(r"\d+", text)[0]) - 1
            if not 0 <= index < len(selection["options"]):
                return f"1~{len(selection['options'])} 중 하나를 선택해주세요."
            # Consume atomically so two replies cannot execute the same pending selection.
            selected = self.store.take("command_choices", key, selection)
            if not selected:
                return "이미 선택한 메뉴입니다. '메인'으로 다시 시작하세요."
            text = selected["options"][index]
        else:
            self.store.delete("command_choices", key)
        commands = split_commands(text)
        if len(commands) > 1:
            return self.menu(
                message, f"명령어가 {len(commands)}개 입니다. 선택해주세요.", commands
            )
        request = stock_request(text)
        if request:
            command, argument = request
            self.wait_for_input(message, command)
            pending = self.store.get("command_input", key)
            return self.input_reply(replace(message, text=argument), pending, execute)
        canonical = ALIASES.get(text, text)
        if canonical == "메인":
            self.menu(message, "", tuple(MENUS))
            return HELP
        if canonical in MENUS:
            return self.menu(
                message, f"[{canonical}] 실행할 항목을 선택해주세요.", MENUS[canonical]
            )
        if text in INPUT_PROMPTS:
            self.wait_for_input(message, text)
            return (
                INPUT_PROMPTS[text] + "\n명령어 없이 값만 보내세요. (5분 내, 취소 가능)"
            )
        return execute(replace(message, text=text))

    @staticmethod
    def page_label(body, index, total):
        return (
            body
            + f"\n\n[{index + 1}/{total}]"
            + (
                " '다음'을 보내면 이어서 전체 내용을 볼 수 있습니다."
                if index + 1 < total
                else " 전체 내용 조회 완료."
            )
        )

    def paginate(self, message, text):
        if message.channel != "kakao" or len(text) <= 1000:
            return text
        chunks = [text[i : i + 900] for i in range(0, len(text), 900)]
        self.store.put(
            "reply_pages",
            self.key(message),
            {
                "chunks": chunks,
                "next": 1,
                "expires_at": (
                    datetime.now(timezone.utc) + timedelta(minutes=5)
                ).isoformat(),
            },
        )
        return self.page_label(chunks[0], 0, len(chunks))
