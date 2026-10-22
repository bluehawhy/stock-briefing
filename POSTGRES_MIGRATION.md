# API · PostgreSQL · Telegram 전환

기존 서버에서 한 번 수행하는 DB 이전입니다. 평소 Compose 업데이트와 다릅니다.
아직 운영 서버에 적용하지 않았으며 자동 테스트·실제 PostgreSQL 이전 검증은 수행하지 않았습니다.
실행 전 이 문서와 스크립트를 검토하세요. 오전 08:30 작업이 진행 중인 시간에는 실행하지 않습니다.

## 구성

| 서비스 | 역할 |
|---|---|
| api | 웹/API, 갱신 큐, 08:30 한국 시간 정기 분석·발송 |
| db | PostgreSQL 17, 호스트 포트 없는 기본 네트워크, 영속 볼륨 |
| telegram | 텔레그램 명령·버튼 수신, 동일 PostgreSQL 사용 |

worker Compose 서비스와 systemd 타이머 배포 템플릿은 제거합니다.
기존 `run-daily` CLI는 운영자 수동 실행용으로 유지하고 API와 동일한 잠금을 사용합니다.
SQLite는 오프라인 이전·기존 파일 복구 용도로 지원하며 운영 Compose는 PostgreSQL을 명시합니다.

## 이전 전 확인

- 기존 API 컨테이너 이름은 `stock-briefing-api-1`, 작업 경로는 `~/source/stock-briefing`인 현재 운영 서버용 스크립트입니다.
- 기존 전체 Compose 파일 목록과 프로젝트명은 실행 중 컨테이너 라벨에서 읽습니다. 원래 override와 관리 환경변수 파일을 보존합니다.
- `BROKER_SECRETS_DIR`의 master.key 등 기존 키를 별도 백업합니다. 새 PostgreSQL 암호 파일도 이 디렉터리에 생성되며 값을 출력하거나 Git에 저장하지 않습니다.
- journald 정책이 미설치된 호스트라면 먼저 기존 DOCKER_LOG_RETENTION.md를 따릅니다.
- PostgreSQL 마이그레이션 표식이 없으면 새 API/텔레그램은 시작을 중단합니다. 빈 PostgreSQL을 운영 DB처럼 사용하지 않습니다.
- SQLite 데이터와 발송 기록을 옮기기 전에 일반 대시보드 Compose 업데이트로 컨테이너를 재생성하지 마세요. 소스만 pull한 뒤 아래 스크립트로 이전합니다.

## 서버에서 한 번 실행

PR 병합 후:

```bash
cd ~/source/stock-briefing
git pull --ff-only origin main
bash scripts/migrate-postgres.sh
```

스크립트는 API·텔레그램 이미지를 먼저 빌드하고 DB를 준비합니다. 기존 타이머를 disable/stop하며,
이미 실행 중인 정기 작업이 있으면 중단하지 않고 스크립트를 종료합니다. 완료 후 다시 실행하세요.
API·텔레그램을 멈춘 뒤 SQLite backup API로 일관된 백업을 생성합니다. SQLite 원본은 삭제하지 않습니다.
백업 파일을 읽기 전용으로 PostgreSQL에 이전하며, 모든 테이블의 모든 행이 일치하는지 비교한 뒤
하나의 트랜잭션으로 완료 표식을 저장합니다. 대상이 비어 있지 않거나 이미 이전된 경우 덮어쓰지 않습니다.

예외: 종료된 프로세스의 leases는 비우고, running 상태의 수동 갱신 작업은 failed로 바꿉니다.
pending/review_required/sent 발송 기록, 봇 수신자, polling offset, 관심종목, 전략, 암호화된 증권사 키,
브리핑과 버튼 스냅샷, 시장 캐시, 로그, 감사 이력은 보존합니다. 결과 출력에는 테이블별 개수만 포함됩니다.

성공 후 스크립트가 `compose.scheduler.yaml`을 마지막 override로 추가해 API 스케줄러를 활성화합니다.
이 파일은 전환 이후 계속 사용해야 합니다. 컨테이너 라벨에 파일 목록이 남으므로 대시보드에서 같은 순서로 업데이트합니다.
해당 파일은 Git 작업 트리 변경으로 업데이트가 막히지 않도록 .gitignore에 등록합니다.

**08:30 이후 API를 시작하면 당일 미발송분이 실행될 수 있습니다.** 기존 당일 발송 기록이 있으면 재발송하지 않습니다.
발송 성공 여부가 불명확한 pending/review_required를 자동으로 지우거나 재시도하지 않습니다.

## 적용 확인 (읽기 전용)

```bash
docker ps --filter label=com.docker.compose.project=stock-briefing
docker logs --tail 30 stock-briefing-api-1
curl -fsS http://127.0.0.1:8001/healthz
systemctl is-enabled stock-briefing.timer
```

정상 기준: api·db·telegram 3개 서비스, healthz의 database=postgres와 daily_scheduler=enabled,
Daily scheduler started 로그, 구 타이머 disabled. 실제 08:30 결과는 API 로그와 DB delivery_runs를 함께 확인합니다.
로그·DB 개수만으로 텔레그램 수신 성공을 단정하지 않습니다.
API의 분석·DB I/O는 별도 스레드에서 처리해 웹 이벤트 루프를 막지 않습니다.
재시작 후 당일만 보완하며 지난 날짜를 소급 발송하지 않습니다. API 여러 프로세스는 PostgreSQL 세션 잠금을 공유합니다.

## 이후 업데이트·백업

대시보드 Compose 업데이트는 API·텔레그램을 재빌드합니다. 별도 worker 빌드는 필요 없습니다.
CLI는 실제 라벨의 파일 목록을 유지해야 하므로 기본 compose 단독 실행을 피하세요.
PostgreSQL 비밀번호 파일은 최초 생성 후 임의 변경하지 않습니다(기존 DB 암호가 자동 갱신되지 않습니다).

백업 예시(실제 전체 Compose 파일 목록에 맞춰 실행):

```bash
umask 077
docker compose -f compose.yaml -f compose.override.yaml -f compose.scheduler.yaml \
  exec -T db pg_dump -U stock_briefing -d stock_briefing -Fc \
  > "stock-postgres-$(date +%Y%m%d-%H%M%S).dump"
```

실패한 dump는 백업으로 간주하지 않습니다. 암호화 master.key와 PostgreSQL 암호는 dump와 별도로 보호합니다.
복원은 api·telegram을 중지한 뒤 새 비어 있는 DB에 pg_restore하고 완료 표식과 행을 확인한 다음 시작합니다.
DB 볼륨을 삭제하거나 down -v를 사용하지 않습니다.

## 실패 시

이전 도중 실패하면 API·텔레그램이 중지된 상태일 수 있습니다. 원본 SQLite와 백업은 남아 있습니다.
오류 출력을 확인하고, 이전이 성공한 DB에 스크립트를 다시 실행해 덮어쓰려 하지 마세요.
새 DB 쓰기가 시작되기 전이라면 이전 커밋과 이전 전체 Compose 파일 목록으로 api·telegram을 복구하고
구 systemd 타이머를 enable --now 할 수 있습니다. compose.scheduler.yaml은 이전 구성에서 제외합니다.
새 PostgreSQL에 쓰기가 시작된 뒤에는 SQLite가 최신이 아니므로 단순 복귀하지 말고 변경분을 먼저 보존해야 합니다.
