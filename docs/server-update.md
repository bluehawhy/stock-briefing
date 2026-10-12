# 주식 브리핑 서버 업데이트 설명서

대상: bluehawhy/stock-briefing  
서버 경로: ~/source/stock-briefing  
작성일: 2026-10-12

## 1. 서버 업데이트

우분투 서버에 접속한 뒤 아래 명령어를 한 번에 복사해서 실행합니다.
텔레그램이 아니라 서버 터미널에 입력합니다.

```bash
cd ~/source/stock-briefing
git pull origin main
docker compose up -d --build
docker compose ps
curl -fsS http://127.0.0.1:8000/healthz
```

| 명령어 | 역할 |
| --- | --- |
| cd ~/source/stock-briefing | 프로젝트 폴더로 이동 |
| git pull origin main | main의 최신 코드 가져오기 |
| docker compose up -d --build | 최신 코드로 이미지를 빌드하고 서비스를 백그라운드 실행 |
| docker compose ps | api와 telegram 서비스 상태 확인 |
| curl -fsS http://127.0.0.1:8000/healthz | API 정상 응답 확인 |

api와 telegram이 모두 실행 중(Up)인지 확인합니다.
git pull 또는 빌드에서 오류가 발생하면 업데이트 완료로 보지 말고 오류 내용을 확인합니다.

## 2. 브리핑 새로 생성하기

코드 업데이트만으로 기존에 저장된 브리핑이 바뀌지는 않습니다.
텔레그램 챗봇에 아래 명령어를 입력해서 새로 생성합니다.

```text
브리핑 갱신
```

이어서 아래 명령어로 진행 상태를 확인합니다.

```text
서비스 상태
```

갱신이 완료되면 아래 명령어로 결과를 조회합니다.

```text
오늘 브리핑
```

새 브리핑은 보유종목과 관심종목을 구분해서 표시합니다.
수동으로 등록한 보유종목도 포함하며, 두 목록에 함께 있는 종목은 각 항목에 표시하되 분석과 공통 매수 예산 계산은 한 번만 처리합니다.

## 3. 문제가 발생했을 때

서버 터미널에서 아래 명령어를 실행합니다.

```bash
cd ~/source/stock-briefing
docker compose ps
docker compose logs --tail=80 api telegram
```

- 챗봇이 응답하지 않으면 telegram 서비스 상태와 로그를 확인합니다.
- 브리핑 갱신이 실패하면 api와 telegram 로그를 확인합니다.
- 오늘 브리핑이 없다고 나오면 브리핑 갱신을 요청하고 서비스 상태에서 완료 여부를 확인합니다.
- 종목에 데이터 확인 필요가 표시되면 함께 나온 사유를 확인합니다. 코드 업데이트만으로 보유량 등록이나 증권사 연결이 자동으로 완료되지는 않습니다.

문제 확인을 요청할 때는 오류 메시지와 위 로그를 함께 전달합니다.
인증키, 토큰, 계좌번호 등 민감한 정보는 가리고 전달합니다.

## 4. 업데이트할 때의 순서

1. 서버 터미널에서 업데이트 명령어 실행
2. api와 telegram 실행 상태 및 API 응답 확인
3. 텔레그램에서 브리핑 갱신 입력
4. 서비스 상태로 완료 확인
5. 오늘 브리핑 조회

