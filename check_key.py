"""用官方 SDK 原样跑一遍 p.md 里的例程，单独验证 Key 是否有效。

    python check_key.py
"""

import os
from pathlib import Path

from openai import OpenAI

ROOT = Path(__file__).resolve().parent
for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

key = os.environ.get("ARK_API_KEY", "")
print("Key :", key)
print("长度:", len(key))

client = OpenAI(
    base_url="https://ark.cn-beijing.volces.com/api/v3",
    api_key=key,
)

try:
    resp = client.images.generate(
        model="doubao-seedream-5-0-flash-260915",
        prompt="生成狗狗趴在草地上的近景画面",
        size="2K",
        response_format="url",
        extra_body={
            "image": "https://ark-project.tos-cn-beijing.volces.com/doc_image/seedream4_imageToimage.png",
            "watermark": True,
        },
    )
    print("成功:", resp.data[0].url)
except Exception as e:
    print("失败:", type(e).__name__)
    print(str(e)[:500])
