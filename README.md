# stock-briefing

실제 텔레그램 봇 계정과 카카오톡 채널 챗봇을 연결하는 단계별 안내는 [docs/bot-setup.md](docs/bot-setup.md)를 참고하세요.

카카오 챗봇 스킬 HTTP 요청과 텔레그램 봇 메시지를 같은 명령 처리기로 연결하는 첫 구현입니다. 카카오는 요청에만 답하고, 텔레그램은 요청에 답하며 저장된 브리핑을 오전 08:30에 자동 발송합니다. 현재 지원 명령은 `도움말`, `오늘 브리핑`, `서비스 상태`, `연동 상태`입니다. 증권사 API·시장 데이터 수집·전략 계산·실제 주문은 아직 구현되지 않았습니다. `연동 상태`도 그 상태를 그대로 알립니다.

## 초기 설정

1. 저장소를 Ubuntu의 Docker 프로젝트 폴더에 내려받고 `.env.example`을 `.env`로 복사합니다. `chmod 600 .env`로 권한을 제한합니다.
2. [Telegram BotFather](https://core.telegram.org/bots/features#botfather)에서 봇을 만들고 토큰을 `TELEGRAM_BOT_TOKEN`에 넣습니다. 봇에 `/start`를 보낸 뒤 **폴러를 시작하기 전에** `docker compose run --rm telegram python -m app.cli inspect-telegram-id`로 개인 사용자 ID를 조회해 `TELEGRAM_ALLOWED_USER_IDS`에 넣습니다. 예상한 본인 ID인지 확인하고 봇 토큰을 공유하지 않습니다.
3. `KAKAO_SKILL_SECRET`에는 충분히 긴 임의 값을 넣고, 카카오 챗봇의 스킬 URL을 `https://<도메인>/kakao/skill/<그 값>`으로 등록합니다. 챗봇 관리자센터에서 사용자 발화를 해당 스킬에 연결합니다. 스킬 테스트 요청의 `userRequest.user.id`를 확인해 `KAKAO_ALLOWED_USER_IDS`에 넣습니다. 카카오의 호출에는 유효한 HTTPS 주소가 필요합니다. `deploy/Caddyfile.example`은 Ubuntu 호스트에서 실행하는 Caddy의 예시입니다. 경로 비밀값이 프록시 접근 로그에 남지 않도록 해당 경로의 로그를 비활성화하거나 마스킹하세요.
4. 프로젝트 폴더에 Docker 컨테이너가 파일을 쓸 수 있도록 권한을 설정하고 `docker compose up -d --build api telegram`을 실행합니다. Compose는 프로젝트 폴더를 컨테이너의 `/data`로 마운트하므로 SQLite DB와 WAL 파일은 **프로젝트 최상단**에 함께 남습니다. 이 폴더에는 `.env`도 있으므로 Ubuntu 호스트와 컨테이너 접근 권한을 제한합니다.
5. `docker compose run --rm telegram python -m app.cli send-test`로 텔레그램 발송을 확인합니다. 카카오 스킬은 챗봇 테스트에서 `도움말`로 확인합니다.

현재 카카오와 텔레그램 사용자 ID는 각각 허용 목록으로 검사합니다. 두 ID를 같은 단일 운영자로 설정해야 하며, 채널 간 자동 계정 연결과 계좌·주문 기능은 다음 단계에서 구현합니다. 허용 ID가 비어 있으면 해당 채널의 명령은 처리하지 않습니다. 텔레그램은 개인 대화방만 허용합니다.

## 정기 발송

내부 브리핑 생성기가 완성되기 전에는 운영자가 다음 명령으로 오늘 브리핑을 DB에 저장해 전송 경로를 검증할 수 있습니다. 이는 **전략 계산 결과가 아닙니다**.

```bash
docker compose --profile job run -T --rm worker python -m app.cli save-briefing --date YYYY-MM-DD --file - < briefing.txt
docker compose --profile job run --rm worker
```

`briefing.txt`는 호스트에 작성한 테스트 문구 파일입니다. 날짜는 **발송일(Asia/Seoul)**입니다. 당일 브리핑이 DB에 없으면 worker는 발송을 건너뜁니다. 발송 성공·실패 여부가 불명확하면 자동 재전송하지 않고 `review_required`로 남깁니다.

`deploy/systemd/stock-briefing.service`의 `WorkingDirectory`를 실제 설치 폴더로 바꾸고 service/timer를 `/etc/systemd/system/`에 설치한 후 `sudo systemctl daemon-reload`와 `sudo systemctl enable --now stock-briefing.timer`를 실행합니다. timer는 Asia/Seoul 오전 08:30에 worker를 시작합니다. Docker 호스트의 컨테이너 볼륨 파일 권한과 방화벽·HTTPS 리버스 프록시를 별도로 설정합니다.

## 개발 검사

```bash
python -m pip install -e '.[test]'
python -m pytest -q
```

`tests`는 권한 거부, 동일 명령 처리, SQLite 발송 예약, 카카오 스킬 JSON, 텔레그램 API 발송 형식을 확인합니다. 외부 증권사나 실제 텔레그램 메시지는 테스트에서 호출하지 않습니다.
