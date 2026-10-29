"""最小连通性验证：调用豆包 Seedream 4.5 做图生图。

用法：
    python test_seedream.py                  # 自动生成一张测试图并转换
    python test_seedream.py 你的图片.png      # 用你自己的图

结果会保存到 output/ 目录，并打印接口返回的元信息。
"""

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path

import requests
from PIL import Image, ImageDraw

# Windows 控制台默认 GBK，中文输出会乱码
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output"

PROMPT = "把这张图做成“平涂色块”效果：每个颜色区域统一成一种纯色，去掉渐变、纹理和深浅变化，保留原有构图。"


def load_env(path: Path) -> None:
    """极简 .env 读取，避免额外依赖。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def make_test_image(path: Path) -> Path:
    """生成一张带渐变和纹理的测试图，方便看出平涂效果。"""
    w, h = 768, 768
    img = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(img)

    # 天空渐变
    for y in range(h // 2):
        t = y / (h // 2)
        draw.line([(0, y), (w, y)], fill=(int(120 + 100 * t), int(170 + 60 * t), 235))
    # 地面渐变
    for y in range(h // 2, h):
        t = (y - h // 2) / (h // 2)
        draw.line([(0, y), (w, y)], fill=(int(60 + 40 * t), int(140 - 40 * t), int(70 + 20 * t)))
    # 山
    draw.polygon([(0, h // 2), (260, 250), (520, h // 2)], fill=(110, 130, 160))
    draw.polygon([(300, h // 2), (560, 300), (760, h // 2)], fill=(90, 110, 145))
    # 太阳
    draw.ellipse([560, 90, 680, 210], fill=(255, 220, 120))
    # 树
    draw.rectangle([120, 520, 150, 620], fill=(90, 60, 40))
    draw.ellipse([60, 400, 210, 560], fill=(40, 110, 60))

    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path


def to_data_uri(path: Path) -> str:
    suffix = path.suffix.lower().lstrip(".")
    mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp"}.get(suffix, "png")
    data = base64.b64encode(path.read_bytes()).decode()
    return f"data:image/{mime};base64,{data}"


def call_api(base_url: str, api_key: str, model: str, prompt: str, image_uri: str) -> requests.Response:
    payload = {
        "model": model,
        "prompt": prompt,
        "image": image_uri,
        "size": "2K",
        "response_format": "b64_json",
        "watermark": False,
    }
    return requests.post(
        f"{base_url.rstrip('/')}/images/generations",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=180,
    )


def save_result(resp_json: dict, out_dir: Path, stem: str) -> Path:
    item = resp_json["data"][0]
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{stem}_result.png"

    if item.get("b64_json"):
        out_path.write_bytes(base64.b64decode(item["b64_json"]))
    elif item.get("url"):
        print(f"  -> 接口返回 URL，下载中…")
        r = requests.get(item["url"], timeout=120)
        r.raise_for_status()
        out_path.write_bytes(r.content)
    else:
        raise RuntimeError(f"返回里没有图片数据: {item.keys()}")
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", nargs="?", help="输入图片路径，不填则自动生成测试图")
    parser.add_argument("--url-only", action="store_true", help="让接口返回 URL 而不是 base64")
    args = parser.parse_args()

    load_env(ROOT / ".env")
    api_key = os.environ.get("ARK_API_KEY", "").strip()
    base_url = os.environ.get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3").strip()
    model = os.environ.get("ARK_MODEL", "doubao-seedream-4-5-251128").strip()

    if not api_key:
        print("缺少 ARK_API_KEY，请在 .env 里配置")
        return 1

    src = Path(args.image) if args.image else make_test_image(OUTPUT_DIR / "test_input.png")
    if not src.exists():
        print(f"找不到图片: {src}")
        return 1

    print(f"接口地址 : {base_url}")
    print(f"模型     : {model}")
    print(f"输入图片 : {src}  ({src.stat().st_size / 1024:.1f} KB)")

    image_uri = to_data_uri(src)
    print(f"编码后   : {len(image_uri) / 1024 / 1024:.2f} MB (base64)")

    t0 = time.time()
    try:
        resp = call_api(base_url, api_key, model, PROMPT, image_uri)
    except requests.RequestException as e:
        print(f"网络错误: {e}")
        return 1
    elapsed = time.time() - t0

    print(f"HTTP {resp.status_code}  耗时 {elapsed:.1f}s")
    if resp.status_code != 200:
        print("---- 错误响应 ----")
        print(resp.text[:2000])
        return 1

    data = resp.json()
    meta = {k: v for k, v in data.items() if k != "data"}
    print("---- 返回元信息 ----")
    print(json.dumps(meta, ensure_ascii=False, indent=2)[:1500])

    out_path = save_result(data, OUTPUT_DIR, src.stem)
    with Image.open(out_path) as im:
        print(f"---- 成功 ----")
        print(f"输出文件: {out_path}  {im.size[0]}x{im.size[1]}  {out_path.stat().st_size / 1024:.1f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
