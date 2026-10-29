# 京东云部署步骤（Ubuntu 22.04 / 24.04）

目标：朋友用 `http://你的公网IP` 打开就能用。不用域名、不用备案。

---

## 0. 买服务器

京东云控制台 → 云主机 / 轻量云主机 → 新建：

- 镜像：**Ubuntu 22.04 LTS**（不要选 CentOS / Windows）
- 配置：**2 核 2G** 就够，1 核 2G 也能跑
- 系统盘：40G
- 计费：按量或包月随你
- 设置 root 密码（或者绑定 SSH 密钥），记下来

买完在控制台找到**公网 IP**，后面用 `你的公网IP` 代替。

---

## 1. 放行端口

京东云的轻量云主机叫「防火墙」，云主机叫「安全组」，位置都在实例详情页。

入站规则加两条：

| 端口 | 来源 | 用途 |
|---|---|---|
| 22 | 0.0.0.0/0（或你的家宽 IP） | SSH 登录 |
| 80 | 0.0.0.0/0 | 网站访问 |

**不要**放行 8000，后端只监听本机，由 nginx 转发。

---

## 2. 登录服务器

Windows 打开 PowerShell：

```powershell
ssh root@你的公网IP
```

第一次会问 `yes/no`，输入 `yes`，然后输密码（输入时屏幕不显示，正常）。

---

## 3. 装系统依赖

```bash
apt update
apt install -y python3 python3-venv python3-pip nginx
python3 -V
```

`python3 -V` 显示 3.10.x 就可以继续。

---

## 4. 上传代码

代码在 GitHub 上，直接在服务器拉取：

```bash
git clone https://github.com/你的用户名/pingtu.git /opt/pingtu
ls /opt/pingtu
```

能看到 `main.py`、`static`、`deploy` 就对了。

**`.env` 不在仓库里**（里面有 Key，已被 `.gitignore` 排除），需要手动创建：

```bash
cat > /opt/pingtu/.env <<'EOF'
ARK_API_KEY=你的Key
ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
ARK_MODEL=doubao-seedream-5-0-flash-260915
COOLDOWN_SECONDS=120
DAILY_LIMIT=200
MAX_UPLOAD_MB=10
OUTPUT_TTL_HOURS=24
ARK_MOCK=0
EOF
```

如果是在网页终端（VNC）里粘贴，开头结尾可能会混入 `^[[200~` 这类乱码，执行一次清理再检查：

```bash
sed -i 's/\x1b\[[0-9;]*[A-Za-z~]//g' /opt/pingtu/.env
cat -v /opt/pingtu/.env
```

确认第一行是干净的 `ARK_API_KEY=...` 即可。

> 仓库是公开的，所以**永远不要把 `.env` 提交上去**。

---

## 5. 装依赖

```bash
cd /opt/pingtu
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -r requirements.txt
```

最后一行看到 `Successfully installed ...` 就对了。

---

## 6. 先手动试跑

```bash
cd /opt/pingtu
.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
```

看到 `Application startup complete` 说明起来了。**再开一个 SSH 窗口**验证：

```bash
curl http://127.0.0.1:8000/healthz
# 期望：{"ok":true}
curl http://127.0.0.1:8000/api/status
# 期望：包含 "daily_remaining":200
```

验证完回到第一个窗口按 `Ctrl+C` 停掉，后面交给 systemd 托管。

---

## 7. 配置 systemd（开机自启、崩溃自动重启）

```bash
# 建一个专用低权限用户，别用 root 跑网站
useradd --system --no-create-home --shell /usr/sbin/nologin pingtu || true

cp /opt/pingtu/deploy/pingtu.service /etc/systemd/system/pingtu.service
chown -R pingtu:pingtu /opt/pingtu
chmod 600 /opt/pingtu/.env

systemctl daemon-reload
systemctl enable --now pingtu
systemctl status pingtu --no-pager
```

看到 `active (running)` 就好了。

> 注意：服务必须是 **单 worker**（配置文件里已经写死 `--workers 1`）。冷却和每日额度存在进程内存里，开多进程会让限制失效。

---

## 8. 配置 nginx

```bash
cp /opt/pingtu/deploy/nginx-pingtu.conf /etc/nginx/sites-available/pingtu
ln -sf /etc/nginx/sites-available/pingtu /etc/nginx/sites-enabled/pingtu
rm -f /etc/nginx/sites-enabled/default

nginx -t
systemctl reload nginx
```

`nginx -t` 必须显示 `syntax is ok` / `test is successful`，否则别 reload。

如果系统开了 ufw 防火墙，再执行：

```bash
ufw allow 80/tcp
ufw allow 22/tcp
```

---

## 9. 验收

1. 电脑浏览器打开 `http://你的公网IP` —— 能看到页面
2. 手机**关掉 WiFi 用流量**再打开一次 —— 确认公网可达
3. 传一张图，点转换，等 15–60 秒出结果
4. 点「下载图片」，确认能存下来

---

## 10. 日常运维

```bash
# 看服务状态
systemctl status pingtu --no-pager

# 实时看日志（排查问题时开着）
journalctl -u pingtu -f

# 改完代码重启
systemctl restart pingtu

# 看今天用了多少张
cat /opt/pingtu/data/usage.json

# 想手动把今日额度清零：删掉用量文件再重启
rm -f /opt/pingtu/data/usage.json && systemctl restart pingtu
```

改代码后的更新流程：本地重新打包 → `scp` 上传 → 服务器解压覆盖 → `systemctl restart pingtu`。

---

## 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| 浏览器打不开 IP | 安全组没放行 80，或 nginx 没起 | 检查安全组；`systemctl status nginx` |
| 502 Bad Gateway | 后端进程没起来 | `systemctl status pingtu`、`journalctl -u pingtu -n 50` |
| 413 Request Entity Too Large | 上传超过 nginx 限制 | 确认用的是 `deploy/nginx-pingtu.conf`，`client_max_body_size 12m` |
| 504 Gateway Timeout | 生成太慢超过 nginx 超时 | 同上看 `proxy_read_timeout 200s` |
| 页面正常但转换报「鉴权失败」 | `.env` 里 Key 不对 | `nano /opt/pingtu/.env` 改完 `systemctl restart pingtu` |
| 按钮一直显示「冷却中」 | 正常，全站 2 分钟一张 | 等倒计时结束 |
| 按钮显示「今日额度已用完」 | 到 200 张了 | 第二天自动恢复，或按上面手动清零 |
| 改完代码没变化 | 没重启服务 | `systemctl restart pingtu` |
| `dpkg was interrupted` | 上次安装被中断 | `dpkg --configure -a` 后重新 `apt install` |
| scp 反复 Password denied | Windows 下密码输入/字符问题 | 配 SSH 密钥免密，见下方「免密登录」 |

### 免密登录（推荐，一劳永逸）

本地 PowerShell 生成密钥：

```powershell
ssh-keygen -t ed25519 -f "$env:USERPROFILE\.ssh\id_ed25519" -N '""'
type "$env:USERPROFILE\.ssh\id_ed25519.pub"
```

复制输出的那一整行（以 `ssh-ed25519` 开头），在服务器上执行：

```bash
mkdir -p ~/.ssh && chmod 700 ~/.ssh
echo '把刚才复制的公钥粘在这里' >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

之后 `ssh`、`scp` 都不再问密码。

---

## 关于备案

- 用 **公网 IP 直接访问**：不需要备案
- 以后想绑**域名**走 80/443：京东云大陆节点必须完成 ICP 备案，否则会被拦截
- 想加 HTTPS：备案后用 `certbot --nginx` 一条命令搞定
