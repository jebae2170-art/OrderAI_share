"""
S3 Presigned URL API 클라이언트
- boto3/AWS SDK 사용 금지. Presigned URL API만 사용.
- 모든 함수는 async, 실패 시 예외를 raise하지 않고 None/False 반환 (caller가 fallback 처리)

원본(order_ai)의 server/s3_client.py 정본 미러링 — 운영배포 체인(_deploy_baseline_prd.py) 전용.
"""

import json
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

PRESIGNED_API_BASE = "https://aviyup1kyk.execute-api.ap-northeast-2.amazonaws.com/prod"
S3_BUCKET = "svc-fnf-ax-platform-pub-s3"
DASHBOARD_NAME = "order-ai"


def _get_env() -> str:
    return os.getenv("S3_ENV", "dev")


def _get_api_key() -> str:
    return os.getenv("S3_API_KEY", "")


# Lite 공용 영역(전 유저 공유 읽기)에 저장되는 파일 — 운영팀 파이프라인 결과물
# 그 외 파일은 lite-user/{brand}/{season}/{email}/ 본인 영역으로 분기됨
SHARED_READONLY_FILES = {
    "dashboard_data.json",
    "season_closing_data.json",
    "style_mapping_data.json",
    "size_assortment_data.json",
    "order_recommendation_data.json",  # baseline (관리자 사전 매핑 결과)
    "order_ai.duckdb",
    "_meta.json",
}


def build_s3_key(
    user_email: str,
    filename: str,
    mode: str = "full",
    brand: str = None,
    season: str = None,
) -> str:
    """S3 key 생성 (Step 2 — mode/brand/season 확장).

    mode='full' (default):
        order-ai/{env}/full/{user_email}/{filename}
        ※ 4/28 결정: 기존 order-ai/{env}/{user_email}/{filename} 경로는 사용 안 함.
           운영 배포 전 scripts/migrate_s3_layout.py로 일괄 이동 필요.

    mode='lite':
        - filename in SHARED_READONLY_FILES → order-ai/{env}/lite/{brand}/{season}/{filename}  (전 유저 공유)
        - 그 외 → order-ai/{env}/lite-user/{brand}/{season}/{user_email}/{filename}  (본인 사물함)
    """
    env = _get_env()
    if mode == "full":
        return f"{DASHBOARD_NAME}/{env}/full/{user_email}/{filename}"
    if mode == "lite":
        if not brand or not season:
            raise ValueError(
                f"mode='lite' requires brand and season (got brand={brand!r}, season={season!r})"
            )
        if filename in SHARED_READONLY_FILES:
            return f"{DASHBOARD_NAME}/{env}/lite/{brand}/{season}/{filename}"
        return f"{DASHBOARD_NAME}/{env}/lite-user/{brand}/{season}/{user_email}/{filename}"
    raise ValueError(f"Unknown mode: {mode!r} (expected 'full' or 'lite')")


async def _get_presigned_url(key: str, action: str) -> Optional[str]:
    """Presigned URL 발급 (PUT_OBJECT / GET_OBJECT / DELETE_OBJECT)"""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{PRESIGNED_API_BASE}/sign",
                headers={
                    "content-type": "application/json",
                    "x-api-key": _get_api_key(),
                },
                json={
                    "bucket": S3_BUCKET,
                    "key": key,
                    "action": action,
                },
            )
            resp.raise_for_status()
            return resp.json()["url"]
    except Exception as e:
        logger.error(f"[S3] Presigned URL 발급 실패 (key={key}, action={action}): {e}")
        return None


async def upload_json(user_email: str, filename: str, data: dict) -> bool:
    """JSON 데이터를 S3에 업로드. 성공 시 True, 실패 시 False."""
    if not _get_api_key():
        logger.warning("[S3] S3_API_KEY가 설정되지 않아 업로드를 건너뜁니다.")
        return False

    key = build_s3_key(user_email, filename)

    try:
        presigned_url = await _get_presigned_url(key, "PUT_OBJECT")
        if not presigned_url:
            return False

        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.put(
                presigned_url,
                content=body,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()

        logger.info(f"[S3] 업로드 성공: {key}")
        return True
    except Exception as e:
        logger.error(f"[S3] 업로드 실패 (key={key}): {e}")
        return False


async def download_json(user_email: str, filename: str) -> Optional[dict]:
    """S3에서 JSON 다운로드. 실패 시 None 반환."""
    if not _get_api_key():
        return None

    key = build_s3_key(user_email, filename)

    try:
        presigned_url = await _get_presigned_url(key, "GET_OBJECT")
        if not presigned_url:
            return None

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(presigned_url)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.error(f"[S3] 다운로드 실패 (key={key}): {e}")
        return None


# ───────────── Binary 파일 업/다운로드 (DuckDB baseline 등) ─────────────

def get_duckdb_s3_key(env: Optional[str] = None) -> str:
    """baseline DuckDB 파일의 S3 키.

    DuckDB는 모든 brand/season을 통합한 단일 파일이므로 brand/season 폴더에 두지 않고
    baseline/ 아래 단일 키로 관리한다.
    """
    return f"{DASHBOARD_NAME}/{env or _get_env()}/baseline/order_ai.duckdb"


async def upload_binary(key: str, local_path: str, content_type: str = "application/octet-stream") -> bool:
    """로컬 binary 파일을 S3에 업로드. 성공 시 True."""
    if not _get_api_key():
        logger.warning("[S3] S3_API_KEY 미설정 — 업로드 건너뜀")
        return False
    try:
        presigned_url = await _get_presigned_url(key, "PUT_OBJECT")
        if not presigned_url:
            return False
        with open(local_path, "rb") as f:
            body = f.read()
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.put(
                presigned_url,
                content=body,
                headers={"Content-Type": content_type},
            )
            resp.raise_for_status()
        logger.info(f"[S3] binary 업로드 성공: {key} ({len(body):,} bytes)")
        return True
    except Exception as e:
        logger.error(f"[S3] binary 업로드 실패 (key={key}): {e}")
        return False


async def download_binary(key: str, local_path: str) -> bool:
    """S3 binary 파일을 로컬에 다운로드. 성공 시 True."""
    if not _get_api_key():
        logger.warning("[S3] S3_API_KEY 미설정 — 다운로드 건너뜀")
        return False
    try:
        presigned_url = await _get_presigned_url(key, "GET_OBJECT")
        if not presigned_url:
            return False
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.get(presigned_url)
            if resp.status_code == 404:
                logger.warning(f"[S3] 파일 없음: {key}")
                return False
            resp.raise_for_status()
            os.makedirs(os.path.dirname(os.path.abspath(local_path)) or ".", exist_ok=True)
            with open(local_path, "wb") as f:
                f.write(resp.content)
        logger.info(f"[S3] binary 다운로드 성공: {key} → {local_path} ({len(resp.content):,} bytes)")
        return True
    except Exception as e:
        logger.error(f"[S3] binary 다운로드 실패 (key={key}): {e}")
        return False


# ───────────── 사용자 파일 목록 ─────────────

async def list_user_files(user_email: str, max_keys: int = 100) -> list:
    """사용자 폴더의 파일 목록 조회."""
    if not _get_api_key():
        return []

    prefix = f"{DASHBOARD_NAME}/{_get_env()}/full/{user_email}/"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{PRESIGNED_API_BASE}/list",
                headers={"x-api-key": _get_api_key()},
                params={
                    "bucket": S3_BUCKET,
                    "prefix": prefix,
                    "maxKeys": max_keys,
                },
            )
            resp.raise_for_status()
            return resp.json().get("items", [])
    except Exception as e:
        logger.error(f"[S3] 목록 조회 실패 (prefix={prefix}): {e}")
        return []
