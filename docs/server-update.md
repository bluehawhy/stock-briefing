# 서버 업데이트

운영 구성은 API·PostgreSQL·Telegram입니다. 정기 분석·발송은 API에 포함됩니다.

기존 SQLite 서버의 최초 전환은 [POSTGRES_MIGRATION.md](../POSTGRES_MIGRATION.md)를 먼저 읽고 실행하세요.
일반 Compose 업데이트로 최초 이전을 대체하지 않습니다.

전환 후에는 대시보드의 Compose 업데이트 또는 scripts/update-server.sh를 사용합니다.
기존 프로젝트명과 전체 Compose override 순서를 유지하며 api·telegram을 빌드하고 db·api·telegram을 올립니다.
스케줄러 활성화 override를 누락하면 정기 실행이 꺼질 수 있으므로 컨테이너 라벨에 기록된 목록을 유지합니다.

확인: /healthz의 database=postgres, daily_scheduler=enabled, API의 Daily scheduler started/result 로그를 확인합니다.
실제 텔레그램 전달은 delivery_runs와 실제 수신을 확인합니다. sent/pending/review_required는 자동 초기화하지 않습니다.

