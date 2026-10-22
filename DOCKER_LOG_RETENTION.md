# Docker 출력 로그 100일 보관

Docker stdout/stderr 로그를 Ubuntu 호스트의 journald로 보냅니다. 기존 max-size: 10m / max-file: 3 제한은 제거합니다. 100일 정책은 Compose 옵션이 아니라 호스트에서 관리합니다.

**먼저 [서버 설치·기존 로그 이전 안내](https://github.com/bluehawhy/bluewaikiki_api_server/blob/main/server-admin/docker-log-retention.md)를 적용하세요.** 호스트 설정 없이 이 Compose만 적용하면 journald 기본 용량 제한으로 100일 전에 로그가 삭제될 수 있습니다. Linux systemd 호스트용 설정입니다.

1. 서버 관리 저장소의 설치 도구로 persistent journal과 100일 정리 타이머를 설치합니다.
2. Docker를 재시작하여 기본 로그 드라이버를 적용합니다. 기존 컨테이너 설정은 이 단계로 바뀌지 않습니다.
3. 저장소를 업데이트하고 현재 운영 중인 프로젝트명, 전체 Compose 파일 순서, 환경파일·대시보드 관리 환경변수를 유지합니다. 로컬 override에 logging 설정이 남아 있으면 중앙 도구의 override를 마지막 파일로 추가하여 기존 options까지 제거하세요. Compose >= 2.24.4가 필요합니다.
4. 기존 프로젝트를 stop하고 각 컨테이너의 최근 100일 로그를 중앙 archive 도구로 내보냅니다. archive 성공 후 동일 Compose 명령으로 up -d --force-recreate 합니다. down -v, 볼륨 삭제, Docker 내부 로그 파일 수정은 사용하지 않습니다.
5. 중앙 inventory 도구에서 모든 대상의 logging.Type이 journald이며 max-size/max-file이 없는지 확인합니다.

새 컨테이너의 docker logs / docker compose logs 및 대시보드 로그 조회는 계속 사용할 수 있습니다. 재생성 전 컨테이너 로그는 gzip 보관본 또는 journalctl CONTAINER_NAME=이름으로 조회합니다. 과거에 용량 제한으로 이미 삭제된 로그는 복구되지 않습니다.

journal 정리는 파일 단위이며 약 1시간, 이전 gzip 보관본은 UTC 날짜 파일 단위이며 최대 1일의 경계 지연이 있습니다. 이 정책은 Docker 출력 로그에 적용되며 앱 자체 파일·DB 로그, Caddy 접근 로그 및 빌드 산출물 보관 기간은 별도입니다.
