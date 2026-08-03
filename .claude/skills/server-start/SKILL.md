---
name: server-start
description: order-ai-share 백엔드(uvicorn) + 프론트엔드(vite) 기동 + 브라우저 자동 띄우기 + 5 step UI 체크리스트 안내 + 종료 방법 안내. /run-pipeline 후속, 운영 마지막 단계.
---

# server-start

분석 산출물 (`public/dashboard_data.json` 등) 이 준비된 상태에서 백엔드/프론트엔드를 띄우고 브라우저를 열어 운영자가 5 step UI 를 확인할 수 있게 하는 스킬. 4-스킬 체인의 마지막 단계.

## 적용 시점 (trigger)

- "서버 띄울게", "화면 열어줘", "UI 확인"
- `/run-pipeline` 완료 후 다음 단계
- 단순 재기동 (데이터/임계값 변화 없이)

## 절차 (순서대로 엄격히 진행)

### Stage 0 — 의존성 점검

**Bash** 로 묶어 확인:

```bash
test -f public/dashboard_data.json && echo "dashboard:OK" || echo "dashboard:MISSING"
test -f data/production/order_ai.duckdb && echo "duckdb:OK" || echo "duckdb:MISSING"
test -x .venv/bin/uvicorn && echo "uvicorn:OK" || echo "uvicorn:MISSING"
test -d apps/lite/node_modules && echo "node_modules:OK" || echo "node_modules:MISSING"
```

**미충족 시 즉시 종료**:
- `dashboard:MISSING` 또는 `duckdb:MISSING` → "분석 파이프라인 미실행. **`/run-pipeline`** 먼저 호출하세요."
- `uvicorn:MISSING` → "venv 손상 또는 미생성. **`/onboard`** 로 재셋업."
- `node_modules:MISSING` → "프론트 의존성 미설치. **`/onboard`** 가 `npm install` 수행."

### Stage 1 — 포트 점검 (8000)

**Bash**: `lsof -nP -iTCP:8000 -sTCP:LISTEN 2>/dev/null | tail -n +2`

**점유 중인 경우**:
- 점유 프로세스 정보 출력
- 사용자에게 안내 후 즉시 종료:
  ```
  포트 8000 이미 사용 중. 다음 중 하나로 해결하세요:
    - 명령어: pkill -f "uvicorn server.api:app"
    - 자연어: Claude Code 에게 "서버 종료해줘"
  종료 후 본 스킬 다시 호출.
  ```

5173 은 점검 안 함 — vite 가 자동으로 다음 포트 사용 (Stage 3 에서 동적 추출).

### Stage 2 — 백엔드 기동 (background) + 헬스체크

**Bash (run_in_background=true)**:
```bash
cd <project_root> && .venv/bin/uvicorn server.api:app --port 8000 > /tmp/orderai_backend.log 2>&1
```

Background ID 캡처. 사용자에게 "백엔드 기동 중..." 안내.

**5초 대기 후 헬스체크**:
```bash
sleep 5 && curl -fs http://localhost:8000/api/health
```

**응답 진단**:
| 결과 | 진단 |
|---|---|
| `{"status":"ok"}` (또는 비슷한 JSON) | [OK] 백엔드 기동 완료 → Stage 3 |
| `connection refused` / curl fail | 로그 (`/tmp/orderai_backend.log`) 마지막 20줄 출력. 일반적 패턴:<br>• `Address already in use` → 포트 충돌 (Stage 1 회피된 race)<br>• `ModuleNotFoundError` → venv 파손, `/onboard` 재셋업 권장<br>• 그 외 → 사용자에게 진단 요청 |

백엔드 fail 시 Stage 3 진입 X.

### Stage 3 — 프론트엔드 기동 (background) + 포트 동적 추출

**Bash (run_in_background=true)**:
```bash
cd <project_root>/apps/lite && npm run dev > /tmp/orderai_frontend.log 2>&1
```

Background ID 캡처. "프론트엔드 기동 중..." 안내.

**8초 대기 후 포트 추출**:
```bash
sleep 8 && grep -oE 'Local:\s+https?://localhost:[0-9]+' /tmp/orderai_frontend.log | head -1 | grep -oE '[0-9]+$'
```

**결과 처리**:
| 결과 | 다음 행동 |
|---|---|
| 포트 숫자 추출됨 (예: `5173`, `5174`) | 그 포트 사용 → Stage 4 |
| 추출 실패 (빈 결과) | 로그 마지막 30줄 출력 + 진단:<br>• `EADDRINUSE` → 5173 충돌, vite 도 fallback 못 함 (희소). 종료 후 재시도.<br>• `Cannot find module` → `node_modules` 손상. `/onboard` 재셋업.<br>• 그 외 → 사용자에게 진단 요청 |

(폴백: 추출 실패 + 명확 진단 안 되면 **5173 강제 가정** 하고 사용자에게 "포트 5173 로 진행. 실제 다르면 vite 로그 직접 확인" 안내)

### Stage 4 — 브라우저 자동 띄우기 (OS 분기)

**Bash**: `uname -s` 로 OS 감지 (`/onboard` Stage 1 결과 재사용 가능).

| OS | 명령 |
|---|---|
| `Darwin` (macOS) | `open http://localhost:<port>` |
| `Linux` (WSL 포함) | `xdg-open http://localhost:<port>` 시도 → 실패 시 `wslview` 시도 |

**자동 열기 실패해도 OK** (예: 헤드리스 환경) — URL 만 사용자에게 출력하고 Stage 5 로.

### Stage 5 — 5 step UI 체크리스트 안내

사용자에게 출력 (확인 안 묻음 — 운영자가 자율 체크):

```
브라우저: http://localhost:<port>

다음 항목이 정상 표시되는지 확인하세요:

  [ ] Step 1 Sales Performance — S/A/B/C/D 등급 표시
  [ ] Step 2 Case Study — 주차별 그래프
  [ ] Step 3 Style Match — GO list 업로드 가능
  [ ] Step 4 Order Suggest — 발주 추천 표
  [ ] Step 5 Size Assortment — 사이즈 표 + Excel export

문제 있으면 Claude Code 에게 자연어로 알려주세요.
(예: "Step 4 가 비어있어", "console 에 빨간 에러 떠")
```

### Stage 6 — 운영 안내

```
=== 서버 기동 완료 ===

  백엔드:  http://localhost:8000           (로그: /tmp/orderai_backend.log)
  프론트:  http://localhost:<port>         (로그: /tmp/orderai_frontend.log)
  API:     http://localhost:8000/api/health (헬스체크)

서버 종료:
  - 명령어:   pkill -f "uvicorn server.api:app" && pkill -f vite
  - 자연어:   Claude Code 에게 "서버 종료해줘"

다음 운영 흐름:
  - 새 raw 데이터 도착 시:  /prepare-pipeline → /run-pipeline → /server-start
  - 임계값만 조정:           /run-pipeline → /server-start
  - 단순 재기동:             /server-start (본 스킬 다시 호출)
```

## 안전 제약

- 백엔드/프론트 모두 **background 실행** — Claude Code 의 background ID 로 추적, 종료는 OS `pkill -f` 패턴 매칭.
- 포트 8000 충돌 시 **자동 kill 절대 X** — 운영자 결정 우선.
- vite 포트 추출 실패 시 5173 폴백 + 명시적 안내. 강제로 다른 포트 지정 X.
- Windows native 환경: `/onboard` 와 동일 — WSL2 안내 후 종료.
- `data/`, `public/`, `apps/lite/` 등 파일 수정 **X** — 본 스킬은 read-only + 서버 기동만.
- 백엔드 헬스체크 fail 시 Stage 3 진입 금지.

## 참고 문서

- 백엔드 진입점: `server/api.py` (FastAPI app + 23 routes)
- 프론트 dev: `apps/lite/vite.config.js`, `apps/lite/package.json`
- 변경 가드레일: `CLAUDE.md` §1, §2 (server/ 와 apps/lite/ 의 dos/donts)
- API 헬스: `/api/health` 응답이 `{"status":"ok"}` 면 정상
- 이전 스킬: `/run-pipeline` (분석 + 산출물 생성)
- 첫 셋업: `/onboard`

