"""FoodLog 프록시 서버.

앱(Android)에서 오는 요청을 받아 Anthropic 비전 모델·식약처 Open API·
Open Food Facts를 대신 호출한다. 외부 API 키는 전부 이 서버에만 둔다.

엔드포인트:
  POST /v1/analyze        음식 사진 / 영양성분표 분석
  GET  /v1/barcode/{code} 바코드 → 제품 영양정보 조회
  GET  /v1/health         헬스체크

모든 요청(health 제외)에 X-App-Token 헤더 필수.
설정은 /etc/foodlog.env → systemd EnvironmentFile 로 주입:
  ANTHROPIC_API_KEY, MFDS_API_KEY, APP_TOKENS(쉼표 구분),
  FOODLOG_DB(기본 cache.db), DAILY_LIMIT(기본 200), ANTHROPIC_MODEL(기본 claude-sonnet-5)
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import sqlite3
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Literal, Optional

import anthropic
import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger("foodlog")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DB_PATH = os.environ.get("FOODLOG_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache.db"))
APP_TOKENS = {t.strip() for t in os.environ.get("APP_TOKENS", "").split(",") if t.strip()}
DAILY_LIMIT = int(os.environ.get("DAILY_LIMIT", "200"))
MFDS_API_KEY = os.environ.get("MFDS_API_KEY", "")
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
MAX_IMAGES = 8
MAX_IMAGE_BYTES = 6 * 1024 * 1024  # base64 디코드 후 기준

# ---------------------------------------------------------------------------
# 시스템 프롬프트 (스펙 6절). prompt caching 을 위해 바이트 단위로 고정한다 —
# 시각·요청별 값은 여기 넣지 말 것.
# ---------------------------------------------------------------------------
MEAL_SYSTEM_PROMPT = """당신은 한국 식단 기록 앱의 음식 인식 엔진입니다. 한 끼니에 해당하는 사진 여러 장을 받습니다.
같은 음식이 여러 사진에 겹쳐 나오면 하나로 합치고, 여러 사진에 걸친 코스 요리는 순서대로 모두 나열하세요.

각 음식에 대해 다음을 추정합니다.
- name: 한국어 메뉴명 (가능하면 구체적으로. "찌개"보다 "김치찌개")
- portion_desc: 사진에 보이는 전체 분량 (예: "1인분(약 300g)", "2인분 가량", "1조각")
- kcal: portion_desc 전체의 열량 (내 몫이 아니라 사진에 보이는 전체)
- carbs_g, protein_g, fat_g: 가능하면 추정, 모르면 null
- likely_shared: 여러 명이 나눠 먹는 형태로 보이면 true (전골, 고기 판, 큰 접시 안주 등), 개인 접시·개인 그릇이면 false
- confidence: 0~1. 메뉴 식별과 분량 추정 모두 반영

반드시 아래 JSON만 출력하고 다른 텍스트는 쓰지 마세요.
{
  "items": [
    {"name": "...", "portion_desc": "...", "kcal": 0, "carbs_g": null, "protein_g": null, "fat_g": null,
     "likely_shared": false, "confidence": 0.0, "photo_indices": [0]}
  ],
  "meal_type_guess": "BREAKFAST|LUNCH|DINNER|SNACK",
  "notes": "분량 판단 근거나 불확실한 점을 한두 문장으로"
}"""

LABEL_SYSTEM_PROMPT = """영양성분표 사진을 읽어 JSON으로 정리하세요. 총 내용량과 1회 제공량을 구분하고,
열량은 1회 제공량 기준으로 적으세요. 읽을 수 없는 값은 null.
반드시 아래 JSON만 출력하고 다른 텍스트는 쓰지 마세요.
{"product_name": "...", "serving_desc": "...", "total_servings": 0,
 "kcal_per_serving": 0, "carbs_g": null, "protein_g": null, "fat_g": null, "confidence": 0.0}"""

# ---------------------------------------------------------------------------
# SQLite: 제품 캐시 + 사용량 로그
# ---------------------------------------------------------------------------
_db_lock = threading.Lock()


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _db_lock, _db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS products (
                barcode TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                brand TEXT,
                serving_desc TEXT,
                kcal_per_serving REAL NOT NULL,
                carbs_g REAL, protein_g REAL, fat_g REAL,
                source TEXT NOT NULL,
                fetched_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS usage_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                requested_at INTEGER NOT NULL,
                day TEXT NOT NULL,
                image_count INTEGER NOT NULL DEFAULT 0,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_usage_day ON usage_log(token, day);
            """
        )


def usage_count_today(token: str) -> int:
    day = datetime.now().strftime("%Y-%m-%d")
    with _db_lock, _db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM usage_log WHERE token=? AND day=? AND endpoint='analyze'",
            (token, day),
        ).fetchone()
        return int(row["c"])


def log_usage(token: str, endpoint: str, image_count: int = 0,
              input_tokens: int = 0, output_tokens: int = 0) -> None:
    now = int(time.time() * 1000)
    day = datetime.now().strftime("%Y-%m-%d")
    with _db_lock, _db() as conn:
        conn.execute(
            "INSERT INTO usage_log(token, endpoint, requested_at, day, image_count, input_tokens, output_tokens)"
            " VALUES(?,?,?,?,?,?,?)",
            (token, endpoint, now, day, image_count, input_tokens, output_tokens),
        )


def cache_get_product(barcode: str) -> Optional[dict[str, Any]]:
    with _db_lock, _db() as conn:
        row = conn.execute("SELECT * FROM products WHERE barcode=?", (barcode,)).fetchone()
        return dict(row) if row else None


def cache_put_product(p: dict[str, Any]) -> None:
    with _db_lock, _db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO products(barcode,name,brand,serving_desc,kcal_per_serving,"
            "carbs_g,protein_g,fat_g,source,fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                p["barcode"], p["name"], p.get("brand"), p.get("serving_desc"),
                p["kcal_per_serving"], p.get("carbs_g"), p.get("protein_g"), p.get("fat_g"),
                p["source"], int(time.time() * 1000),
            ),
        )


# ---------------------------------------------------------------------------
# 요청/응답 모델
# ---------------------------------------------------------------------------
class AnalyzeImage(BaseModel):
    data: str = Field(description="base64 JPEG")
    taken_at: Optional[str] = None


class AnalyzeRequest(BaseModel):
    images: list[AnalyzeImage]
    hint: Optional[str] = None
    mode: Literal["meal", "label"] = "meal"


# ---------------------------------------------------------------------------
# 앱 토큰 인증
# ---------------------------------------------------------------------------
def require_token(x_app_token: str = Header(default="")) -> str:
    if not APP_TOKENS:
        raise HTTPException(status_code=503, detail="server has no APP_TOKENS configured")
    if x_app_token not in APP_TOKENS:
        raise HTTPException(status_code=401, detail="invalid app token")
    return x_app_token


# ---------------------------------------------------------------------------
# FastAPI 앱
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    app.state.anthropic = anthropic.AsyncAnthropic()
    app.state.http = httpx.AsyncClient(timeout=15.0)
    yield
    await app.state.http.aclose()
    await app.state.anthropic.close()


app = FastAPI(title="foodlog-proxy", lifespan=lifespan)


@app.get("/v1/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "model": MODEL, "time": datetime.now().astimezone().isoformat()}


# ---------------------------------------------------------------------------
# /v1/analyze
# ---------------------------------------------------------------------------
_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _extract_json(text: str) -> dict[str, Any]:
    """모델 응답에서 JSON 오브젝트를 파싱한다. 코드펜스·앞뒤 잡담 방어."""
    cleaned = _JSON_FENCE_RE.sub("", text).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            return json.loads(cleaned[start:end + 1])
        raise


def _build_user_content(req: AnalyzeRequest) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    for img in req.images:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": img.data},
        })
    lines = []
    for i, img in enumerate(req.images):
        if img.taken_at:
            lines.append(f"사진 {i}: 촬영 시각 {img.taken_at}")
    if req.hint:
        lines.append(f"힌트: {req.hint}")
    lines.append("위 사진을 분석해 지정된 JSON으로만 답하세요.")
    content.append({"type": "text", "text": "\n".join(lines)})
    return content


@app.post("/v1/analyze")
async def analyze(req: AnalyzeRequest, token: str = Depends(require_token)) -> dict[str, Any]:
    if not req.images:
        raise HTTPException(status_code=400, detail="images is empty")
    if len(req.images) > MAX_IMAGES:
        raise HTTPException(status_code=400, detail=f"too many images (max {MAX_IMAGES})")
    for img in req.images:
        # base64 길이로 대략 검증 (4/3 오버헤드)
        if len(img.data) * 3 // 4 > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=400, detail="image too large")
        try:
            base64.b64decode(img.data[:80], validate=True)
        except Exception:
            raise HTTPException(status_code=400, detail="invalid base64 image")

    if usage_count_today(token) >= DAILY_LIMIT:
        raise HTTPException(status_code=429, detail="daily analyze limit reached")

    system_prompt = MEAL_SYSTEM_PROMPT if req.mode == "meal" else LABEL_SYSTEM_PROMPT
    client: anthropic.AsyncAnthropic = app.state.anthropic
    messages: list[dict[str, Any]] = [{"role": "user", "content": _build_user_content(req)}]

    last_error: Exception | None = None
    total_in = total_out = 0
    for attempt in range(2):  # JSON 파싱 실패 시 1회 재시도
        try:
            response = await client.messages.create(
                model=MODEL,
                max_tokens=2000,
                system=[{
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=messages,
            )
        except anthropic.RateLimitError:
            raise HTTPException(status_code=429, detail="upstream rate limited")
        except anthropic.APIStatusError as e:
            logger.error("anthropic error %s: %s", e.status_code, e.message)
            raise HTTPException(status_code=502, detail="model call failed")
        except anthropic.APIConnectionError:
            raise HTTPException(status_code=502, detail="model connection failed")

        total_in += response.usage.input_tokens
        total_out += response.usage.output_tokens
        text = "".join(b.text for b in response.content if b.type == "text")
        try:
            result = _extract_json(text)
            log_usage(token, "analyze", len(req.images), total_in, total_out)
            return result
        except (json.JSONDecodeError, ValueError) as e:
            last_error = e
            logger.warning("attempt %d: JSON parse failed: %s", attempt + 1, text[:300])
            messages = messages + [
                {"role": "assistant", "content": text or "(빈 응답)"},
                {"role": "user", "content": "형식이 잘못됐습니다. 지정한 스키마의 JSON 오브젝트 하나만 다시 출력하세요."},
            ]

    log_usage(token, "analyze", len(req.images), total_in, total_out)
    raise HTTPException(status_code=502, detail=f"model returned unparseable output: {last_error}")


# ---------------------------------------------------------------------------
# /v1/barcode/{code}
# 1) 식약처 식품안전나라 C005(바코드연계 제품정보) → I2790(가공식품 영양성분)
#    ※ 서비스명·필드명은 공식 문서 기준으로 확인 필요 (스펙 10절) — 필드가 없으면
#      방어적으로 건너뛰고 OFF 로 폴백한다.
# 2) Open Food Facts v2
# ---------------------------------------------------------------------------
def _to_float(v: Any) -> Optional[float]:
    try:
        s = str(v).strip()
        if not s or s in ("-", "N/A"):
            return None
        return float(re.sub(r"[^0-9.\-]", "", s) or "nan")
    except (ValueError, TypeError):
        return None


async def _lookup_mfds(http: httpx.AsyncClient, code: str) -> Optional[dict[str, Any]]:
    if not MFDS_API_KEY:
        return None
    try:
        # C005: 유통 바코드 → 품목보고번호/제품명
        url = f"https://openapi.foodsafetykorea.go.kr/api/{MFDS_API_KEY}/C005/json/1/5/BAR_CD={code}"
        r = await http.get(url)
        r.raise_for_status()
        rows = (r.json().get("C005") or {}).get("row") or []
        if not rows:
            return None
        row = rows[0]
        name = row.get("PRDLST_NM") or row.get("PRDT_NM")
        report_no = row.get("PRDLST_REPORT_NO")
        brand = row.get("BSSH_NM")
        if not name:
            return None

        product: dict[str, Any] = {
            "barcode": code, "name": name, "brand": brand,
            "serving_desc": None, "kcal_per_serving": None,
            "carbs_g": None, "protein_g": None, "fat_g": None,
            "source": "MFDS",
        }
        if report_no:
            # I2790: 품목보고번호 → 영양성분 (NUTR_CONT1=열량, 2=탄수화물, 3=단백질, 4=지방)
            url2 = (f"https://openapi.foodsafetykorea.go.kr/api/{MFDS_API_KEY}"
                    f"/I2790/json/1/5/PRDLST_REPORT_NO={report_no}")
            r2 = await http.get(url2)
            if r2.status_code == 200:
                rows2 = (r2.json().get("I2790") or {}).get("row") or []
                if rows2:
                    n = rows2[0]
                    product["kcal_per_serving"] = _to_float(n.get("NUTR_CONT1"))
                    product["carbs_g"] = _to_float(n.get("NUTR_CONT2"))
                    product["protein_g"] = _to_float(n.get("NUTR_CONT3"))
                    product["fat_g"] = _to_float(n.get("NUTR_CONT4"))
                    serving = n.get("SERVING_SIZE") or n.get("SERVING_WT")
                    unit = n.get("SERVING_UNIT") or ""
                    if serving:
                        product["serving_desc"] = f"{serving}{unit}".strip()
        if product["kcal_per_serving"] is None:
            return None  # 열량 없이는 쓸모가 없으므로 다음 소스로
        return product
    except (httpx.HTTPError, ValueError, KeyError) as e:
        logger.warning("MFDS lookup failed for %s: %s", code, e)
        return None


async def _lookup_off(http: httpx.AsyncClient, code: str) -> Optional[dict[str, Any]]:
    try:
        url = f"https://world.openfoodfacts.org/api/v2/product/{code}"
        r = await http.get(url, headers={"User-Agent": "foodlog/1.0 (personal use)"})
        if r.status_code == 404:
            return None
        r.raise_for_status()
        body = r.json()
        p = body.get("product")
        if body.get("status") != 1 or not p:
            return None
        nutriments = p.get("nutriments") or {}
        name = p.get("product_name_ko") or p.get("product_name")
        if not name:
            return None

        kcal = _to_float(nutriments.get("energy-kcal_serving"))
        serving_desc = p.get("serving_size")
        carbs = _to_float(nutriments.get("carbohydrates_serving"))
        protein = _to_float(nutriments.get("proteins_serving"))
        fat = _to_float(nutriments.get("fat_serving"))
        if kcal is None:
            # 1회 제공량 정보가 없으면 100g 기준으로
            kcal = _to_float(nutriments.get("energy-kcal_100g"))
            if kcal is None:
                return None
            serving_desc = "100g 기준"
            carbs = _to_float(nutriments.get("carbohydrates_100g"))
            protein = _to_float(nutriments.get("proteins_100g"))
            fat = _to_float(nutriments.get("fat_100g"))

        return {
            "barcode": code, "name": name, "brand": p.get("brands"),
            "serving_desc": serving_desc, "kcal_per_serving": kcal,
            "carbs_g": carbs, "protein_g": protein, "fat_g": fat,
            "source": "OFF",
        }
    except (httpx.HTTPError, ValueError, KeyError) as e:
        logger.warning("OFF lookup failed for %s: %s", code, e)
        return None


@app.get("/v1/barcode/{code}")
async def barcode(code: str, token: str = Depends(require_token)) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9]{6,14}", code):
        raise HTTPException(status_code=400, detail="invalid barcode")

    cached = cache_get_product(code)
    if cached:
        source = cached.pop("source")
        cached.pop("fetched_at", None)
        log_usage(token, "barcode")
        return {"found": True, "product": cached, "source": source}

    http: httpx.AsyncClient = app.state.http
    product = await _lookup_mfds(http, code) or await _lookup_off(http, code)
    log_usage(token, "barcode")
    if not product:
        return {"found": False}

    cache_put_product(product)
    source = product.pop("source")
    return {"found": True, "product": product, "source": source}
