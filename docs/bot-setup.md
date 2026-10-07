# 텔레그램·카카오톡 챗봇 연결

이 문서는 `stock-briefing`의 현재 메시지 코드를 실제 봇 계정에 연결하는 순서입니다. 텔레그램은 명령 응답과 매일 08:30(한국 시간) 발송을 담당합니다. 카카오톡 채널 챗봇은 사용자가 보낸 메시지에만 응답합니다. 양쪽 메시지는 같은 `BotService`로 전달됩니다.

현재 명령은 `도움말`에서 확인합니다. 관심종목·보유량·계좌 추적·전략·알림·모의 기록 관리와 자동 브리핑 생성이 구현되어 있습니다. 데이터 공급자·전략·예산을 설정해야 분석이 가능하며, 설정이 없으면 데이터 확인 필요를 전달합니다. 첫 분석 설정과 서버 업데이트는 [README](../README.md), 지원 범위는 [구현 안내](implementation.md)를 참고하세요.

## 1. Ubuntu 서버 준비

```bash
git clone https://github.com/bluehawhy/stock-briefing.git
cd stock-briefing
cp .env.example .env
chmod 600 .env
```

Docker Compose가 프로젝트 디렉터리를 `/data`에 마운트하므로 SQLite 파일 `stock-briefing.db`와 WAL 파일은 프로젝트 최상단에 생성됩니다. 컨테이너가 이 디렉터리에 쓸 수 있어야 합니다. `.env`와 DB 파일을 Git에 올리지 마세요.

## 2. 텔레그램 봇 만들기

1. 텔레그램에서 [@BotFather](https://t.me/BotFather)를 열고 `/newbot`을 보냅니다.
2. 표시 이름(예: `Stock Briefing`)과 `bot`으로 끝나는 고유한 사용자 이름(예: `my_stock_briefing_bot`)을 지정합니다.
3. BotFather가 발급한 **토큰을 서버의 `.env`에만** `TELEGRAM_BOT_TOKEN=...`으로 저장합니다. 토큰을 채팅이나 GitHub에 붙이지 마세요.
4. 생성한 봇과 개인 대화를 열고 `/start`를 보냅니다. 폴러를 시작하기 전에 사용자 ID를 확인합니다.

```bash
docker compose run --rm telegram python -m app.cli inspect-telegram-id
```

표시된 ID가 본인 것인지 확인한 뒤 `.env`의 `TELEGRAM_ALLOWED_USER_IDS=<본인 숫자 ID>`를 채웁니다. 다른 사람이 봇에 먼저 메시지를 보냈다면 여러 ID가 나올 수 있으므로 임의로 선택하지 마세요.

```bash
docker compose up -d --build api telegram
docker compose run --rm telegram python -m app.cli send-test
```

개인 채팅에서 `/start`, `도움말`, `서비스 상태`를 시험합니다. 텔레그램 봇은 사용자가 먼저 대화를 시작해야 메시지를 보낼 수 있습니다. 폴러를 여러 인스턴스로 실행하거나 같은 토큰에 다른 `getUpdates` 소비자를 붙이지 마세요.

## 3. 카카오톡 채널 챗봇 만들기

카카오 계정으로 [카카오비즈니스](https://business.kakao.com/)와 [챗봇 관리자센터](https://chatbot.kakao.com/)에 가입하고, 카카오톡 채널을 생성합니다. 관리자센터에서 `카카오톡 챗봇`을 만들고 **설정 → 챗봇 관리 → 카카오톡 채널 연결**에서 채널을 연결합니다. 운영 채널에 적용하려면 봇을 배포해야 합니다.

스킬 URL은 집 서버에 들어오는 공개 HTTPS 주소가 필요합니다. DNS, 공유기 포트 포워딩/방화벽, TLS 역방향 프록시를 설정하고 `deploy/Caddyfile.example`을 서버 환경에 맞게 적용합니다. API 컨테이너는 기본적으로 호스트의 `127.0.0.1:8000`에만 노출됩니다. 외부에는 프록시의 HTTPS만 공개하세요.

```bash
# 임의의 긴 URL 비밀값을 만들고 출력 값을 .env에 직접 보관
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
```

1. `.env`에 `KAKAO_SKILL_SECRET=<생성한 값>`을 설정하고 API 컨테이너를 재시작합니다: `docker compose up -d --build api`.
2. 관리자센터 **스킬**에서 `stock-briefing` 스킬을 만들고 URL을 `https://<본인 도메인>/kakao/skill/<생성한 값>`으로 등록합니다.
3. **시나리오 → 시나리오 설정 → 폴백 블록**에 스킬을 연결하고, 응답 형식에서 **스킬 데이터 사용**을 선택합니다. 일반 명령을 특정 블록 발화에도 등록했다면 그 블록도 같은 스킬에 연결합니다. 웰컴 블록 등의 별도 응답이 있는 경우 모든 입력이 서버로 가지 않을 수 있습니다.
4. 스킬 테스트에서 요청의 `userRequest.user.id`를 확인하고 `.env`의 `KAKAO_ALLOWED_USER_IDS=<본인 사용자 ID>`에 넣습니다. 테스트 환경의 ID와 실제 카카오톡 채널에서의 ID가 같다고 가정하지 말고, 채널에서 본인이 `도움말`을 보내 응답을 확인합니다. 다르면 실제 채널 요청의 ID를 확인해 허용 목록을 고쳐야 합니다.
5. 개발 채널에서 `도움말`, `오늘 브리핑`을 시험한 뒤 **배포**하여 운영 채널에서 다시 확인합니다. 카카오 스킬은 서버가 약 5초 안에 응답해야 합니다.

스킬 URL에는 비밀값이 들어갑니다. 프록시 접근 로그에서 이 경로를 기록하지 않거나 경로를 마스킹하고, 다른 사람에게 URL을 공유하지 마세요. `KAKAO_ALLOWED_USER_IDS`가 비어 있거나 실제 사용자 ID와 다르면 봇은 `사용 권한이 없습니다.`라고 응답합니다. 현재 코드에는 카카오 ID를 자동 등록하는 절차가 없습니다.

## 4. 08:30 브리핑 발송 점검

데이터 수집과 분리해 전달 경로만 확인할 때는 테스트 문구를 해당 **발송일(한국 시간)**로 저장하고 `send-briefing`을 명시합니다. 기본 worker는 데이터를 수집하고 브리핑을 새로 생성합니다.

```bash
docker compose --profile job run -T --rm worker python -m app.cli save-briefing --date YYYY-MM-DD --file - < briefing.txt
docker compose --profile job run --rm worker python -m app.cli send-briefing
```

worker의 당일 발송 기록이 생기므로 같은 날짜로 여러 번 시험하려고 반복 실행하지 마세요. 실제 예약은 `deploy/systemd/stock-briefing.service`의 `WorkingDirectory`를 설치 경로로 바꾸고 service/timer를 `/etc/systemd/system/`에 설치한 뒤 다음처럼 켭니다.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now stock-briefing.timer
systemctl list-timers stock-briefing.timer
```

## 계정 연결에 필요한 값

| 값 | 저장 위치 | 외부 공유 |
| --- | --- | --- |
| 텔레그램 봇 토큰 | 서버 `.env` | 금지 |
| 텔레그램 본인 사용자 ID | 서버 `.env` | 불필요 |
| 카카오 스킬 URL 비밀값 | 서버 `.env`, 관리자센터 스킬 URL | 금지 |
| 카카오 본인 사용자 ID | 서버 `.env` | 불필요 |
| 서버 도메인 | DNS, HTTPS 프록시, 관리자센터 스킬 URL | 가능 |

참고: [Telegram BotFather](https://core.telegram.org/bots/features#botfather), [카카오 챗봇 만들기](https://kakaobusiness.gitbook.io/main/tool/chatbot/tutorial/make_chatbot/tutorial_1), [스킬 만들기](https://kakaobusiness.gitbook.io/main/tool/chatbot/skill_guide/make_skill), [스킬과 블록 연결](https://kakaobusiness.gitbook.io/main/tool/chatbot/tutorial/make_chatbot/tutorial_3), [배포](https://kakaobusiness.gitbook.io/main/tool/chatbot/main_notions/deploy).
