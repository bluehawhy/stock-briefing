# API 명세 및 외부 경제지표 연동 설계

- 저장소: `bluehawhy/stock-briefing`
- 문서 기준: 2026-11-13, `main` 코드와 [거시경제 설계](macro-indicators-design.md) 대조
- **구현 상태:** 1~2절의 기존 HTTP 라우트는 소스에서 확인한 **현재 구현**, 3~7절의 거시경제 API 및 확장은 **설계안(미구현)**이다.
- 연계 문서: [DB 스키마](database.md) · [구현/운영](implementation.md) · [영상 연구 요약](research/fear-greed-2026-video-summary.md)
- 정책: 국내주식 종목 분석·제안 수량은 유지하고 한·미 금리·물가·유가·환율 분석은 **별도 설명 영역**으로 제공한다. 실주문 API는 제공하지 않는다.

## 1. 현재 구현된 API / 호출 경로

FastAPI 진입점은 `app/main.py`이며 `app/kakao.py`, `app/registration.py`, `app/dashboard.py` 라우터를 포함한다. **아래 경로만 기존 코드에서 확인했다.** 서버 배포 시 프록시 기본 경로는 배포 설정에 따라 달라질 수 있으므로 운영 URL을 이 문서에서 추측하지 않는다.

| 메서드 | 경로 | 용도 | 보호 조건 |
|---|---|---|---|
| GET | `/healthz` | DB 연결 확인, DB 종류·정기 스케줄러 상태 | 소스상 별도 인증 의존성 없음; 프록시 공개 정책 별도 |
| POST | `/kakao/skill/{secret}` | 카카오 스킬 요청을 공통 명령 처리기로 전달 | URL 시크릿 검증, 허용 사용자 검사 |
| GET / POST | `/broker/register/{token}` | 한국투자증권 API 키·계좌 등록 화면 및 제출 | 5분 일회용 링크, HTTPS origin 검증, 크기/입력 제한, 암호화 저장 |
| GET | `/stock-briefing/` | 개인 대시보드 | 로그인 쿠키 |
| GET | `/stock-briefing/login` | 대시보드 로그인 화면 | 비밀번호 설정·전송 보안 |
| POST | `/stock-briefing/api/login` | 로그인·12시간 쿠키 발급 | 동일 출처 헤더 및 속도 제한 |
| POST | `/stock-briefing/api/logout` | 세션 종료 | 동일 출처 헤더 |
| GET | `/stock-briefing/api/overview` | 최신 브리핑·관심종목·코스피 상태·작업 상태 | 세션 쿠키 |
| GET | `/stock-briefing/api/history` | 브리핑 이력(기준일 커서 `before`) | 세션 쿠키 |
| GET | `/stock-briefing/api/history/{day}` | 해당 날짜의 저장된 브리핑 | 세션 쿠키 |
| POST | `/stock-briefing/api/refresh` | 기존 브리핑 갱신 작업 접수(202) | 세션 + 동일 출처 |
| POST | `/stock-briefing/api/watchlist` | 관심종목 추가(`{"query":"종목명 또는 코드"}`) | 세션 + 동일 출처 |
| DELETE | `/stock-briefing/api/watchlist/{symbol}` | 관심종목 제거 | 세션 + 동일 출처 |

- 근거 파일: `app/main.py`, `app/dashboard.py`, `app/registration.py`, `app/kakao.py`.
- 텔레그램은 별도 공개 웹훅이 아니라 `TELEGRAM_POLLING_ENABLED=1`에서 단일 프로세스가 Bot API를 폴링한다. 채널별 명령·버튼·확인 처리기는 `app/services.py`와 `app/commands.py`에서 공유한다.
- 대시보드 API는 `DASHBOARD_PASSWORD` 최소 12자, 허용된 HTTPS 전송(예외 설정은 코드 참고), 서명된 로그인 쿠키, 변경 요청의 동일 출처 검사를 사용한다. 신규 API도 같은 보호를 유지해야 한다.
- 실제 주식 주문 API는 구현하지 않으며, 주문 요청은 **제출 불가 미리보기**이다.

### 1.1 현재 수집 공급자 (기존 기능)

| 영역 | 공급자·기능 | 구현 상태 |
|---|---|---|
| 계좌/수급/일봉 | 한국투자증권 개인 REST: 인증, 거래소 일정, 수정주가 일봉, 수급, 추적 계좌 잔고 | 구현, 실계좌/권한 검증은 운영 별도 |
| 관심종목·코스피 | 공개 일봉·코스피 및 지표 분석 | 구현 |
| 미래에셋 | 대체 어댑터 자리 | 지원 불가 안내; 실제 연동 미구현 |
| 정기 발송 | API 내부 스케줄러(08:30 Asia/Seoul) → 저장 → 텔레그램 | 구현; 일자별 중복 전송 방지 |

기존 공개 데이터/증권사 어댑터와 신규 거시경제 수집 어댑터는 **분리**한다.

## 2. 공통 API 계약 및 상태 처리

- 응답은 비밀키·토큰·계좌번호·공급자 URL 내 인증값을 포함하지 않는다.
- 저장 시각과 관측일·발표일이 다른 개념이므로 날짜만 있는 시계열과 시각이 있는 발표 기록을 혼동하지 않는다.
- `404`(정보 없음), `401`(세션 없음), `403`(출처·접근 거부), `429`(요청 제한), `5xx`(서버 오류)는 기존 의미를 유지한다. 신규 거시경제 수집 장애는 가급적 **기존 주식 브리핑의 전체 실패로 전파하지 않는다**.
- 기존 `/api/refresh`는 *전체 브리핑 갱신 접수*이며, 앞으로 제안하는 거시경제 전용 갱신 라우트와 동일하게 가정하지 않는다.
- 별도 공개 무인증 API나 제삼자 서비스용 키 발급은 이번 범위에 포함하지 않는다.

## 3. 신규 외부 거시경제 API (모두 미구현)

### 3.1 한국은행 ECOS — 한국 금리·원/달러

| 논리 지표 | 공급자 | 계열·식별자 |
|---|---|---|
| 한국은행 기준금리 | ECOS | `722Y001` 계열 **후보**, 상세 항목 확인 필요 |
| 국고채 3년·10년 수익률 | ECOS | 메타데이터로 통계표·항목 코드 확인 후 확정 |
| 원/달러 | ECOS | `731Y003` **후보**, 고시 유형·항목 확인 필요 |

- 공식 문서: [한국은행 ECOS OpenAPI](https://ecos.bok.or.kr/api/#/)
- 통계표/항목 검색 → 시계열 코드 확정 → 기간별 `StatisticSearch` 조회 → 정규화 → 저장의 순서로 설계한다.
- 계약 필드: `provider_series_id`, `item_code`, `observation_date`, `value`, `unit`, `frequency`, `source_updated_at?`, `fetched_at`.
- 금리·환율의 관측 시점, 기준금리의 적용일, 공시일이 서로 다를 수 있다. 별도 일시를 제공하지 않으면 `null/미확인`을 유지한다.

### 3.2 KOSIS — 한국 CPI·근원 CPI

- 공식 문서: [KOSIS 통계 API](https://kosis.kr/serviceInfo/openAPIGuide.do)
- 수집: 국내 소비자물가조사의 **총지수** 및 **식료품·에너지 제외 근원지수**. 농산물·석유류 제외 지수와 섞지 않는다.
- 세부 통계표 ID·분류·항목·기준연도/계절조정 여부는 실제 API 메타데이터를 확인한 뒤 확정한다. **미검증 코드값을 설정 기본값으로 사용하지 않는다.**
- 최근 월뿐 아니라 최소 전년 동월과 추세 계산에 필요한 선행 구간을 받는다. 공개되지 않은 달을 결측으로 오판하지 않는다.
- YoY는 같은 종류의 지수로 계산한다. 한국 전월비는 공식 발표 정의와 계절조정 여부를 명확히 표시한다.

### 3.3 FRED — 미국 금리·물가 + 국제유가

- 공식 문서: [FRED API](https://fred.stlouisfed.org/docs/api/fred/)
- 기본 조회 기능: `fred/series/observations` 시계열 기간 조회. 호출 파라미터(예: `series_id`, `observation_start`, `observation_end`, `api_key`, `file_type=json`)는 실제 연동에서 검증한다.
- 빈 값 `.`, 공급자 오류, 통계 중단, 수정 공표를 각각 구분한다. `.`을 `0`으로 변경하지 않는다.

| 범주 | 시계열 ID | 의미 | 단위/주기 |
|---|---|---|---|
| 미국 정책금리 | `DFEDTARL`, `DFEDTARU` | 연준 목표금리 하한/상한 | % / 일별 유효값 |
| 미국 국채금리 | `DGS2`, `DGS10` | 미 국채 2년/10년 | % / 영업일 |
| 미국 헤드라인 CPI | `CPIAUCNS` | 비계절조정 지수(YoY용) | 지수 / 월별 |
| 미국 헤드라인 CPI | `CPIAUCSL` | 계절조정 지수(MoM용) | 지수 / 월별 |
| 미국 근원 CPI | `CPILFESL` | 식료품·에너지 제외, 계절조정 지수 | 지수 / 월별 |
| 국제유가 | `DCOILWTICO` | WTI 현물 | USD/배럴 / 일별 |
| 국제유가 | `DCOILBRENTEU` | 브렌트 현물 | USD/배럴 / 일별 |
| 환율 보조 | `DEXKOUS` | 원/달러 참고값(한국 ECOS 대조) | KRW/1 USD / 일별 |

`FEDFUNDS`는 과거 영상에 사용된 **실효금리** 계열로, 목표 범위(`DFEDTARL/DFEDTARU`)와 다르다. 목표 범위 추세 분석에서 대신 사용하지 않는다.

### 3.4 공급자 어댑터 설계

```text
ECOSAdapter / KOSISAdapter / FREDAdapter
  └─ fetch_series(series_id, start_date, end_date)
       -> series metadata + observations + provider status
              └─ Normalize(country, indicator, frequency, unit,
                            seasonal_adjustment, observation_date, value,
                            publication_time?, fetched_at)
                      └─ PostgreSQL macro_*
```

- 외부 응답 모델은 공급자마다 다르므로 원본 필드를 외부 HTTP 응답에 그대로 노출하지 않는다.
- 인증 키는 운영 환경변수/시크릿에 저장: `ECOS_API_KEY`, `KOSIS_API_KEY`, `FRED_API_KEY` (**제안 이름; 실제 설정 미구현**).
- 외부 요청에는 제한된 재시도·백오프·타임아웃 및 공급자별 쿼터 준수를 적용한다. 개인정보·인증값을 URL 로그·애플리케이션 로그에 남기지 않는다.

## 4. 신규 내부 API 초안 (모든 경로 미구현)

기존 개인용 대시보드 라우터 하위에서 제공하며, **현재 접근 가능한 경로가 아니다**. 현재 구현된 `GET /stock-briefing/api/overview`를 깨지 않도록 별도 경로를 예약하는 방식이다.

| 메서드 | 제안 경로 | 요청 | 응답 목적 |
|---|---|---|---|
| GET | `/stock-briefing/api/macro/overview` | 선택 없음 | 한국·미국 금리·물가, 유가·환율 최신 비교 스냅샷 |
| GET | `/stock-briefing/api/macro/series/{series_id}` | `start`, `end` 선택 | 허용된 계열의 구간별 관측값과 단위/기준일 |
| GET | `/stock-briefing/api/macro/status` | 선택 없음 | 공급자별 최근 수집일·발표일·지연/실패 상태 |
| POST | `/stock-briefing/api/macro/refresh` | 비어 있는 본문 또는 범위 지정(검토) | 비동기 수집 작업 접수(202); 중복 작업 방지 |

**공통 보안:** 현재 대시보드와 동일한 로그인 쿠키, HTTPS, `same_origin` 검사(변경 요청), API별 허용 계열 목록과 기간/결과량 제한. 테이블명·정규화 SQL·외부 URL을 클라이언트가 임의 지정하지 못하게 한다.

### 4.1 제안 응답 예시 — 값은 가상/자리표시자

아래는 **향후 응답 구조의 예**이며 현재 서비스에서 호출할 수 없고 실제 통계값도 아니다.

```json
{
  "as_of": "YYYY-MM-DDT08:30:00+09:00",
  "status": "partial",
  "countries": {
    "KR": {
      "policy_rate": {"value": null, "unit": "percent", "observation_date": null, "source": "ECOS", "status": "not_collected"},
      "cpi_yoy": {"value": null, "unit": "percent", "period": null, "source": "KOSIS", "status": "not_collected"}
    },
    "US": {
      "policy_rate_target": {"lower": null, "upper": null, "unit": "percent", "source": "FRED", "status": "not_collected"},
      "cpi_yoy": {"value": null, "unit": "percent", "period": null, "source": "FRED", "status": "not_collected"}
    }
  },
  "global": {
    "wti": {"value": null, "unit": "USD/barrel", "status": "not_collected"},
    "brent": {"value": null, "unit": "USD/barrel", "status": "not_collected"},
    "usdkrw": {"value": null, "unit": "KRW/USD", "status": "not_collected"}
  },
  "missing": ["미수집"],
  "note": "실제 구현 시 항목마다 발표일·관측일·수집일 및 출처를 포함"
}
```

## 5. 내부 명령·스케줄러 연결 설계 (미구현)

- 기존 텔레그램/카카오 공통 명령 `오늘 브리핑`에서 생성되는 **주식 분석**을 유지하고, 새 **거시경제 해설**을 별도 절로 붙인다. 새 명령이 필요하면 `거시경제` 또는 `시장지표`를 추후 정식 명령으로 결정한다.
- 새 자료 수집은 공통 스케줄러/작업 큐와 충돌하지 않도록 별도 lease 및 공급자/시계열별 중복 제어를 설계한다. 기존 `delivery_runs`의 당일 중복 발송 보호는 유지한다.
- `08:30 KST` 이전에 FRED/BLS/KOSIS의 신규 발표가 완료됐다고 가정하지 않는다. 브리핑은 해당 시각 **공개·검증된 마지막 값**을 사용하고, 이전 발표월과 수집 시각을 표시한다.
- 미국 데이터·유가 등의 지연, 공급자 장애 또는 현시점 정보 누락은 거시 절에서 `데이터 확인 필요`로 표시한다. 전체 국내주식 매수·매도 의견을 임의로 변경하거나 지난 거시 해설을 '당일 최신'으로 재사용하지 않는다.
- 해설 내용/사용 자료를 생성 당시 스냅샷으로 보존하여 과거 브리핑 조회 시 최신 자료로 바뀌지 않게 한다.
- 데이터 흐름과 DB 테이블 초안은 [database.md](database.md)에 정의한다.

## 6. 거시경제 계산 API 계약(논리 모델, 미구현)

| 계산 | 입력 | 처리 원칙 |
|---|---|---|
| 한국·미국 CPI YoY | 같은 통계 계열의 당월/12개월 전 지수 | `(현재/12개월 전 - 1) × 100`; 통계 기준변경/재기준화 확인 |
| CPI MoM | 전월/당월 지수 | 계절조정 여부를 구분해 표시 |
| 한국-미국 정책금리차 | 한국 금리, 미국 목표 하한·상한 | 차이 범위: `[한국-미국 상한, 한국-미국 하한]` %p |
| 국채 10년물 차 | 한국·미국 10년물 | 같은 관측일에 대해서만 차이 산출; 단위 %p |
| 유가·환율 변동 | 동일 공급자의 각 비교 시점 가격 | 기간 정의·두 관측일·변동률을 함께 저장 |
| 거시 국면 서술 | 위 변수의 기간별 변화 | 임계값 및 매매 가중치 **미확정**. 국면 단정 대신 근거+반대신호 설명 |

영상의 5개 국면은 **해설 참고 틀**일 뿐, 과거 관측으로 예측력을 검증하기 전에는 API 응답에 자동 점수로 노출하지 않는다.

## 7. 데이터 품질·운영 수용 기준(구현 시 검증할 사항)

1. 국가·단위·시계열 ID·계절조정 여부·관측일·발표일(가능한 경우)·수집 시각이 저장/표시된다.
2. CPI의 미발표 월, 주말의 시장금리/유가/환율 공백, 빈 데이터와 공급자 장애를 구분한다.
3. 발표 뒤 수정된 값이 있을 때 과거 발송 브리핑의 원본 수치·판단이 덮어씌워지지 않는다.
4. 같은 계열·관측일의 반복 수집은 중복 데이터가 생기지 않으며, 실제 수정사항은 이력으로 보존한다.
5. 한국은행·KOSIS·FRED 키를 노출하지 않으며, 기존 로그인·권한 경계가 유지된다.
6. 거시경제 공급자 한 곳의 장애 때문에 기존 주식 종목별 브리핑/로그인/기존 API가 중단되지 않는다.
7. 현 시점에서 검증되지 않은 공급자 항목 매핑이나 결측은 `미확인`으로 처리하고 추정 숫자를 만들지 않는다.

> 구현·배포·DB 마이그레이션·테스트는 별도 작업이다. 본 변경은 API 문서 정리에 한한다.
