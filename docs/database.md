# 데이터베이스 구조 및 거시경제 확장 설계

- 기준일: 2026-11-13
- 대상: bluehawhy/stock-briefing
- 운영 저장소: PostgreSQL
- 상태: **기존 테이블은 현행 코드 확인**, 거시경제 신규 테이블은 **미구현 설계**
- 관련 문서: [API 설계](api.md), [거시경제 종합 설계](macro-indicators-design.md), [PostgreSQL 이전 가이드](../POSTGRES_MIGRATION.md)

## 1. 설계 원칙

- 현행 국내주식의 종목·계좌·보유량·거래일·수급·브리핑 데이터는 그대로 유지한다.
- 한국·미국의 정책금리/국채금리/헤드라인·근원 CPI, 글로벌 WTI·브렌트유, 원/달러 환율은 별도 거시경제 시계열에 수집한다.
- **관측 기준일, 실제 발표시각, 최초/최종 수집시각을 구분**한다. 발표시각을 모르면 NULL을 기록한다.
- 정확한 데이터를 얻을 수 없는 경우 임의의 기본값이나 0으로 대체하지 않는다.
- 최종 갱신된 관측값과 정정 이력, 브리핑 당시 사용한 값의 스냅샷을 별도 관리해 과거 내용을 보호한다.
- 이번 문서는 DB/마이그레이션 코드 변경이나 실제 테이블 생성 작업을 수행하지 않는다.

## 2. 기존 PostgreSQL 스키마 — 구현 확인

근거: app/storage.py의 Store.init(), app/database.py의 연결 설정, app/migrate_database.py의 이전 대상 테이블.

| 테이블 | 기본 키/주요 열 | 용도 |
|---|---|---|
| telegram_recipients | user_id PK, chat_id UNIQUE, updated_at | 텔레그램 발송 대상 |
| poll_state | bot PK, next_update_id | 메시지 중복 처리 오프셋 |
| briefings | briefing_date PK, body, created_at | 하루별 최신 브리핑 본문 |
| delivery_runs | (briefing_date, channel) PK, status, detail, updated_at | 일일 전송 예약·완료·재검토 |
| records | (kind, key) PK, value, updated_at | 설정·계좌·보유량·관심종목·제안 등 JSON 문자열 |
| daily_bars | (symbol, day) PK, open/high/low/close/volume | 국내 종목 일봉 |
| investor_flows | (symbol, day) PK, institution, foreign_net | 기관·외국인 수급 |
| confirmations | code PK, channel, user_id, chat_id, action, payload, expires_at, used | 일회용 확인 |
| jobs | id PK, status, detail, updated_at | 데이터 갱신 작업 |
| leases | name PK, owner, expires_at | 동시 실행 잠금 |
| application_logs | id PK, created_at, level, source, message | 보안 필터가 적용된 진단 로그 |
| audit | id PK, action, detail, created_at | 감사 이력 |
| database_metadata | key PK, value | PostgreSQL 이전 완료/운영 메타데이터 |

- records 테이블의 kind 예시는 settings, watchlist, accounts, credentials, manual_holdings, watch_proposals, command_choices, runs 등이며, JSON 구조는 기존 서비스 코드가 관리한다.
- briefings 테이블은 당일 재분석 시 **같은 날짜의 body를 갱신**한다. 따라서 저장된 최신 본문과 이전에 텔레그램으로 실제 발송된 본문이 항상 같다고 보장할 수 없다.
- delivery_runs의 (날짜, 채널) 고유 키와 기존 발송 중복 방지 규칙을 유지한다.
- PostgreSQL 모드에서 database_metadata.sqlite_import_complete 표시가 없으면 기동을 차단하는 기존 보호 장치가 있다. 새 거시 테이블의 생성으로 이 보호를 우회하지 않는다.
- SQLite 지원/이전 기능은 기존 레거시 호환 범위로 보존하며, 새로운 거시 스키마의 운영 대상은 PostgreSQL이다.

## 3. 신규 거시경제 테이블 — 설계안 (미구현)

아래 필드·타입은 최종 DDL이 아닌 **논리 스키마**이다. 실제 CREATE TABLE / ALTER TABLE은 작성·적용하지 않았다.

### 3.1 macro_series — 시계열 사전

| 컬럼/제안 타입 | 내용 |
|---|---|
| series_id TEXT PK | 내부 고유 식별자 |
| country TEXT NOT NULL | KR, US, GLOBAL |
| indicator TEXT NOT NULL | policy_rate, treasury_yield, cpi, core_cpi, oil_price, usdkrw |
| provider TEXT NOT NULL | ECOS, KOSIS, FRED |
| provider_series_id TEXT NOT NULL | 통계표/시계열 식별자 |
| provider_item_key TEXT NULL | 세부항목·분류코드, 필요 시 |
| unit TEXT NOT NULL | PERCENT, INDEX, USD_PER_BBL, KRW_PER_USD |
| frequency TEXT NOT NULL | D, M, EVENT |
| seasonal_adjustment TEXT NOT NULL | SA, NSA, NA |
| base_period TEXT NULL | CPI 기준연도·기준지수 |
| active BOOLEAN NOT NULL | 수집 대상 여부 |
| metadata JSONB | 설명·원본 링크·매핑 버전 |

- ECOS의 한국 기준금리 722Y001, 원/달러 731Y003은 **확인 예정 후보**이다. 실제 항목·주기·통계 정의가 확인되기 전까지 정식 매핑으로 확정하지 않는다.
- KOSIS 한국 CPI·근원 CPI의 통계표/항목 코드는 메타데이터 검증 후 확정한다.
- FRED 미국 금리: DFEDTARL, DFEDTARU, DGS2, DGS10.
- FRED 미국 CPI: CPIAUCNS(비계절조정), CPIAUCSL(계절조정), CPILFESL(근원·계절조정).
- FRED 유가: DCOILWTICO, DCOILBRENTEU. 원/달러 보조: DEXKOUS.
- ECOS 환율과 FRED 환율은 서로 다른 series_id로 저장한다. 공시 시각·산정 기준이 다를 수 있으므로 원본을 한 행으로 합치지 않는다.

### 3.2 macro_observations — 최신 유효 관측값

| 컬럼/제안 타입 | 내용 |
|---|---|
| series_id TEXT FK | macro_series 참조 |
| observation_date DATE | 실측일 또는 월 통계 기준기간의 대표 날짜 |
| value NUMERIC(24,8) NOT NULL | 정규화한 공표값 |
| source_updated_at TIMESTAMPTZ NULL | 공식 수정 시각(알 수 있을 때만) |
| fetched_at TIMESTAMPTZ NOT NULL | 마지막으로 자료를 확인한 시각 |
| revision INTEGER NOT NULL DEFAULT 1 | 값 변경 버전 |
| PK | (series_id, observation_date) |

- 월간 CPI의 observation_date가 월초 날짜라면 이는 **통계 기준월 표기**이지 월초 공표를 뜻하지 않는다.
- 휴일/미공표에 의한 결측, FRED의 점(.) 결측 표기, 인증 오류는 **숫자 0으로 변환하지 않는다**.
- 같은 시계열·관측일의 반복 응답은 중복 행을 만들지 않는다. 수치가 실제 수정될 때만 revision을 증가시킨다.
- 원본 CPI 지수와 계산된 YoY/MoM은 단위와 계산 정의를 분리한다.

### 3.3 macro_observation_revisions — 정정 감사

| 컬럼/제안 타입 | 내용 |
|---|---|
| id BIGSERIAL PK | 수정 이벤트 |
| series_id TEXT, observation_date DATE | 대상 관측값 |
| old_value NUMERIC(24,8), new_value NUMERIC(24,8) | 정정 전후 |
| detected_at TIMESTAMPTZ | 시스템이 정정을 확인한 시각 |
| source_updated_at TIMESTAMPTZ NULL | 공급자 공식 수정 시각(확인 가능할 경우) |
| provider_revision_ref TEXT NULL | 공식 개정 식별자 |

- 과거값 정정과 수정 시계열의 이력을 남긴다.
- 정정 이력을 수집한 최초 시점보다 과거의 **실시간 이용 가능 데이터**를 완전히 재현할 수 있다고 단정하지 않는다. 역사적 백테스트에는 발표 당시 빈티지 자료/스냅샷이 필요하다.

### 3.4 macro_release_log — 통계 발표·정정 이벤트

| 컬럼/제안 타입 | 내용 |
|---|---|
| id BIGSERIAL PK | 발표 이벤트 |
| series_id TEXT, period DATE | 발표 대상 계열·기간 |
| published_at TIMESTAMPTZ NULL | 실제 공식 발표 시각 |
| detected_at TIMESTAMPTZ NOT NULL | 시스템 확인 시각 |
| status TEXT | published, revised, not_yet_published, unverified |
| source_ref TEXT NULL | 공식 발표/정정 근거 |

- 관측일, 발표일, 수집일은 각각 독립적으로 관리한다.
- 발표일을 공급자가 제공하지 않으면 published_at은 NULL이다. fetched_at이나 detected_at을 발표 시각으로 추정해 넣지 않는다.
- 예정 발표일과 실제 발표일은 구분한다. 발표 지연/휴장도 상태로 표현한다.

### 3.5 macro_collection_state — 수집 진단

| 컬럼/제안 타입 | 내용 |
|---|---|
| series_id TEXT PK | 계열 단위 상태 |
| last_attempt_at TIMESTAMPTZ NULL | 마지막 조회 시도 |
| last_success_at TIMESTAMPTZ NULL | 마지막 성공 조회 |
| last_observation_date DATE NULL | 확보한 최신 관측일 |
| requested_from DATE NULL, requested_to DATE NULL | 마지막 요청 구간 |
| status TEXT | ok, not_published, delayed, provider_error, unverified |
| retry_after TIMESTAMPTZ NULL | 재시도 시점 |
| error_category TEXT NULL | 요청 실패 분류. 토큰·원문 응답 제외 |

- 새로운 거시 수집 작업은 기존 jobs/leases를 무단으로 덮어쓰지 않는다.
- 실패해도 직전 관측값 자체는 유지하지만, 수집 상태는 실패/지연으로 기록해 신선도를 속이지 않는다.
- 서비스 장애로 새 거시 해설이 없더라도 기존 주식 브리핑은 계속 진행할 수 있도록 한다.

### 3.6 macro_briefing_snapshots — 생성 당시 해설 보존

| 컬럼/제안 타입 | 내용 |
|---|---|
| id UUID PK | 브리핑 생성 회차 |
| briefing_date DATE NOT NULL | 한국 시간 기준 브리핑 날짜 |
| generated_at TIMESTAMPTZ NOT NULL | 실제 생성 시각 |
| classification_version TEXT NOT NULL | 해설 규칙 버전 |
| used_observations JSONB NOT NULL | 국가/계열/값/단위/관측일/발표시각/출처 스냅샷 |
| narrative JSONB NOT NULL | 근거, 반대 신호, 보류 이유, 설명 |
| quality_status TEXT NOT NULL | ok, partial, unavailable |
| briefing_body_sha256 TEXT NULL | 기존 브리핑 본문과 연결하는 무결성 표지 |

- 기존 briefings는 날짜가 PK여서 당일 재생성 시 변경될 수 있다. 신규 스냅샷은 **UUID로 회차를 구분**하고, 실제 발송 회차/본문 해시와 명시적으로 연결한다.
- 시장 해설을 과거 날짜 기준으로 조회할 때 최신 수정값으로 과거 내용을 다시 만들어내지 않는다.
- 함께 분석한 여러 시계열이 서로 다른 시각에 발표됐을 경우, 브리핑 생성 시점까지 **공식 공개된 데이터만 포함**한다.

## 4. 데이터 흐름

ECOS(한국 금리·환율), KOSIS(한국 CPI), FRED(미국 금리·CPI·국제유가·환율 보조)
→ 공급자별 어댑터
→ 단위·기준기간·원천 코드 검증
→ macro_series / macro_observations 저장
→ macro_release_log / macro_observation_revisions / macro_collection_state 반영
→ 한국·미국 거시 상황과 유가·환율 분석
→ macro_briefing_snapshots 저장
→ 기존 08:30 브리핑에서 **독립 거시경제 해설 영역** 출력

이 흐름은 **구현 목표**이며 현재 배포된 API·DB에는 반영되지 않았다.

## 5. 계산·비교 규칙

| 산출 지표 | 기준 |
|---|---|
| 한국 CPI YoY | 동일한 한국 CPI 계열의 이번 기준월/12개월 전 지수 |
| 미국 CPI YoY | CPIAUCNS와 같이 동일한 비계절조정 계열의 이번 월/12개월 전 |
| 미국 CPI MoM | CPIAUCSL과 같이 동일한 계절조정 계열의 이번 월/전월 |
| 근원 CPI | 식료품·에너지 제외 지수. 국가별 정의·계절조정 여부 구분 |
| 한미 정책금리 격차 | 한국 기준금리 - 미국 목표금리 범위: [한국-미국 상한, 한국-미국 하한] %p |
| 한미 국채 10년물 | 동일 관측일 확인된 수치끼리만 차이(%p) 계산 |
| WTI/Brent 변화 | 동일 원천/단위의 직전과 현재 관측치, 기간·관측일 명시 |
| 원/달러 변화 | KRW/1 USD, **상승은 원화 약세**. 공급자/고시 기준 통일 |

월별 한국/미국 CPI의 발표월이 다르면 바로 비교하지 않고 서로 다른 기준월이라고 표시한다. 유가 상승이 반드시 주가 악재 또는 물가 상승으로 연결된다고 단정하지 않는다. 국면별 고정 임계값이나 주식 매매 점수 가중치는 아직 정하지 않았다.

## 6. 인덱스·제약 제안

- macro_series: series_id PK, country + indicator 조회 인덱스, 세부항목 포함 출처별 논리 유일성 검토.
- macro_observations: (series_id, observation_date) PK, 계열별 최신일 조회.
- macro_observation_revisions: (series_id, observation_date, detected_at) 인덱스.
- macro_release_log: (series_id, period, detected_at) 인덱스.
- macro_collection_state: series_id PK, retry_after 인덱스 선택.
- macro_briefing_snapshots: (briefing_date, generated_at DESC) 인덱스, 동일 날짜 다중 생성 허용.
- 수치 단위와 계절조정 여부를 검증하고, 유가 및 환율 값을 CPI와 같은 숫자 범위로 해석하지 않는다.

## 7. 적용 순서 및 안전성

1. 기존 PostgreSQL 일관된 백업과 비밀키/인증 복원 경로 확인.
2. ECOS·KOSIS 통계표/항목 코드, FRED 시계열, 데이터 제공 조건의 실제 검증.
3. 기존 데이터·스키마 수정 없이 신규 macro_* 테이블을 추가하는 버전 관리 마이그레이션 작성.
4. 필요한 초기 역사 구간만 백필한 뒤 일별 금리·유가·환율과 월별 CPI를 증분 수집.
5. 별도 품질 검사 후 브리핑 해설로만 연결하고 기존 매매 로직·발송 중복방지 정책을 그대로 유지.
6. 공급자 오류/시차/정정의 복구 경로를 확인하고 수집 오류 시 거시 영역만 비활성화.
7. 통계 개정 전후·브리핑 발송 당시 스냅샷의 참조 일관성을 확인.

**주의:** 실제 SQL 마이그레이션, API 구현, DB 데이터 수집, 테스트 코드는 이 문서 갱신 요청 범위에 포함하지 않는다.
