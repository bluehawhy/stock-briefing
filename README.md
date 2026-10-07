# stock-briefing

국내주식 데이터를 수집하고 지표·보유량·설정 예산으로 제안을 계산해 매일 **08:30 KST 텔레그램 개인 대화**로 전달합니다. 카카오와 텔레그램의 요청은 같은 공통 명령 처리기를 사용합니다. 사용자용 대시보드는 없으며, 비밀 키 입력에만 일회용 HTTPS 화면을 사용합니다.

## 구현 범위

- 한국투자증권 조회용 REST 어댑터: 토큰 발급·암호화 캐시, 거래소 일정, 수정주가 일봉 페이지 조회, 최근 확정 기관·외국인 수급, 선택 계좌의 잔고 연속조회
- SQLite 증분 수집: 처음에는 52주 계산에 필요한 과거 구간을 채우고, 이후에는 빠진 거래일만 요청; 일봉·수급 별도 upsert
- 이동평균·교차, 52주 범위·전일 위치 변화, 거래량 비율, 기관·외국인 순매수·직전 구간 비교
- 설명 가능한 7개 조건 점수와 정수 수량 계산: 보유금액 한도, 공통 매수 예산, 비용, 매도 비율 반영
- 데이터·보유량·전략 설정 누락 시 `데이터 확인 필요`; 이전 의견을 최신 제안으로 재사용하지 않음
- worker의 수집 → 분석 → DB 저장 → 텔레그램 발송; 발송일별 예약으로 중복 방지
- 관심종목·보유량·추적 계좌·전략·알림 관리, 브리핑 갱신 작업 큐·이력·상태 조회
- 채널·사용자·대화에 묶인 5분 일회용 변경 확인, 텔레그램 update 중복 응답 캐시
- 종가 기반 모의 성과: 당시 제안 가격·수량·정책·비용 스냅샷, 설정 거래일 후 관측 가격 평가
- 주문 중지·한도 설정 및 **제출 불가 미리보기**. 실제 주문 API는 구현에 포함하지 않음
- WAL 동시접근, 데이터 수집 lease, 제한된 API 재시도, 비밀 URL 로그 억제, 온라인 DB 백업

**실계좌 API 키/권한 검증과 투자 전략·예산 설정은 운영자 설정이 필요합니다.** 임의의 투자 기간·가중치·예산을 자동 기본값으로 적용하지 않습니다. 미래에셋 국내주식 API는 확인된 명세·개인 이용 권한이 없어 지원 불가 상태를 안내하는 어댑터만 제공합니다. 모의 KIS 환경은 일정·수급 지원이 확인되지 않으면 분석을 보류합니다.

## 기존 Ubuntu 서버 업데이트

서버에서 아래 명령을 실행합니다. 개발 검증에서는 이미지 빌드를 하지 않았습니다. 서버가 새 Python 코드·의존성을 실행하려면 운영 이미지 갱신이 필요합니다.

```bash
cd ~/source/stock-briefing
git pull --ff-only
mkdir -p ../stock-briefing-secrets
chmod 700 ../stock-briefing-secrets
```

기존 `.env`에 `.env.example`의 신규 변수만 추가합니다. 기존 텔레그램·카카오 값을 유지하세요.

```dotenv
PUBLIC_BASE_URL=https://본인도메인
BROKER_SECRETS_DIR=../stock-briefing-secrets
MASTER_KEY_FILE=/run/stock-briefing-secrets/master.key
KIS_ENVIRONMENT=live
```

`PUBLIC_BASE_URL`은 아래 등록 경로를 프록시하는 주소입니다. 기존 가계부와 같은 호스트를 쓴다면 기존 Caddy 사이트 블록에 `/broker/register/*`만 추가로 프록시하세요. `/kakao/skill/*` 연결은 유지합니다. 예시는 [deploy/Caddyfile.example](deploy/Caddyfile.example)에 있습니다. 키 등록 경로의 접근 로그도 기록하지 않습니다.

```bash
docker compose --profile job build api telegram worker
# 최초 한 번만 생성. 기존 master.key가 있으면 실행하지 않음.
docker compose --profile job run --rm -v "$(pwd)/../stock-briefing-secrets:/key-write" worker \
  python -m app.cli create-master-key --file /key-write/master.key
docker compose up -d api telegram
```

키 파일은 별도의 보호된 폴더에 보관하며 DB와 함께 Git에 올리지 않습니다. 새 DB·백업 파일은 권한 600으로 생성합니다. 기존 DB·WAL 파일의 권한은 호스트에서 확인합니다. DB 구조는 추가 방식으로 초기화되어 기존 수신자·브리핑·발송 기록을 보존합니다.

기존 timer/service의 명령은 동일한 `docker compose --profile job run --rm worker`이므로 timer를 다시 만들 필요 없습니다. worker의 내부 명령이 `run-daily`로 변경되었습니다. 알림을 끄면 브리핑은 생성하고 정기 발송만 건너뜁니다.

## 첫 분석 설정

`도움말`, `메인`, `메뉴`는 메인 카테고리 선택 목록을 보여줍니다. 카테고리 이름이나 번호를 보내면 하위 항목을 보여주고, 하위 항목 이름이나 번호를 보내면 실행합니다. 예를 들어 `브리핑`은 `오늘 브리핑`, `브리핑 갱신`, `브리핑 이력`, `제안 보기` 메뉴를 보여주며, `오늘 브리핑`은 바로 당일 전체 브리핑을 조회합니다. 추가 인자가 필요한 항목은 입력 형식을 안내하고, 설정·계좌·보유량 변경의 기존 확인 절차는 그대로 사용합니다.

`오늘 브리핑, 브리핑 갱신`처럼 여러 명령을 입력하면 `명령어가 2개 입니다. 선택해주세요.`와 번호 목록을 보여줍니다. 선택 전에는 어느 명령도 실행하지 않으며, 번호 또는 전체 항목명으로 하나만 실행합니다. 쉼표·줄바꿈·세미콜론·`그리고`·` / ` 구분을 지원하며, 명확히 구분되는 조회·고정 인자 명령은 같은 줄에서도 인식합니다. JSON 내부의 쉼표와 종목명 안의 명령 단어는 추가 명령으로 해석하지 않습니다. 자유 형식 종목명이 있는 명령은 구분자를 사용하세요.

선택은 사용자·채널·대화별로 저장되고 5분 뒤 만료됩니다. `취소`는 현재 선택을 종료하고 `메인`은 첫 메뉴로 돌아갑니다. 텔레그램은 긴 조회 결과를 여러 메시지로 모두 전송합니다. 카카오는 긴 결과를 1,000자 이내로 나누며 `다음`을 보내 나머지 내용을 조회할 수 있습니다. 새로운 명령을 보내면 이전의 선택·이어보기 상태를 종료합니다.

1. 봇에 `증권사 연결 한국투자증권`을 보내고 일회용 HTTPS 화면에 키·시크릿·계좌번호를 입력합니다. 키는 암호화 저장됩니다. 이 단계는 토큰 발급만 검증하며, 계좌의 조회 권한은 추적 선택 후 첫 조회에서 검사합니다. 계좌 자동 열거 API를 가정하지 않으므로 각 계좌를 화면에서 등록합니다. 새 연결 등록은 기존 계좌 추적을 중지하므로 필요한 계좌를 다시 선택합니다.
2. `추적 계좌 보기`로 마스킹된 계좌와 ID를 확인하고 `추적 계좌 추가 KIS-...` → `확인 코드`를 보냅니다. 미선택 계좌는 조회하지 않습니다. 계좌 연동을 쓰지 않는다면 `보유량 설정 005930 0`처럼 **0주도 명시적으로 확인**합니다.
3. `관심종목 추가 005930 삼성전자`로 관심종목을 등록합니다. 추적 계좌 보유종목도 분석 대상에 포함됩니다. 종목명은 표시용이며 코드 유효성은 데이터 조회에서 확인합니다.
4. `설정 도움말`을 보고 전체 전략 JSON을 `설정 변경 {JSON}`으로 보내고 확인합니다. 운영자 로컬 설정은 `python -m app.cli import-strategy --file strategy.json`도 지원합니다. JSON 파일을 컨테이너 `/data/strategy.json`에 놓으면 `docker compose --profile job run --rm worker python -m app.cli import-strategy --file /data/strategy.json`으로 등록할 수 있습니다. 변수와 규칙은 [docs/implementation.md](docs/implementation.md)에 정리했습니다.
5. `브리핑 갱신` → `서비스 상태` → `오늘 브리핑`으로 확인합니다. 카카오 5초 응답 제한을 위해 갱신 요청은 큐에 넣고 즉시 응답합니다. API 컨테이너가 큐를 처리합니다.

직접 실행해서 DB 생성만 확인하려면:

```bash
docker compose --profile job run --rm worker python -m app.cli generate-briefing
# 당일 정기 발송 기록을 남기는 전체 실행:
docker compose --profile job run --rm worker
```

`send-test`, `inspect-telegram-id`, `save-briefing`, `send-briefing`은 기존 전달 경로 점검용으로 유지합니다. 수동으로 저장한 테스트 문구를 그대로 보내려면 `send-briefing`을 명시적으로 호출합니다. 기본 worker는 수동 문구 대신 새 브리핑을 생성합니다.

## 운영·백업

```bash
docker compose ps
docker compose logs --since=30m api telegram
systemctl list-timers stock-briefing.timer
# 실행 중에도 SQLite backup API로 일관된 스냅샷 생성 (새 경로만 허용)
docker compose --profile job run --rm worker python -m app.cli backup --file /data/stock-briefing.db.backup-YYYYMMDD
```

복원은 `api`·`telegram`과 정기 worker를 모두 중지한 상태에서 원본 DB를 따로 보관하고 WAL/SHM을 정리한 다음 백업 파일을 `stock-briefing.db`로 복사하여 권한 600으로 설정합니다. 암호화 키는 DB 백업과 별도로 보호·백업해야 하며, 키가 없으면 저장된 증권사 인증정보를 복호화할 수 없습니다.

발송이 일부 메시지만 성공했거나 응답을 받기 전 연결이 끊기면 `review_required`로 기록하며 자동 재전송하지 않습니다. worker가 발송 예약 후 종료되어 `pending`이 남아도 재전송하지 않습니다. 실제 텔레그램 대화와 DB의 발송 기록을 확인한 후에만 운영자가 재처리해야 합니다. 서버가 08:30에 꺼져 있었다면 persistent timer가 나중에 실행하고, 실행 당시 한국 날짜의 전 거래일 종가를 기준으로 생성합니다.

## 테스트

```bash
python -m pip install -e '.[test]'
python -m pytest -q
```

Python 3.13에서 지표·수량·설정·증분 수집·휴장·데이터 누락·선택 계좌·모의 성과·등록 보안·동시 DB 접근·발송 중복과 결과 불명확 처리 등을 모의 데이터/HTTP로 검증합니다. 테스트는 실계좌와 실제 메시지를 호출하지 않습니다. API 이용 신청·실계좌 응답 검증과 서버 업데이트는 별도로 필요합니다. 초기 채널 설정은 [docs/bot-setup.md](docs/bot-setup.md)를 참고하세요.
