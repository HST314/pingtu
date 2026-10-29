"""平涂色块转换器 —— FastAPI 后端。

启动：
    python -m uvicorn main:app --reload --port 8000
然后打开 http://127.0.0.1:8000
"""

import asyncio
import base64
import io
import json
import math
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
OUTPUT_DIR = ROOT / "output"

JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def load_env(path: Path) -> None:
    """极简 .env 读取，避免额外依赖。已存在的环境变量优先。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


load_env(ROOT / ".env")

API_KEY = os.environ.get("ARK_API_KEY", "").strip()
BASE_URL = os.environ.get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3").strip()
MODEL = os.environ.get("ARK_MODEL", "doubao-seedream-4-5-251128").strip()
MOCK = os.environ.get("ARK_MOCK", "0") == "1"

COOLDOWN = int(os.environ.get("COOLDOWN_SECONDS", "120"))
DAILY_LIMIT = int(os.environ.get("DAILY_LIMIT", "200"))
MAX_UPLOAD = int(float(os.environ.get("MAX_UPLOAD_MB", "10")) * 1024 * 1024)
TTL_HOURS = float(os.environ.get("OUTPUT_TTL_HOURS", "24"))
MAX_INPUT_SIDE = 2048

DATA_DIR = ROOT / "data"
USAGE_FILE = DATA_DIR / "usage.json"

PROMPT = "把这张图做成“平涂色块”效果：每个颜色区域统一成一种纯色，去掉渐变、纹理和深浅变化，保留原有构图。"


# ---------------------------------------------------------------- 全局状态

_cooldown_until = 0.0   # epoch 秒
_busy = False
_lock = asyncio.Lock()


def today_key() -> str:
    return time.strftime("%Y-%m-%d")


def _load_usage() -> tuple[str, int]:
    try:
        data = json.loads(USAGE_FILE.read_text(encoding="utf-8"))
        return str(data.get("date", "")), int(data.get("count", 0))
    except Exception:
        return today_key(), 0


def _save_usage() -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = USAGE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"date": _usage_date, "count": _usage_count}), encoding="utf-8")
        tmp.replace(USAGE_FILE)
    except Exception:
        pass  # 统计写盘失败不影响出图


_usage_date, _usage_count = _load_usage()


def remaining_cooldown() -> int:
    return max(0, math.ceil(_cooldown_until - time.time()))


def daily_remaining() -> int:
    global _usage_date, _usage_count
    if _usage_date != today_key():
        _usage_date, _usage_count = today_key(), 0
    return max(0, DAILY_LIMIT - _usage_count)


# ---------------------------------------------------------------- 图片处理

def prepare_image(raw: bytes) -> bytes:
    """校验并压缩上传图片，返回 JPEG 字节。"""
    if len(raw) > MAX_UPLOAD:
        raise ValueError(f"图片超过 {MAX_UPLOAD // 1024 // 1024}MB 限制")
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception:
        raise ValueError("无法识别这张图片，请换 JPG / PNG / WEBP 格式")

    if img.format not in ("JPEG", "PNG", "WEBP"):
        raise ValueError("只支持 JPG / PNG / WEBP 格式")

    img = img.convert("RGB")
    if max(img.size) > MAX_INPUT_SIDE:
        img.thumbnail((MAX_INPUT_SIDE, MAX_INPUT_SIDE), Image.LANCZOS)

    out = io.BytesIO()
    img.save(out, "JPEG", quality=92)
    return out.getvalue()


def to_data_uri(jpeg: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()


def mock_result(jpeg: bytes) -> bytes:
    """MOCK 模式：用色阶压缩模拟平涂效果，方便没 Key 时调界面。"""
    img = Image.open(io.BytesIO(jpeg)).convert("RGB")
    img = img.filter(ImageFilter.MedianFilter(5))
    img = img.quantize(colors=6, method=Image.Quantize.MEDIANCUT).convert("RGB")
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def save_png(png_bytes: bytes, job_id: str) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"{job_id}.png"
    path.write_bytes(png_bytes)
    return path


def normalize_to_png(image_bytes: bytes) -> bytes:
    img = Image.open(io.BytesIO(image_bytes))
    img.load()
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def find_job(job_id: str) -> Path:
    if not JOB_ID_RE.match(job_id):
        raise HTTPException(404, "找不到这张图")
    path = OUTPUT_DIR / f"{job_id}.png"
    if not path.exists():
        raise HTTPException(404, "这张图已过期或不存在")
    return path


# ---------------------------------------------------------------- 调用模型

async def call_ark(jpeg: bytes) -> bytes:
    """调用豆包 Seedream 图生图，返回 PNG 字节。"""
    if MOCK:
        await asyncio.sleep(1.2)
        return mock_result(jpeg)

    if not API_KEY:
        raise RuntimeError("服务器没有配置 ARK_API_KEY")

    payload = {
        "model": MODEL,
        "prompt": PROMPT,
        "image": to_data_uri(jpeg),
        "size": "2K",
        "response_format": "b64_json",
        "watermark": False,
    }
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=180) as client:
        try:
            resp = await client.post(f"{BASE_URL}/images/generations", json=payload, headers=headers)
        except httpx.TimeoutException:
            raise RuntimeError("模型响应超时，请稍后重试")

    if resp.status_code == 401:
        raise RuntimeError("模型鉴权失败：API Key 无效或未启用，请检查 .env 配置")
    if resp.status_code == 429:
        raise RuntimeError("模型服务繁忙，请稍后重试")
    if resp.status_code != 200:
        detail = resp.text[:300]
        try:
            detail = resp.json().get("error", {}).get("message", detail)
        except Exception:
            pass
        raise RuntimeError(f"模型调用失败（HTTP {resp.status_code}）：{detail}")

    data = resp.json().get("data") or [{}]
    item = data[0]
    if item.get("b64_json"):
        return normalize_to_png(base64.b64decode(item["b64_json"]))
    if item.get("url"):
        async with httpx.AsyncClient(timeout=120) as client:
            img_resp = await client.get(item["url"])
            img_resp.raise_for_status()
            return normalize_to_png(img_resp.content)
    raise RuntimeError("模型没有返回图片数据")


# ---------------------------------------------------------------- 过期清理

async def cleanup_loop() -> None:
    ttl = TTL_HOURS * 3600
    while True:
        try:
            now = time.time()
            for path in OUTPUT_DIR.glob("*.png"):
                if now - path.stat().st_mtime > ttl:
                    path.unlink(missing_ok=True)
        except Exception:
            pass
        await asyncio.sleep(1800)


@asynccontextmanager
async def lifespan(app: FastAPI):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    task = asyncio.create_task(cleanup_loop())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="平涂色块转换器", lifespan=lifespan)


# ---------------------------------------------------------------- 路由

@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/api/status")
async def status():
    return {
        "busy": _busy,
        "cooldown_remaining": remaining_cooldown(),
        "cooldown_seconds": COOLDOWN,
        "daily_remaining": daily_remaining(),
        "daily_limit": DAILY_LIMIT,
        "mock": MOCK,
    }


@app.post("/api/convert")
async def convert(request: Request):
    global _busy, _cooldown_until, _usage_date, _usage_count

    raw = await request.body()
    if not raw:
        raise HTTPException(400, "没有收到图片")

    async with _lock:
        if daily_remaining() <= 0:
            return JSONResponse(
                {"detail": f"今日 {DAILY_LIMIT} 张额度已用完，明天再来", "daily_remaining": 0},
                status_code=429,
            )
        if _busy:
            return JSONResponse(
                {"detail": "正在转换另一张图，请稍等", "retry_after": remaining_cooldown()},
                status_code=429,
            )
        left = remaining_cooldown()
        if left > 0:
            return JSONResponse(
                {"detail": f"冷却中，还需等待 {left} 秒", "retry_after": left},
                status_code=429,
            )
        _busy = True
        _cooldown_until = time.time() + COOLDOWN
        _usage_count += 1
        _save_usage()

    started = time.time()
    try:
        jpeg = prepare_image(raw)
        png = await call_ark(jpeg)
        job_id = uuid.uuid4().hex
        save_png(png, job_id)
        return {
            "job_id": job_id,
            "image_url": f"/api/image/{job_id}",
            "download_url": f"/api/download/{job_id}",
            "elapsed": round(time.time() - started, 1),
            "cooldown_remaining": remaining_cooldown(),
            "daily_remaining": daily_remaining(),
        }
    except ValueError as e:
        _cooldown_until = 0.0          # 输入问题不该罚用户等 2 分钟
        _usage_count = max(0, _usage_count - 1)
        _save_usage()
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        _cooldown_until = 0.0          # 上游失败同样退回冷却和额度
        _usage_count = max(0, _usage_count - 1)
        _save_usage()
        raise HTTPException(502, str(e))
    finally:
        _busy = False


@app.get("/api/image/{job_id}")
async def image(job_id: str):
    return FileResponse(find_job(job_id), media_type="image/png")


@app.get("/api/download/{job_id}")
async def download(job_id: str):
    path = find_job(job_id)
    stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(path.stat().st_mtime))
    return FileResponse(path, media_type="image/png", filename=f"pingtu_{stamp}.png")


STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
