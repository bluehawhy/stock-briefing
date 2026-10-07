from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from app.config import Settings
from app.connections import registration_link
from app.models import DataError, Strategy, number, symbol_code
from app.storage import Store
from app.commands import CommandNavigator, HELP

KST = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class InboundMessage:
    channel: str
    user_id: str
    text: str
    chat_id: str | None = None
    is_private: bool = True
    message_id: str | None = None


class BotService:
    def __init__(self, settings: Settings, store: Store):
        self.settings, self.store = settings, store

    def handle(self, message: InboundMessage) -> str | None:
        allowed = (
            self.settings.kakao_allowed_user_ids
            if message.channel == "kakao"
            else self.settings.telegram_allowed_user_ids
            if message.channel == "telegram"
            else frozenset()
        )
        if (
            not message.user_id
            or message.user_id not in allowed
            or not message.is_private
        ):
            return None
        if message.channel == "telegram" and message.chat_id:
            if message.chat_id != message.user_id:
                return None
            self.store.register_telegram(message.user_id, message.chat_id)
        key = (
            f"{message.channel}:{message.user_id}:{message.message_id}"
            if message.message_id
            else None
        )
        owner = None
        if key:
            cached = self.store.get("responses", key)
            if cached:
                return cached["text"]
            owner = self.store.acquire_lease("message:" + key, 30)
            if not owner:
                return "같은 메시지를 처리 중입니다. 잠시 후 상태를 조회하세요."
        try:
            try:
                navigator = CommandNavigator(self.store)
                reply = navigator.route(message, self._command)
                reply = navigator.paginate(message, reply)
            except DataError as exc:
                reply = str(exc)
            except (ValueError, TypeError):
                reply = "입력값 또는 설정을 확인하세요. '도움말' 또는 '설정 도움말'을 입력하세요."
            if key:
                self.store.put("responses", key, {"text": reply})
            return reply
        finally:
            if owner:
                self.store.release_lease("message:" + key, owner)

    def _proposal(self, message, action, payload, summary):
        code = self.store.confirm(message, action, payload)
        return f"{summary}\n적용하려면 5분 내 이 대화에서 '확인 {code}'를 입력하세요."

    def _command(self, m: InboundMessage) -> str:
        command = m.text.strip()
        parts = command.split()
        today = datetime.now(KST).date()
        if command in {"/start", "/help", "도움말"}:
            return HELP
        if command == "오늘 브리핑":
            record = self.store.briefing(today)
            if not record:
                previous = self.store.briefing()
                return (
                    "오늘 브리핑이 없습니다. '브리핑 갱신'으로 생성을 요청하세요."
                    + (
                        f"\n최근 기록은 {previous[0]}입니다. '브리핑 이력 {previous[0]}'으로 조회하세요."
                        if previous
                        else ""
                    )
                )
            return f"[저장된 브리핑: {record[0]}]\n{record[1]}"
        if command == "브리핑 갱신":
            job = self.store.enqueue_refresh()
            return f"브리핑 갱신 요청 #{job}. '서비스 상태'로 진행 확인 후 '오늘 브리핑'을 조회하세요."
        if command == "브리핑 이력":
            self.store.init()
            with self.store.connect() as db:
                days = db.execute(
                    "SELECT briefing_date FROM briefings ORDER BY briefing_date DESC LIMIT 10"
                ).fetchall()
            return "최근 브리핑: " + (", ".join(d[0] for d in days) or "없음")
        if len(parts) == 3 and parts[:2] == ["브리핑", "이력"]:
            record = self.store.briefing(date.fromisoformat(parts[2]))
            return (
                f"[{record[0]}]\n{record[1]}"
                if record
                else "해당 날짜의 브리핑이 없습니다."
            )
        if command in {"서비스 상태", "/status"}:
            record, delivery, job = (
                self.store.briefing(),
                self.store.last_delivery(),
                self.store.latest_job(),
            )
            collection = self.store.items("collection")
            return (
                f"최근 브리핑: {record[0] if record else '없음'}\n"
                f"텔레그램 자동 발송: {delivery[0] + ' / ' + delivery[1] if delivery else '이력 없음'}\n"
                f"갱신 작업: {str(job) if job else '없음'}\n"
                + "\n".join(
                    f"{symbol}: {state['state']} / {state.get('day', '-')} / {state.get('reason', '')}"
                    for symbol, state in collection
                )
                + f"\n확인 시각: {datetime.now(KST):%Y-%m-%d %H:%M} KST"
            )
        if command == "연동 상태":
            state = self.store.get("provider_status", "kis", {})
            tracked = [
                key for key, a in self.store.items("accounts") if a.get("tracked")
            ]
            data = self.store.get("data_status", "latest", {})
            complete_data = self.store.get("data_status", "last_complete", {})
            return (
                f"한국투자증권: {state.get('state', '미연결')} / 토큰 만료 {state.get('expires_at', '-')}\n"
                "미래에셋증권: 국내주식 API 명세·권한 미확인, 연결 미지원\n"
                f"추적 계좌: {', '.join(tracked) or '없음'}\n최근 분석 기준일: {data.get('day', '없음')}\n"
                f"최근 완전 데이터: {complete_data.get('day', '없음')} / 완전 종목 {data.get('complete', 0)}/{data.get('total', 0)}\n"
                "실제 주문: 비활성 (조회용 어댑터)"
            )
        if parts[:1] == ["관심종목"]:
            if command in {"관심종목", "관심종목 보기"}:
                return "관심종목:\n" + (
                    "\n".join(
                        f"{k} {v['name']}" for k, v in self.store.items("watchlist")
                    )
                    or "없음"
                )
            if len(parts) >= 3 and parts[1] == "추가":
                symbol = symbol_code(parts[2])
                name = " ".join(parts[3:]) or symbol
                if len(name) > 40:
                    raise DataError("종목명 길이 초과")
                self._watchlist_change(symbol, {"name": name})
                self.store.audit("watchlist_add", symbol)
                return f"관심종목 추가: {name} ({symbol}). 종목 유효성은 데이터 갱신 시 확인합니다."
            if len(parts) == 3 and parts[1] in {"제거", "삭제"}:
                symbol = symbol_code(parts[2])
                self._watchlist_change(symbol, None)
                self.store.audit("watchlist_remove", symbol)
                return f"관심종목 제거: {symbol}. 과거 데이터·모의 기록은 보관합니다."
        if command in {"보유종목", "보유량"}:
            tracked = [
                (k, a) for k, a in self.store.items("accounts") if a.get("tracked")
            ]
            rows = []
            if tracked:
                for key, _ in tracked:
                    snapshot = self.store.get("holdings", key)
                    rows.append(
                        key
                        + ": "
                        + (
                            f"{snapshot['quantities']} / 기준 {snapshot['at']}"
                            if snapshot
                            else "조회 이력 없음"
                        )
                    )
            else:
                rows = [
                    f"{k}: {v['quantity']}주 / 수동 기준 {v['at']}"
                    for k, v in self.store.items("manual_holdings")
                ]
            return "보유량:\n" + (
                "\n".join(rows) or "미등록 (0주도 명시적으로 설정 필요)"
            )
        if len(parts) == 4 and parts[:2] == ["보유량", "설정"]:
            symbol, quantity = symbol_code(parts[2]), number(parts[3])
            return self._proposal(
                m,
                "manual_holding",
                {"symbol": symbol, "quantity": quantity},
                f"수동 보유량: {symbol} → {quantity}주",
            )
        if parts[:2] == ["제안", "보기"] and len(parts) in {2, 3}:
            records = (
                self.store.items("proposals")
                if len(parts) == 2
                else [(symbol_code(parts[2]), self.store.get("proposals", parts[2]))]
            )
            from app.briefings import BriefingEngine

            return (
                "\n\n".join(
                    BriefingEngine._format(v) + f"\n기준 {v['day']} / 생성 {v['at']}"
                    if v and "indicators" in v
                    else f"{k}: {v.get('reason', '없음') if v else '없음'}"
                    for k, v in records
                )
                or "저장된 제안이 없습니다."
            )
        if command == "증권사 연결 한국투자증권":
            return (
                "5분 내 일회용 등록 화면에 입력하세요. 키를 대화에 보내지 마세요.\n"
                + registration_link(self.settings, self.store)
            )
        if command == "증권사 연결 미래에셋증권":
            return "미래에셋 국내주식 API 명세·개인 이용 권한을 확인하기 전에는 연결할 수 없습니다."
        if command == "증권사 연결 해제 한국투자증권":
            return self._proposal(
                m,
                "disconnect",
                {},
                "한국투자 인증정보·토큰 삭제 및 추적 중지 (이력 보관)",
            )
        if command == "추적 계좌 보기":
            return "계좌:\n" + (
                "\n".join(
                    f"{k}: ****{a['number'][-4:]}-{a['product']} / 추적 {'ON' if a.get('tracked') else 'OFF'}"
                    for k, a in self.store.items("accounts")
                )
                or "등록 없음. 증권사 연결 화면에서 계좌를 등록하세요."
            )
        if (
            len(parts) == 4
            and parts[:2] == ["추적", "계좌"]
            and parts[2] in {"추가", "제거"}
        ):
            if not self.store.get("accounts", parts[3]):
                return "등록된 계좌 ID를 사용하세요. '추적 계좌 보기'로 확인하세요."
            return self._proposal(
                m,
                "tracking",
                {"id": parts[3], "tracked": parts[2] == "추가"},
                f"{parts[3]} 추적 {parts[2]}",
            )
        if command == "설정 보기":
            return (
                json.dumps(
                    self.store.get("settings", "strategy", {}),
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n안전 한도: "
                + json.dumps(
                    self.store.get("settings", "order_limits", {}), ensure_ascii=False
                )
                + "\n정기 알림: "
                + (
                    "ON"
                    if self.store.get("settings", "notifications", {"enabled": True})[
                        "enabled"
                    ]
                    else "OFF"
                )
            )
        if command == "설정 도움말":
            return (
                "설정 변경 {JSON}: 전체 전략을 명시하세요. 자동 기본 전략은 없습니다.\n"
                "short_ma/long_ma: 1≤단기<장기≤250, volume_window 1~250, flow_window 1~30\n"
                "volume_ratio>0, 0≤range_buy_max<range_sell_min≤1\n"
                "buy_score 1~가중치합, sell_score -가중치합~-1, buy_budget/max_position 원 (0 허용)\n"
                "weights: ma_trend,price_position,cross,range_position,volume,institution,foreign 각각 0~100 정수, 합>0\n"
                "sell_fraction 0초과~1, holdings_max_age_hours 0초과~8760\n"
                "evaluation_days 1~250 거래일, fee_bps/tax_bps/slippage_bps 0~9999 (1bps=0.01%)\n"
                "설정 변경 일부JSON: 기존 전체 설정이 있을 때 일부 값만 변경 가능\n"
                '주문 한도 변경 {"symbol_amount":원,"daily_amount":원,"price_deviation":0~1,"valid_seconds":1~300}\n'
                "점수: 이평 방향·종가 위치·교차·52주 위치·거래량 동반 방향·기관·외국인 각 신호 ±1 × 설정 가중치\n"
                "설정 변경 후 확인 코드를 입력하세요."
            )
        if command.startswith("설정 변경 "):
            changes = json.loads(command[len("설정 변경 ") :])
            if not isinstance(changes, dict):
                raise DataError("JSON 객체 필요")
            previous = self.store.get("settings", "strategy", {})
            strategy = Strategy.parse(previous | changes)
            return self._proposal(
                m,
                "strategy",
                strategy.dict(),
                "전략 변경 전: "
                + json.dumps(previous, ensure_ascii=False)
                + "\n변경 후: "
                + json.dumps(strategy.dict(), ensure_ascii=False),
            )
        if command in {"알림 켜기", "알림 끄기"}:
            return self._proposal(
                m,
                "notifications",
                {"enabled": command.endswith("켜기")},
                command + " (08:30 정기 발송)",
            )
        if command.startswith("주문 한도 변경 "):
            values = json.loads(command[len("주문 한도 변경 ") :])
            if not isinstance(values, dict) or set(values) != {
                "symbol_amount",
                "daily_amount",
                "price_deviation",
                "valid_seconds",
            }:
                raise DataError("필수 주문 한도 누락")
            parsed = {
                k: number(v) if k != "price_deviation" else float(v)
                for k, v in values.items()
            }
            if (
                not 0 <= parsed["price_deviation"] <= 1
                or not 1 <= parsed["valid_seconds"] <= 300
            ):
                raise DataError("주문 한도 범위 오류")
            return self._proposal(
                m,
                "order_limits",
                parsed,
                "주문 한도 변경: " + json.dumps(parsed, ensure_ascii=False),
            )
        if command == "모의 운용 기록":
            evaluations = self.store.items("evaluations")
            total = len(self.store.items("recommendations"))
            header = f"모의 평가: {len(evaluations)}건 / 미평가 {total - len(evaluations)}건\n"
            if evaluations:
                header += f"비용 반영 양의 손익 비율: {sum(v['hit'] for _, v in evaluations) / len(evaluations):.1%}\n"
            return (
                header
                + "\n".join(
                    f"{k} → {v['day']} / {v['pnl']:+,.0f}원"
                    for k, v in evaluations[-10:]
                )
                + "\n매수: 종가 진입·청산 왕복 가정 / 매도: 매도 후 재매수 회피손익, 공매도 아님.\n"
                "관측 시점 실제 종가와 당시 비용·평가 거래일 설정 사용. 실제 체결·확정 수익과 다릅니다."
            )
        if command in {"당일 주문 중지", "당일 주문 재개"}:
            return self._proposal(
                m,
                "order_stop",
                {"day": today.isoformat(), "stopped": command.endswith("중지")},
                command,
            )
        if parts[:2] == ["주문", "요청"]:
            return self._order_preview(m, parts)
        if len(parts) == 3 and parts[:2] == ["주문", "확인"]:
            return self.store.apply_confirmation(m, parts[2], self._apply)
        if command.startswith(("주문 정정", "주문 취소")):
            return "현재 조회용 API 어댑터는 주문 정정·취소를 지원하지 않습니다. 증권사 앱에서 확인하세요."
        if command == "주문 상태":
            stopped = self.store.get("order_stop", today.isoformat(), {}).get(
                "stopped", False
            )
            return f"실제 주문 비활성 / 당일 중지 {'ON' if stopped else 'OFF'} / 서버 제출 이력 없음"
        if len(parts) == 2 and parts[0] == "확인":
            return self.store.apply_confirmation(m, parts[1], self._apply)
        return (
            "지원하지 않는 명령입니다. '도움말'을 입력해 사용 가능한 명령을 확인하세요."
        )

    def _watchlist_change(self, symbol, value):
        self.store.init()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM leases WHERE name='collection' AND expires_at>?",
                (datetime.now(timezone.utc).isoformat(),),
            ).fetchone():
                raise DataError("데이터 갱신 중입니다. 완료 후 관심종목을 변경하세요.")
            if value is None:
                db.execute(
                    "DELETE FROM records WHERE kind='watchlist' AND key=?", (symbol,)
                )
            else:
                Store.put_in(db, "watchlist", symbol, value)
                if value["name"] != symbol:
                    Store.put_in(db, "symbol_names", symbol, value)
            db.execute(
                "DELETE FROM records WHERE kind='proposals' AND key=?", (symbol,)
            )

    def _order_preview(self, m, parts):
        if len(parts) != 7 or parts[3] not in {"매수", "매도"}:
            return "사용법: 주문 요청 계좌ID 매수|매도 종목코드 수량 지정가격"
        account = self.store.get("accounts", parts[2])
        if not account or not account.get("tracked"):
            return "추적 중인 계좌 ID가 필요합니다."
        symbol, quantity, price = (
            symbol_code(parts[4]),
            number(parts[5]),
            number(parts[6]),
        )
        if not quantity or not price:
            raise DataError("수량·가격은 0 초과")
        limits = self.store.get("settings", "order_limits")
        if not limits:
            return "주문 안전 한도를 먼저 설정하세요. 실제 주문 제출은 비활성입니다."
        today = datetime.now(KST).date().isoformat()
        if self.store.get("order_stop", today, {}).get("stopped"):
            return "당일 주문 중지 상태입니다."
        if quantity * price > min(limits["symbol_amount"], limits["daily_amount"]):
            return "설정한 주문 금액 한도를 초과합니다."
        # Historical close cannot validate a live order; this is an offline preview only.
        payload = {
            "account": parts[2],
            "action": parts[3],
            "symbol": symbol,
            "quantity": quantity,
            "price": price,
        }
        code = self.store.confirm(m, "order_disabled", payload)
        return (
            f"[제출 불가 미리보기] {parts[2]} / {parts[3]} {symbol} {quantity}주 × {price:,}원\n"
            "지정가 / 실시간 시세·주문가능금액 미검증 / 실제 주문 비활성\n"
            f"5분 내 같은 대화에서 '주문 확인 {code}': 비활성 상태를 확인하며 주문은 제출되지 않습니다."
        )

    def _apply(self, db, action, payload):
        if action == "order_disabled":
            return "실제 주문 제출이 비활성입니다. 주문 API를 호출하지 않았습니다."
        if action in {"strategy", "manual_holding", "tracking", "disconnect"}:
            if db.execute(
                "SELECT 1 FROM leases WHERE name='collection' AND expires_at>?",
                (datetime.now(timezone.utc).isoformat(),),
            ).fetchone():
                raise DataError(
                    "데이터 갱신 중입니다. 완료 후 같은 확인 코드를 다시 입력하세요."
                )
        if action == "strategy":
            Strategy.parse(payload)
            Store.put_in(db, "settings", "strategy", payload)
        elif action in {"notifications", "order_limits"}:
            Store.put_in(db, "settings", action, payload)
        elif action == "manual_holding":
            Store.put_in(
                db,
                "manual_holdings",
                payload["symbol"],
                {
                    "quantity": payload["quantity"],
                    "at": datetime.now(timezone.utc).isoformat(),
                },
            )
        elif action == "tracking":
            row = db.execute(
                "SELECT value FROM records WHERE kind='accounts' AND key=?",
                (payload["id"],),
            ).fetchone()
            if not row:
                return "계좌가 삭제되어 요청을 적용할 수 없습니다."
            account = json.loads(row[0])
            account["tracked"] = payload["tracked"]
            Store.put_in(db, "accounts", payload["id"], account)
        elif action == "disconnect":
            if self.settings.kis_app_key or self.settings.kis_app_secret:
                return "환경변수 KIS_APP_KEY/KIS_APP_SECRET을 먼저 제거하고 컨테이너를 재시작하세요."
            db.execute("DELETE FROM records WHERE kind='credentials'")
            db.execute("DELETE FROM records WHERE kind='provider_status'")
            db.execute("DELETE FROM records WHERE kind='registration'")
            for key, value in db.execute(
                "SELECT key,value FROM records WHERE kind='accounts'"
            ).fetchall():
                account = json.loads(value)
                account["tracked"] = False
                Store.put_in(db, "accounts", key, account)
        elif action == "order_stop":
            if payload["day"] != datetime.now(KST).date().isoformat():
                return "요청한 날짜가 지나 적용하지 않았습니다. 다시 요청하세요."
            Store.put_in(
                db, "order_stop", payload["day"], {"stopped": payload["stopped"]}
            )
        else:
            raise DataError("미지원 확인 작업")
        if action in {"strategy", "manual_holding", "tracking", "disconnect"}:
            db.execute("DELETE FROM records WHERE kind='proposals'")
        return "변경을 적용했습니다. 분석에 반영하려면 '브리핑 갱신'을 요청하세요."
