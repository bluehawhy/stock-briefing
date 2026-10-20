"""Channel-independent category menus and conservative multiple-command parsing."""

from __future__ import annotations

import re
from uuid import uuid4
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from app.models import DataError

MENUS = {
    "브리핑": ("오늘 브리핑", "브리핑 이력", "제안 보기", "종목별 제안 선택"),
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
    "상태": ("서비스 상태", "연동 상태", "버전", "로그 보기"),
    "모의 운용": ("모의 운용 기록",),
    "빠른 메뉴": ("오늘 브리핑", "관심종목 보기", "보유종목", "종목별 제안 선택",
                  "서비스 상태", "연동 상태", "버전"),
}
ALIASES = {
    "관리": "상태",
    "추적 계좌": "계좌",
    "보유": "보유량",
    "도움말": "메인",
    "메뉴": "메인",
    "메인": "메인",
    "/start": "메인",
    "/help": "메인",
    "/briefing": "브리핑",
    "바로가기": "빠른 메뉴",
    "/quick": "빠른 메뉴",
}
USAGES = {
    "로그 보기": "로그 보기 / 로그 보기 10분 / 로그 보기 09:05",
    "지금 로그 보기": "지금 로그 보기 (최근 3분)",
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
    "메인 카테고리를 선택해주세요. (텔레그램은 버튼 터치 가능)\n"
    + "\n".join(f"{i}. {name}" for i, name in enumerate(MENUS, 1))
    + "\n\n번호 또는 카테고리명을 보내세요.\n"
    "예: 브리핑 → 오늘 브리핑 / 브리핑 이력 / 제안 보기\n"
    "'오늘 브리핑'은 최신 데이터를 확인해 새 브리핑을 생성합니다.\n"
    "텔레그램에서는 매수·매도·관망 개수 버튼으로 해당 종목 전체 내용을 볼 수 있습니다.\n"
    "명령 단어 순서는 자유롭습니다: 브리핑 오늘 / 추가 관심종목 에스엔시스.\n"
    "영문·숫자 6자리 종목코드도 지원합니다. 겹치는 명령은 선택 후 실행합니다.\n"
    "여러 명령은 쉼표·줄바꿈·그리고로 구분하며, 하나를 선택한 뒤 실행합니다.\n"
    "항목 선택 후에는 필요한 값만 보내세요. 명령어를 다시 입력할 필요가 없습니다.\n"
    "예: 관심종목 → 2 → 삼성전자 → 종목코드 자동 조회\n"
    "'관심종목 이노스페이스 추가'처럼 종목명을 먼저 보내도 됩니다.\n"
    "종목코드는 API 연결 시 종목정보 확인, 미연결·실패 시 공식 공개 목록으로 조회합니다.\n"
    "로그 보기: 최근 3분 / 로그 보기 10분: 최근 10분 / 로그 보기 09:05: 오늘 그 1분 (한국 시간)\n"
    "메인: 처음 메뉴 / 취소: 선택·입력 종료\n"
    "빠른 메뉴 또는 바로가기: 자주 쓰는 조회 기능 / 종목별 제안 선택: 종목을 번호로 선택\n"
    "설정·보유량·계좌 변경은 별도 확인 코드가 필요합니다.\n"
    "API 비밀 키는 대화에 보내지 마세요."
)
SIMPLE = (
    set(MENUS)
    | set(ALIASES)
    | {item for options in MENUS.values() for item in options}
    | {"/status", "/version", "확인", "취소", "다음", "지금 로그 보기"}
)
PARAMETER_PREFIXES = sorted(
    set(USAGES) | {"브리핑 이력", "브리핑 분류", "제안 보기", "확인", "관심종목 삭제"},
    key=len,
    reverse=True,
)
ARGUMENT_COMMANDS = re.compile(
    r"(?:보유량 설정\s+[A-Za-z0-9]{6}\s+[0-9]+"
    r"|관심종목 (?:제거|삭제)\s+[A-Za-z0-9]{6}"
    r"|추적 계좌 (?:추가|제거)\s+\S+"
    r"|브리핑 이력\s+[0-9]{4}-[0-9]{2}-[0-9]{2}"
    r"|(?:지금 )?로그 보기\s+(?:[0-9]+\s*분|[0-9]{2}:[0-9]{2})"
    r"|제안 보기\s+[A-Za-z0-9]{6}"
    r"|(?:주문 확인|확인)\s+[0-9a-f]{16}"
    r"|주문 요청\s+\S+\s+(?:매수|매도)\s+[A-Za-z0-9]{6}\s+[0-9]+\s+[0-9]+)(?=\s|$)"
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
            re.match(r"\s+(?:그리고|및|하고|또는)\s+|\s+/+\s+", text[i:])
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


def command_candidates(text: str) -> list[str]:
    """Match command words in any order, keeping argument text intact."""
    # JSON commands retain their exact prefix and payload parsing.
    if "{" in text or "[" in text or text.startswith("/"):
        return [text] if text in SIMPLE or any(
            text.startswith(prefix + " ") for prefix in PARAMETER_PREFIXES
        ) else []
    words = re.findall(r"\S+", text)
    hits = []
    for command in sorted({item for menu in MENUS.values() for item in menu}
                          | set(INPUT_PROMPTS) | {"확인", "지금 로그 보기", "관심종목 삭제"}):
        required = command.split()
        remaining = list(words)
        for word in required:
            if word not in remaining:
                break
            remaining.remove(word)
        else:
            hits.append(command)
    # A longer command absorbs its own shorter prefix, not another action.
    hits = [command for command in hits if not any(
        set(command.split()) < set(other.split()) for other in hits
    )]
    matched_words = {word for command in hits for word in command.split()}
    argument = " ".join(word for word in words if word not in matched_words)
    candidates = []
    for command in hits:
        canonical = "관심종목 제거" if command == "관심종목 삭제" else command
        if argument and canonical not in PARAMETER_PREFIXES:
            continue
        candidates.append(canonical + (" " + argument if argument else ""))
    return list(dict.fromkeys(candidates))


def stock_request(text: str):
    match = re.fullmatch(r"관심종목\s+(추가|제거|삭제)\s+(.+)", text)
    if match:
        action, argument = match.groups()
    else:
        candidates = [candidate for candidate in command_candidates(text)
                      if candidate.startswith(("관심종목 추가 ", "관심종목 제거 "))]
        if len(candidates) != 1:
            return None
        _, action, argument = candidates[0].split(maxsplit=2)
    return "관심종목 " + ("제거" if action == "삭제" else action), argument.strip()


def known_command(text: str) -> bool:
    return (
        bool(command_candidates(text))
        or text in SIMPLE
        or any(text.startswith(prefix + " ") for prefix in PARAMETER_PREFIXES)
        or stock_request(text) is not None
    )


class CommandNavigator:
    def __init__(self, store, symbol_resolver=None):
        self.store = store
        self.symbol_resolver = symbol_resolver

    @staticmethod
    def key(message):
        # Platform, sender and conversation all delimit the pending choice.
        return (
            f"{message.channel}:{message.user_id}:{message.chat_id or message.user_id}"
        )

    def menu(self, message, title, options, *, labels=None):
        self.store.put(
            "command_choices",
            self.key(message),
            {
                "id": uuid4().hex,
                "labels": list(labels if labels is not None else options),
                "options": list(options),
                "expires_at": (
                    datetime.now(timezone.utc) + timedelta(minutes=5)
                ).isoformat(),
            },
        )
        return (
            title
            + "\n"
            + "\n".join(f"{i}. {option}" for i, option in enumerate(
                labels if labels is not None else options, 1))
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
        if re.match(r"^[A-Za-z0-9]{6}(?:\s|$)", text):
            code, *rest = text.split(maxsplit=1)
            text = code.upper() + (" " + rest[0] if rest else "")
        if pending.get("name"):
            if not re.fullmatch(r"[A-Za-z0-9]{6}", text):
                return "6자리 종목코드만 보내세요. (취소 가능)"
            arguments = text + " " + pending["name"]
        elif pending.get("symbol"):
            if not re.fullmatch(r"[0-9]+", text):
                return "수량을 0 이상의 정수로 보내세요. (취소 가능)"
            arguments = pending["symbol"] + " " + text
        elif command in {"관심종목 추가", "관심종목 제거"}:
            arguments = text
            if not re.match(r"^[A-Za-z0-9]{6}(?:\s|$)", text):
                if text.isdecimal():
                    return "종목코드는 6자리입니다. 다시 보내세요. (취소 가능)"
                if len(text) > 40:
                    return "종목명은 40자 이내로 보내세요. (취소 가능)"
                names = dict(self.store.items("symbol_names"))
                names.update(dict(self.store.items("watchlist")))
                matches = [
                    code for code, value in names.items() if value["name"] == text
                ]
                if command.endswith("추가") and self.symbol_resolver:
                    try:
                        matches = self.symbol_resolver.resolve(text)
                    except DataError as exc:
                        self.wait_for_input(message, command, name=text)
                        return f"{exc}\n6자리 종목코드만 보내세요. (5분 내, 취소 가능)"
                    if len(matches) > 1:
                        self.wait_for_input(message, command, name=text)
                        return "같은 이름의 종목이 여러 개입니다: " + ", ".join(matches) + "\n6자리 종목코드만 보내세요. (5분 내, 취소 가능)"
                if len(matches) == 1:
                    arguments = matches[0] + (
                        " " + text if command.endswith("추가") else ""
                    )
                elif command.endswith("추가"):
                    self.wait_for_input(message, command, name=text)
                    return f"'{text}'의 코드를 확인할 수 없습니다. 6자리 종목코드만 보내세요. (5분 내, 취소 가능)"
                else:
                    return "제거할 종목을 찾을 수 없거나 이름이 중복됩니다. 6자리 코드만 보내세요."
        elif command == "보유량 설정" and re.fullmatch(r"[A-Za-z0-9]{6}", text):
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
        candidates = command_candidates(text)
        if len(candidates) > 1:
            return self.menu(message, "명령어가 겹칩니다. 무엇을 실행할지 선택해주세요.", candidates)
        if candidates:
            text = candidates[0]
        if text == "종목별 제안 선택":
            names = dict(self.store.items("symbol_names"))
            names.update(dict(self.store.items("watchlist")))
            proposals = dict(self.store.items("proposals"))
            watch_proposals = dict(self.store.items("watch_proposals"))
            symbols = sorted(set(dict(self.store.items("watchlist")))
                             | set(proposals) | set(watch_proposals)
                             | {symbol for symbol, holding in self.store.items("manual_holdings")
                                if holding.get("quantity", 0) > 0})
            if not symbols:
                return "선택할 종목이 없습니다. '관심종목 추가'로 종목을 등록한 뒤 '오늘 브리핑'을 실행하세요."
            labels = []
            for symbol in symbols:
                info = names.get(symbol) or proposals.get(symbol) or watch_proposals.get(symbol) or {}
                labels.append(f"{info.get('name', symbol)} ({symbol}) — 제안 보기")
            return self.menu(message, "[종목별 제안 선택] 조회할 종목을 선택해주세요.",
                             [f"제안 보기 {symbol}" for symbol in symbols], labels=labels)
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
