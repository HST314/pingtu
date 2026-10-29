# 平涂色块转换器

上传一张图，调用豆包 Seedream 图生图，把照片压成平涂色块风格，结果可下载。

## 运行

```bash
pip install -r requirements.txt
# 编辑 .env，填入可用的 ARK_API_KEY
python -m uvicorn main:app --reload --port 8000
```

打开 http://127.0.0.1:8000

## 配置（.env）

| 变量 | 说明 | 默认 |
|---|---|---|
| `ARK_API_KEY` | 火山方舟 API Key | 必填 |
| `ARK_BASE_URL` | 接口地址 | `https://ark.cn-beijing.volces.com/api/v3` |
| `ARK_MODEL` | 模型 ID | `doubao-seedream-5-0-flash-260915` |
| `COOLDOWN_SECONDS` | 每张图之间的冷却秒数（全站共用） | `120` |
| `MAX_UPLOAD_MB` | 上传大小上限 | `10` |
| `OUTPUT_TTL_HOURS` | 结果图保留小时数 | `24` |
| `ARK_MOCK` | `1` = 不调模型，本地伪造结果，只用于调界面 | `0` |

## 行为说明

- **限流**：全站共用一个冷却窗口，从一次转换被接受时开始计时 2 分钟。冷却期间的请求返回 `429` 并带上 `retry_after`，前端显示倒计时。
- **失败不罚站**：上传格式错误或模型调用失败时，冷却会立即清零，可以马上重试。
- **输入处理**：超过 2048px 的图会等比缩到 2048，再以 JPEG q92 + base64 发给模型。
- **结果**：统一转存为 PNG，`output/{job_id}.png`，24 小时后由后台任务清理。

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 页面 |
| GET | `/api/status` | 冷却剩余秒数、是否忙碌 |
| POST | `/api/convert` | 请求体为图片原始字节，返回 job 信息 |
| GET | `/api/image/{job_id}` | 结果图 |
| GET | `/api/download/{job_id}` | 结果图（附件下载） |

## 验证脚本

```bash
python test_seedream.py            # 自动生成测试图
python test_seedream.py 你的图.jpg  # 用指定图片
python test_seedream.py --url-only # 让接口返回 URL
```
