# Snowflake 데이터소스 전환 설계

> 작성: 2026-03-25
> ※ **개발용 참조 문서** — 운영담당자(주간갱신)는 읽지 않아도 됩니다. 쿼리·캐시 구조 변경 시에만 참조.
> 상태: 파라미터 확정 대기

## 개요

현재 로컬 엑셀 파일 → Snowflake 직접 조회로 전환.
Step 0에서 설정한 브랜드/시즌을 파라미터로 Snowflake 쿼리 실행.

### 환경별 연결 방식
| 환경 | 연결 방식 |
|------|----------|
| 개발 (Claude Code) | MCP → Snowflake |
| 배포 (FastAPI 서버) | `snowflake-connector-python` → Snowflake (직접) |

### 호출 원칙
- **조회 최소화**: 동일 데이터 중복 조회 금지 — Step 0에서 1번만 조회
- **Pre-fetch**: Step 0 설정 저장 시 4개 쿼리 **병렬 실행** → 메모리 캐시
- **Step 1~5 즉시 로딩**: 메모리에서 반환 (Snowflake 재호출 없음)
- **사용성 목표**: Step 0 저장 시 1회 로딩 대기 → 이후 Step 이동 시 로딩 없음

### 캐시 전략 (3-tier)
```
1차: 서버 메모리 (최우선, 즉시 반환)
2차: parquet 파일 (서버 재시작 시 복구용)
3차: Snowflake 재조회 (1차+2차 없을 때)
```

### 호출 흐름
```
Step 0 설정 저장 (브랜드/시즌/KPI)
    ↓
POST /api/brand-config
    ↓
백엔드: 메모리 캐시 클리어 → Snowflake 4개 쿼리 병렬 실행
    ↓
메모리 캐시 저장 + parquet 2차 캐시 저장
    ↓
Step 1~5 진입 → 메모리에서 즉시 반환 (조회 없음)
```

### 캐시 무효화
- Step 0에서 브랜드/시즌 변경 → 메모리 + parquet 모두 클리어 → 재조회
- UI "새로고침" 버튼 → 강제 재조회

### 2차 캐시(parquet) 위치
```
data/{brand}/{season}/
  ├── season_raw.parquet
  ├── weekly_raw.parquet
  ├── similarity_mapping.parquet
  └── size_data.parquet
```

---

## 공통 파라미터 (Step 0에서 설정)

| 파라미터 | config_loader 함수 | 예시값 | 비고 |
|---------|-------------------|--------|------|
| `brand` | `get_brand()` | 'MLB' | 브랜드코드: M=MLB, X=Discovery, V=Duvetica, I=MLB KIDS |
| `base_season` | `get_base_season()` | '25F' | 분석 대상 시즌 (전 시즌 실적) |
| `target_season` | `get_target_season()` | '26F' | 발주 계획 시즌 |
| `season_type` | config에서 파생 | 'FW' | SS or FW (시즌 유형) |

---

## D1: 시즌마감실적 (season_raw)

### 데이터 성격
시즌 종료 후 스타일별 최종 발주/입고/판매/재고 집계. Step 1 시즌마감분석의 핵심 입력.

### 현재 소스
`data/{brand}/{season}/season_raw.xlsx` → 1개 시트, ~500~2,000행

### 사용처 (3곳)
| 스크립트 | 용도 |
|---------|------|
| `main.py` | Step 1 전체 분석 (등급, BCG, KPI) |
| `step4_integration.py` | ITEM→ITEM_NM 룩업 테이블 |
| `generate_color_mapping.py` | PART_CD→CLASS2 룩업 |

### 필요 컬럼

| 컬럼 | 타입 | 설명 | 필수 |
|------|------|------|------|
| SEASON_GB | str | 시즌 구분 ('당해'/'전년') | O |
| CLASS1 | str | 대분류 ('의류'/'용품'/'신발') | O |
| CLASS2 | str | 중분류 (Inner/Bottom/Outer) | O |
| ITEM | str | 아이템 코드 (약어) | O |
| ITEM_NM | str | 아이템명 (T-shirt, Pants 등) | O |
| PART_CD | str | 스타일코드 (품번) | O |
| ORDER_QTY_KR | int | 국내 발주수량 | O |
| STOR_QTY_KR | int | 국내 입고수량 | O |
| SALE_QTY_CNS | int | 소비자 판매수량 | O |
| STOCK_QTY_KR | int | 재고수량 (없으면 입고-판매로 계산) | △ |
| TAG_PRICE | int | 정가 (매출액 계산용) | O |
| PRDT_NM | str | 상품명 | △ |

### 쿼리 필터 조건

```sql
WHERE brand_cd = '{brand}'              -- Step 0 설정
  AND season = '{base_season}'          -- Step 0 설정
  -- SEASON_GB는 데이터 내 '당해'/'전년' 구분 → 코드에서 필터
```

### 코드 적용 필터 (쿼리 후)
- `SEASON_GB == '당해'` → 당해 시즌만 (전년은 비교용)
- `CLASS1 contains '의류'` → 용품/신발 제외
- `ORDER_QTY > 0` → 기획만 있고 생산 안 된 스타일 제외

---

## D2: 주차별실적 (weekly_raw)

### 데이터 성격
주차별 스타일×컬러 단위 입고/판매/재고 시계열. Step 2 시계열분석 + Step 3 기회비용의 핵심 입력.

### 현재 소스
`data/{brand}/{season}/weekly_raw.xlsx` → 1개 시트, ~5,000~50,000행

### 사용처 (3곳)
| 스크립트 | 용도 |
|---------|------|
| `weekly_analysis.py` | Step 2 시계열 패턴 분석 + AI 진단 |
| `ai_sales_loss_v2.py` | Step 3 기회비용 계산 |
| `generate_color_mapping.py` | 컬러코드 추출 |

### 필요 컬럼

| 컬럼 | 타입 | 설명 | 필수 |
|------|------|------|------|
| PERIOD | str | '당해'/'전년' | O |
| CLASS1 | str | 대분류 ('의류') | O |
| CLASS2 | str | 중분류 | O |
| ITEM_NM | str | 아이템명 | O |
| PART_CD | str | 스타일코드 | O |
| COLOR_CD | str | 컬러코드 (예: BKS, WHM) | O |
| END_DT | date | 주차 종료일 (YYYY-MM-DD) | O |
| STOR_QTY_KR | int | 주차별 국내 입고수량 | O |
| SALE_QTY_CNS | int | 주차별 소비자 판매수량 | O |
| STOCK_QTY_KR | int | 주말 재고수량 | O |
| TAG_PRICE | int | 정가 | △ |
| ORDER_QTY_KR | int | 주차별 발주수량 (없으면 STOR 대체) | △ |
| PRDT_NM | str | 상품명 | △ |

### 쿼리 필터 조건

```sql
WHERE brand_cd = '{brand}'
  AND season = '{base_season}'
  -- PERIOD는 데이터 내 구분 → 코드에서 필터
```

### 코드 적용 필터 (쿼리 후)
- `PERIOD == '당해'` → 당해 시즌만
- `CLASS1 contains '의류'` → 용품/신발 제외
- `STOR_QTY_KR > 0` (스타일 단위) → 입고 실적 없는 품번 제외
- 아웃라이어 제거: `EXCLUDE_STYLES = ['3ATSB3054']` (하드코딩)

### 데이터 볼륨 참고
- D1 대비 **10~25배** 큰 데이터 (주차×컬러 단위)
- 캐시 포맷: parquet 권장 (CSV/JSON보다 로드 속도 빠름)

---

## D3: 유사스타일매핑 (similarity_mapping)

### 데이터 성격
ML 모델이 산출한 차시즌 신규 스타일 ↔ 전 시즌 유사 스타일 매핑 결과. Step 4 발주추천의 참조(Ref) 스타일 소스.

### 현재 소스
`data/{brand}/{season}/similarity_mapping.xlsx` → 2개 시트 (Result 1, Result 2), ~100~500행

### 사용처 (1곳)
| 스크립트 | 용도 |
|---------|------|
| `step4_integration.py` | 유사 스타일 매핑 + D2 실적 머지 → 발주추천 |

### 필요 컬럼

| 컬럼 | 타입 | 설명 | 필수 |
|------|------|------|------|
| BRD_CD | str | 브랜드코드 | O |
| NEW_SEASON | str | 차시즌 코드 ('26F') | O |
| NEW_STYLE | str | 신규 스타일코드 | O |
| SIMILAR_STYLE | str | 유사 스타일코드 (전 시즌) | O |
| SIMILAR_STYLE_SEASON | str | 유사 스타일 시즌 | △ |
| RANKING | int | 유사도 순위 (1=최고, 최대 3) | O |
| ITEM | str | 아이템 약어 | O |
| CAT_NM_ENG | str | 카테고리 영문명 → CLASS2 매핑용 | O |
| CAT_NM | str | 카테고리 한글명 | △ |
| PRDT_NM | str | 상품명 | △ |

### 쿼리 필터 조건

```sql
WHERE brand_cd = '{brand}'
  AND new_season = '{target_season}'    -- 차시즌 기준
  AND ranking <= 3                      -- Top 3만
```

### 코드 적용 필터 (쿼리 후)
- `REF_SCORE >= 0.50` (MIN_SCORE) → 최소 유사도 필터

### Long → Wide 변환
- DB에서 Long 포맷(1행=1매핑)으로 조회
- 코드에서 Wide 포맷으로 피벗 (NEW_STYLE별 REF_1/2/3)
- 이 변환 로직은 `step4_integration.py`에 이미 구현되어 있음

---

## D4: 사이즈별실적 (fw_size_data)

### 데이터 성격
시즌별 스타일×컬러×사이즈 단위 발주/판매 실적. Step 5 사이즈 배분 최적화의 입력.

### 현재 소스
`data/{brand}/{season}/fw_size_data.xlsx` → "Result 1" 시트, ~2,000~20,000행

### 사용처 (1곳)
| 스크립트 | 용도 |
|---------|------|
| `generate_size_data.py` | 사이즈별 판매 비중 → 배분 기준 산출 |

### 필요 컬럼

| 컬럼 | 타입 | 설명 | 필수 |
|------|------|------|------|
| period | str | '당해'/'전년' (첫 번째 컬럼) | O |
| SEX_NM | str | 성별 ('공용'/'M'/'F') | O |
| CLASS2 | str | 중분류 (Outer/Inner/Bottom) | O |
| CAT_NM | str | 카테고리명 (패딩, 맨투맨 등) | O |
| SUB_CAT_NM | str | 서브카테고리 (숏패딩, 롱패딩 등) | O |
| ITEM | str | 아이템 코드 | O |
| ITEM_NM | str | 아이템명 | O |
| COLOR_CD | str | 컬러코드 → COLOR_RANGE 매핑 | O |
| SIZE_CD | str | 사이즈 (XS/S/M/L/XL/XXL/2XL) | O |
| ORDER_QTY_KR | int | 국내 발주수량 | O |
| SALE_QTY_KR | int | 국내 판매수량 | O |

### 쿼리 필터 조건

```sql
WHERE brand_cd = '{brand}'
  AND season = '{base_season}'
  -- period는 데이터 내 구분 → 코드에서 분리
```

### 코드 적용 필터 (쿼리 후)
- `period` 컬럼으로 당해/전년 분리
- `SALE_QTY_KR > 0` → 판매 없는 행 제외
- GROUP BY: SEX_NM, CLASS2, CAT_NM, SUB_CAT_NM, ITEM, ITEM_NM, COLOR_RANGE, SIZE_CD
- COLOR_CD → COLOR_RANGE 매핑은 `public/color_mapping.json`에서 로드 (D5 분리)

---

## D5: 컬러그룹매핑 (color_mapping) — MCP 제외

### 처리 방식
- **Snowflake 조회 안 함** — 기준정보이므로 `public/color_mapping.json`으로 관리
- 초기값: 현재 엑셀(FNF_GROUP_COLOR_*.xlsx) 304개 코드 → 14개 그룹
- 유저가 Step 5 UI에서 미매핑 컬러 추가/수정 → `POST /api/color-mapping` → JSON 저장
- `config_loader.py`에 `get_color_mapping()` 추가

---

## 서버 데이터 로딩 레이어 (server/api.py 확장)

### Pre-fetch 구조

```python
# server/api.py

DATA_CACHE = {}  # {"d1": DataFrame, "d2": DataFrame, "d3": DataFrame, "d4": DataFrame}

@app.post("/api/brand-config")
async def save_brand_config(config):
    save_config(config)
    DATA_CACHE.clear()                  # 설정 변경 → 메모리 캐시 초기화
    await prefetch_all(config)          # 4개 쿼리 병렬 실행
    return {"status": "ok"}

async def prefetch_all(config):
    """Step 0 저장 시 4개 데이터 병렬 조회 → 메모리 + parquet 캐시"""
    brand = config["brand"]
    season = config["baseSeason"]

    # 1차: parquet 캐시 존재하면 로드 (서버 재시작 복구)
    # 2차: 없으면 Snowflake 조회 → 메모리 + parquet 저장
    queries = {
        "d1": load_query("d1_season_raw.sql", brand=brand, base_season=season),
        "d2": load_query("d2_weekly_raw.sql", brand=brand, base_season=season),
        "d3": load_query("d3_similarity_mapping.sql", brand=brand, target_season=target),
        "d4": load_query("d4_size_data.sql", brand=brand, base_season=season),
    }
    # 병렬 실행 → DATA_CACHE에 저장

def get_data(key: str) -> pd.DataFrame:
    """Step 1~5에서 호출 — 메모리에서 즉시 반환"""
    if key in DATA_CACHE:
        return DATA_CACHE[key]          # 즉시
    # fallback: parquet → Snowflake 재조회
```

### SQL 파일 로드

```python
def load_query(filename: str, **params) -> str:
    """queries/ 폴더에서 SQL 파일 읽기 + 파라미터 치환"""
    sql_path = os.path.join(BASE_DIR, "queries", filename)
    with open(sql_path, "r") as f:
        template = f.read()
    return template.format(**params)
```

---

## 구현 순서 (제안)

| 단계 | 작업 | 의존성 |
|------|------|--------|
| 1 | D5 컬러매핑 JSON 분리 | 없음 (독립 진행 가능) |
| 2 | 데이터 로딩 레이어 구현 (Pre-fetch + 3-tier 캐시) | Snowflake 테이블 구조 확인 필요 |
| 3 | D1 시즌마감실적 전환 | Snowflake 쿼리 파라미터 확정 |
| 4 | D2 주차별실적 전환 | 대용량 처리 검증 |
| 5 | D3 유사스타일매핑 전환 | ML 파이프라인 Snowflake 적재 확인 |
| 6 | D4 사이즈별실적 전환 | D5 완료 후 |
| 7 | Step 0 UI → Pre-fetch 트리거 연결 + 로딩 인디케이터 | 전체 통합 테스트 |

---

## TODO
- [ ] Snowflake 테이블명/스키마 확인 (엔지니어팀 또는 Chacha 확인)
- [ ] 쿼리 파라미터 최종 확정 (사용자와 함께)
- [ ] Snowflake 연결 프로토타입 (snowflake-connector-python)
- [ ] D5 컬러매핑 JSON 분리 (선행 가능)
