/**
 * Lite API 클라이언트 — Full(src/service/apiClient.js)의 동일 인터페이스 제공.
 *
 * 차이점 (내부적으로 처리):
 *   - 모든 fetch가 /api/lite/* 로 자동 라우팅
 *   - brand/season query string 자동 부착
 *   - X-User-Email 헤더 자동 부착
 *   - fetchFile(filename) → 적절한 GET endpoint로 매핑
 *
 * 컴포넌트는 Full과 동일하게:
 *   const api = useMemo(() => createApiClient(user?.email, brand, season),
 *                       [user?.email, brand, season])
 *   const data = await api.fetchFile('dashboard_data.json')
 */

const API_BASE = (import.meta.env.BASE_URL?.replace(/\/$/, '') || '')

// HTTP 헤더 값은 ISO-8859-1(Latin-1)만 허용한다. 포털이 내려준 email/role에
// 한글 등 code point > 255 문자가 섞이면 fetch가 RequestInit.headers 생성 단계에서
// TypeError를 던지므로, 헤더에 넣기 전 비-Latin1 문자를 모두 제거한다.
function _latin1Safe(v) {
  return String(v ?? '').replace(/[^\x00-\xFF]/g, '')
}

// roles는 배열/콤마문자열 모두 허용. 서버가 쓰는 `orderai:brand:{code}` 형식만
// 통과시키고(라벨/한글 섞인 값 배제) Latin1로 정제한 콤마 문자열을 반환.
function _safeRolesHeader(roles) {
  const list = Array.isArray(roles)
    ? roles
    : (typeof roles === 'string' ? roles.split(',') : [])
  const ok = list
    .map((r) => (typeof r === 'string' ? r.trim() : ''))
    .filter((r) => /^orderai:brand:[a-z]+$/i.test(r))
  return ok.length > 0 ? _latin1Safe(ok.join(',')) : ''
}

// filename → Lite GET endpoint 매핑 (baseline + 본인 사물함 fallback)
const FILE_TO_ENDPOINT = {
  'dashboard_data.json':            '/api/lite/dashboard',
  'season_closing_data.json':       '/api/lite/season-closing',
  'style_mapping_data.json':        '/api/lite/style-mapping',
  'order_recommendation_data.json': '/api/lite/order-recommendation',
  'size_assortment_data.json':      '/api/lite/size-assortment',
}

// 본인 사물함 단순 read (baseline에 없음, 화이트리스트는 lite.py에서 검증)
const USER_FILE_NAMES = new Set([
  'confirmed_order_data.json',
  'confirmed_mapping.json',
  'go_list.json',
])

// brand/season 무관 공용 파일 — shared.py::/api/s3/file/* endpoint로 라우팅
const SHARED_FILE_NAMES = new Set([
  'color_mapping.json',
])

// Lite로 변환하지 않고 그대로 두는 path (Full/Lite 양쪽 공유 endpoint — shared.py)
const SHARED_PATH_PREFIXES = [
  '/api/brand-config',
  '/api/health',
  '/api/s3/file/',
]

// Full path → Lite path 변환
//   '/api/confirmed-mapping' → '/api/lite/confirmed-mapping'
//   '/api/lite/X' → 그대로
//   '/api/brand-config' → 그대로 (shared)
function _toLitePath(path) {
  if (path.startsWith('/api/lite/')) return path
  if (SHARED_PATH_PREFIXES.some((p) => path === p || path.startsWith(p))) return path
  return path.replace(/^\/api\//, '/api/lite/')
}


export function createApiClient(userEmail, brand, season, userRoles = []) {
  function headers(contentType = 'application/json') {
    const h = {}
    if (contentType) h['Content-Type'] = contentType
    if (userEmail) h['X-User-Email'] = _latin1Safe(userEmail)
    const rolesHeader = _safeRolesHeader(userRoles)
    if (rolesHeader) h['X-User-Roles'] = rolesHeader
    return h
  }

  function _qs(extra = '') {
    const parts = []
    if (brand)  parts.push(`brand=${encodeURIComponent(brand)}`)
    if (season) parts.push(`season=${encodeURIComponent(season)}`)
    if (extra)  parts.push(extra)
    return parts.length ? `?${parts.join('&')}` : ''
  }

  function _appendQs(path, extra = '') {
    const sep = path.includes('?') ? '&' : '?'
    const parts = []
    if (brand && !path.includes('brand='))   parts.push(`brand=${encodeURIComponent(brand)}`)
    if (season && !path.includes('season=')) parts.push(`season=${encodeURIComponent(season)}`)
    if (extra) parts.push(extra)
    return parts.length ? `${path}${sep}${parts.join('&')}` : path
  }

  return {
    /**
     * S3 파일 조회와 동일한 인터페이스 — 내부적으로 Lite GET endpoint 호출.
     * 204 (본인 사물함 없음) → null 반환 (Full fetchFile 동작과 호환)
     *
     * 매핑 우선순위:
     *   1. FILE_TO_ENDPOINT — baseline 풀 데이터 (DuckDB fallback)
     *   2. USER_FILE_NAMES  — 본인 사물함 화이트리스트 (/api/lite/user-file)
     */
    async fetchFile(filename) {
      let url
      if (FILE_TO_ENDPOINT[filename]) {
        url = `${API_BASE}${_appendQs(FILE_TO_ENDPOINT[filename])}`
      } else if (USER_FILE_NAMES.has(filename)) {
        url = `${API_BASE}${_appendQs('/api/lite/user-file', `name=${encodeURIComponent(filename)}`)}`
      } else if (SHARED_FILE_NAMES.has(filename)) {
        url = `${API_BASE}/api/s3/file/${filename}`
      } else {
        console.warn('[Lite apiClient] 알려지지 않은 filename:', filename)
        return null
      }
      // step3 확정 직후 step4가 옛 GET 응답을 캐시하지 않도록 강제 fresh fetch.
      const res = await fetch(url, { headers: headers(null), cache: 'no-store' })
      if (res.status === 204) return null
      if (!res.ok) return null
      return res.json()
    },

    /** POST 요청 (JSON body) — path를 Lite로 변환 + brand/season 자동 부착 */
    async post(path, body) {
      const litePath = _toLitePath(path)
      const url = `${API_BASE}${_appendQs(litePath)}`
      return fetch(url, {
        method: 'POST',
        headers: headers('application/json'),
        body: JSON.stringify(body),
      })
    },

    /** POST 요청 (FormData — 파일 업로드, GO list 등) */
    async postForm(path, formData) {
      const litePath = _toLitePath(path)
      const url = `${API_BASE}${_appendQs(litePath)}`
      const h = {}
      if (userEmail) h['X-User-Email'] = _latin1Safe(userEmail)
      const rolesHeader = _safeRolesHeader(userRoles)
      if (rolesHeader) h['X-User-Roles'] = rolesHeader
      return fetch(url, {
        method: 'POST',
        headers: h,
        body: formData,
      })
    },

    /** GET 요청 */
    async get(path) {
      const litePath = _toLitePath(path)
      const url = `${API_BASE}${_appendQs(litePath)}`
      return fetch(url, { headers: headers(null) })
    },

    // ───────────── Lite 전용 ─────────────

    /** 본인 사물함 reset (mapping/orders/size/go/all) */
    async resetScope(scope) {
      const url = `${API_BASE}${_appendQs('/api/lite/reset', `scope=${encodeURIComponent(scope)}`)}`
      return fetch(url, { method: 'POST', headers: headers(null) })
    },

    /** 사이즈 mirroring 결과 명시 저장 */
    async saveConfirmedSize(payload) {
      return this.post('/api/lite/confirmed-size', payload)
    },

    /** 발주 Excel 다운로드 (Blob) */
    async downloadOrdersExcel() {
      const url = `${API_BASE}${_appendQs('/api/lite/orders/excel')}`
      const res = await fetch(url, { headers: headers(null) })
      if (!res.ok) {
        const error = new Error(`Excel download 실패 (${res.status})`)
        error.status = res.status
        try { error.data = await res.json() } catch { error.data = null }
        throw error
      }
      const blob = await res.blob()
      const cd = res.headers.get('Content-Disposition') || ''
      const match = cd.match(/filename="?([^"]+)"?/)
      const filename = match ? match[1] : 'OrderRecommendation_DRAFT.xlsx'
      return { blob, filename }
    },
  }
}
