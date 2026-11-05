# 部署：Vercel＋VM＋Cloudflare Tunnel

前端放 Vercel；backend 與 PostgreSQL 用 Docker Compose 跑在一台 Linux VM（以 Ubuntu 22.04+ 為例），經 Cloudflare Tunnel 對外。決定與取捨見 [ADR 部署拓樸](adr/2026-11-05-vercel-rewrite-and-cloudflare-tunnel.md)。

```
瀏覽器 ──https──▶ Vercel（example.com）
                   ├─ 頁面、SSR ─────────────┐
                   └─ /api/* rewrite ────────┤  都帶 X-Origin-Secret
                                             ▼
                     Cloudflare（api.example.com）──tunnel──▶ cloudflared ──▶ backend:8000 ──▶ postgres
                                                                  （VM 上的 compose，不發佈任何 port）
```

- 瀏覽器只看到 `https://example.com`：頁面與 `/api` 同源，所以 session cookie 是不帶 Domain 的 `__Host-session`，不需要 CORS。`/api/*` 由 `frontend/vercel.json` 的 rewrite 轉給 `BACKEND_ORIGIN`，同一條 route 用 transform 覆寫 `X-Origin-Secret`。
- SSR 不繞經 Vercel 的網域，直接連 `API_URL`（就是 `https://api.example.com`），由伺服器端程式碼自己帶 `X-Origin-Secret`。
- backend 在 `MODE=prod` 拒絕沒有密鑰的 `/api` 請求，帶錯一律 403；只有密鑰正確時才把 `X-Real-IP`（Vercel 會覆寫它）當成使用者位址（`backend/src/app/web/origin.py`）。
- compose 沒有服務發佈 port：Docker 發佈的 port 會繞過 ufw 等主機防火牆（[Docker：Packet filtering and firewalls](https://docs.docker.com/engine/network/packet-filtering-firewalls/)）。VM 對外只需要 SSH；cloudflared 主動往外連 Cloudflare。

## Prerequisites

- VM：1 vCPU / 1GB RAM / 20GB disk、Ubuntu 22.04 或更新版本、SSH 存取權限；不用開 80/443。
- 網域，DNS 由 Cloudflare 管理（tunnel 的公開主機名稱必須在 Cloudflare 的 zone 裡）。
- Vercel 帳號，以及一個指到本 repo `frontend/` 的 Vercel 專案。
- 以下用 `example.com` 代表前端網域、`api.example.com` 代表 backend 的公開主機名稱。

---

## 1. 安裝 Docker 與 Docker Compose

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER

docker compose version
```

> [!NOTE]
> 重新登入 SSH 讓 `docker` 群組權限生效，之後不需再加 `sudo`。

---

## 2. 上傳專案程式碼

```bash
cd /opt
git clone <your-repo-url> myproject
cd myproject
```

主機上需要 `make` 與 Python 3（`make init-env` 用）；uv 與 pnpm 不需要，image 在 Docker 內建置。

---

## 3. 設定環境變數與密鑰

```bash
make init-env
nano .env
```

`make init-env` 從 `.env-example` 建立 `.env`，並在 `.secrets/` 產生三個資料庫密碼檔（superuser、migrate 用的 owner 角色、backend 用的 app 角色）與 `origin_secret`（backend 的 `ORIGIN_SECRET`）。compose 把它們以 secret 掛進各自的容器，服務經 `*_FILE` 讀取，不會出現在 `.env`、環境變數或 URL 裡。重跑不會覆寫既有的檔案。

> [!IMPORTANT]
> 正式環境務必在 `.env` 設定：
> - `COOKIE_SECURE=true`：cookie 名稱為 `__Host-session` 並帶 `Secure`（`MODE=prod` 下設 `false` 會拒絕啟動）
> - `APP_ORIGIN=https://example.com`：瀏覽器看到的前端網域，不帶結尾斜線
>
> `MODE` 不用改：`make up-prod` 一律以 `MODE=prod` 啟動（JSON log、不提供 `/api/openapi.yaml` 與 `/api/docs`、要求來源密鑰）。

> [!WARNING]
> `.secrets/` 裡的資料庫密碼在 postgres volume 第一次初始化時寫進資料庫角色。之後換掉檔案不會改資料庫裡的密碼；不要刪除 `.secrets/`，並把它和資料庫備份一起保存。

---

## 4. 建立 Cloudflare Tunnel

1. Cloudflare 後台 → Zero Trust → Networks → Tunnels → Create a tunnel（類型 Cloudflared），複製 token。
2. 把 token 存到 VM 的 `.secrets/tunnel_token`（`chmod 600`），不要貼進 `.env` 或指令列。
3. 在 tunnel 的 Public Hostname 加一條：主機名稱 `api.example.com`、路徑 `api/*`、服務 `http://backend:8000`。只轉送 `/api/*`：`/health` 與 `/health/ready` 只接受 loopback，backend 不依賴這條規則，但其他路徑本來就不該對外。
4. SSL/TLS → Edge Certificates：開啟 Always Use HTTPS 與 HSTS（`api.example.com` 的 HSTS 由 Cloudflare 送；backend 不送，見 `backend/src/app/web/headers.py`）。

---

## 5. 啟動服務與建立管理員

```bash
# 建置並以 MODE=prod 啟動（含 cloudflared）。migrate 先套用 migration 並授權 app 角色，結束後 backend 才啟動，
# backend 健康後 cloudflared 才連上 Cloudflare
make build
make up-prod

# 建立管理員帳號；密碼從終端機讀取，不會回顯
make seed ADMIN_ACCOUNT=admin

# 管理員全被停用、被節流鎖住或忘記密碼時：替既有的管理員設新密碼、啟用並登出所有裝置
make recover-admin ADMIN_ACCOUNT=admin
```

驗證運行狀態：
```bash
make status
make logs
# 沒帶密鑰直接打 backend：MODE=prod 應為 403
curl -s -o /dev/null -w '%{http_code}\n' https://api.example.com/api/users/me
```

---

## 6. 設定 Vercel

1. 專案的 Root Directory 設為 `frontend`。
2. Settings → Environment Variables，只設在 **Production**（preview 部署不設 `ORIGIN_SECRET`，所以 preview 呼叫不了 API，見 ADR 部署拓樸的 Revocation Triggers）：

   | 變數 | 值 | 誰用 |
   |---|---|---|
   | `BACKEND_ORIGIN` | `https://api.example.com` | `vercel.json` 的 `/api` rewrite |
   | `API_URL` | `https://api.example.com` | SSR 直接呼叫 backend |
   | `ORIGIN_SECRET` | 與 VM 的 `.secrets/origin_secret` 相同 | rewrite 的 transform 與 SSR；勾 Sensitive，不加 `NEXT_PUBLIC_` |
   | `NEXT_PUBLIC_APP_URL` | `https://example.com` | sitemap 與 robots |

   把 `.secrets/origin_secret` 的值貼進 Vercel 時，直接從 VM 複製到剪貼簿（例如 `ssh vm cat /opt/myproject/.secrets/origin_secret | pbcopy`），不要讓它出現在終端機畫面或 shell history。
3. Settings → Functions → Function Region 選離 VM 最近的區域：SSR 每次都直接連 backend，區域離得遠，每次呼叫都多一段往返。
4. Domains 加上 `example.com`。這筆 DNS 記錄在 Cloudflare 上要設成 **DNS only（灰雲）**，不開代理：開了代理，Vercel 看到的來源是 Cloudflare，`X-Real-IP` 就不再是使用者位址，per-IP 限流會讓所有人共用一個 bucket。
5. 部署。`vercel.json` 的形狀由 `scripts/check_vercel_config.py` 在 CI 守住；改它之前先讀那支腳本。

---

## 7. 第一次部署檢查清單

範本本身沒有部署過，下列行為沒有實測（ADR 部署拓樸「未驗證」的部分）。衍生專案第一次部署時逐項確認，任何一項不成立就先停下：第 1 項不成立時改用 ADR 部署拓樸的基準（前後端分子網域加 CORS）。指令裡的 `https://example.com` 換成實際網域，`ADMIN_PASSWORD` 用一個測試帳號的密碼。

- [ ] **1. Cookie 穿過 rewrite**：登入回應的 `Set-Cookie` 回到瀏覽器，之後的請求帶著 cookie 到 backend。
  ```bash
  curl -si -c /tmp/jar -H 'Content-Type: application/json' -d '{"account":"admin","password":"'"$ADMIN_PASSWORD"'"}' https://example.com/api/sessions | grep -i '^set-cookie: __Host-session'
  curl -s -b /tmp/jar -o /dev/null -w '%{http_code}\n' https://example.com/api/users/me   # 應為 200
  ```
- [ ] **2. `X-Real-IP` 是使用者位址**：經 Vercel 與 Cloudflare 兩層後，backend 收到的仍是你的公開 IP。
  ```bash
  curl -s https://example.com/api/users/me -o /dev/null; curl -s https://api.ipify.org; echo
  make logs-backend | grep '"path": "/api/users/me"' | tail -1   # client_ip 應等於上面印出的 IP
  ```
- [ ] **3. `Host` 標頭**：backend 收到的 `Host` 是 `api.example.com`（rewrite 的目的地），不是 `example.com`。backend 不靠 `Host` 組網址，只記下來排錯用。
  ```bash
  # backend 的 access log 不記 Host；暫時以 debug 等級重開 cloudflared，它的 log 會印出轉送的請求與標頭
  docker compose --profile tunnel run --rm -e TUNNEL_LOGLEVEL=debug cloudflared tunnel --no-autoupdate run --token-file /run/secrets/tunnel_token 2>&1 | grep -i 'host' &
  curl -s -o /dev/null https://example.com/api/users/me; sleep 3; kill %1; make up-prod   # 看完恢復原本的 cloudflared
  ```
- [ ] **4. Transform 覆寫 client 帶的密鑰**：client 自己帶錯的 `X-Origin-Secret`，Vercel 的 `set` 會蓋掉它，請求照常成功；直接打 backend 帶錯的值則是 403。
  ```bash
  curl -s -o /dev/null -w '%{http_code}\n' -H 'X-Origin-Secret: wrong' https://example.com/api/users/me       # 應為 401（未登入），不是 403
  curl -s -o /dev/null -w '%{http_code}\n' -H 'X-Origin-Secret: wrong' https://api.example.com/api/users/me   # 應為 403
  ```
- [ ] **5. `no-store` 不被快取**：API 回應帶 `Cache-Control: no-store`，Vercel 沒有快取它。
  ```bash
  curl -sI https://example.com/api/users/me | grep -i -E '^(cache-control|x-vercel-cache):'   # no-store；x-vercel-cache 不是 HIT
  ```
- [ ] **6. SSR 帶密鑰**：登入後直接載入需要登入的頁面，伺服器端呼叫成功（沒帶密鑰時 backend 回 403，頁面會導回登入或顯示錯誤）。
  ```bash
  curl -s -b /tmp/jar -o /dev/null -w '%{http_code}\n' https://example.com/en/overview   # 應為 200，不是導向登入的 307
  make logs-backend | grep '"status": 403' | tail -3                                  # 不應出現 SSR 那次請求
  ```
- [ ] **7. access log 的 `client_ip` 不是 cloudflared**：瀏覽器發出的請求，`client_ip` 是使用者位址，不是 compose network 裡 cloudflared 的位址（`172.` 或 `10.` 開頭）。
  ```bash
  make logs-backend | grep '"client_ip"' | tail -5
  docker compose exec cloudflared cat /etc/hosts 2>/dev/null | tail -1   # cloudflared 自己的位址，用來比對
  ```
- [ ] **8. HSTS**：兩個網域都送 `Strict-Transport-Security`。
  ```bash
  curl -sI https://example.com | grep -i '^strict-transport-security'
  curl -sI https://api.example.com/api/users/me | grep -i '^strict-transport-security'
  ```
- [ ] **9. 改環境變數後要不要 redeploy**：在 Vercel 改一個無害的變數（例如在 `NEXT_PUBLIC_APP_URL` 後面加查詢字串再改回），不 redeploy，觀察 rewrite 與 SSR 是否用到新值；結果決定下面「輪換來源密鑰」第 3 步要不要 redeploy，寫進專案的部署紀錄。
  ```bash
  curl -s https://example.com/sitemap.xml | head -5   # 改值後不 redeploy 時，這裡的網址是新值還是舊值
  ```
- [ ] **10. Tunnel 只轉送 `/api/*`**：其他路徑在 Cloudflare 就被擋，到不了 backend。
  ```bash
  curl -s -o /dev/null -w '%{http_code}\n' https://api.example.com/health   # 不應為 200
  curl -s -o /dev/null -w '%{http_code}\n' https://api.example.com/         # 不應為 backend 的 404 problem（看 Content-Type 不是 application/problem+json）
  ```
- [ ] **11. 跨來源 POST 被擋**：`Sec-Fetch-Site` 與 `Origin` 經 Vercel 與 Cloudflare 後原樣到達 backend，從另一個網域發出的 POST 收到 403 `cross_origin_request`。
  ```bash
  curl -s -H 'Origin: https://evil.example' -H 'Sec-Fetch-Site: cross-site' -H 'Content-Type: application/json' -d '{"account":"x","password":"y"}' https://example.com/api/sessions   # status 403、type 結尾 cross_origin_request
  ```
  再用瀏覽器確認一次：在另一個網域的頁面的 DevTools console 執行 `fetch('https://example.com/api/sessions', {method: 'POST', credentials: 'include', body: '{}', headers: {'Content-Type': 'application/json'}})`，Network 分頁裡的回應應為 403。

---

## 8. 輪換來源密鑰

backend 的 `ORIGIN_SECRET` 接受逗號分隔的多個值（目前的與上一個），所以可以不停機輪換：

1. 產生新值，寫成 `新,舊` 到 VM 的 `.secrets/origin_secret`：`printf '%s,%s' "$(openssl rand -hex 32)" "$(cat .secrets/origin_secret)" > .secrets/origin_secret.new && mv .secrets/origin_secret.new .secrets/origin_secret`
2. 重啟 backend：`docker compose up -d --force-recreate backend`。
3. 把 Vercel 的 `ORIGIN_SECRET` 改成新值（逗號前那段）；檢查清單第 9 項確認需要時就 redeploy。
4. 確認 `make logs-backend` 沒有新的 403 之後，把檔案改成只有新值，再重啟 backend 一次。

---

## 9. 更新版本

```bash
git pull
make build
make up-prod
```

`make up-prod` 會先跑 migrate（套用新的 migration），再重建 backend。backend 收到 SIGTERM 後最多花 30 秒處理完進行中的請求；新的 backend 健康之前，經 tunnel 進來的請求會失敗（沒有 proxy 暫留請求）。單一 backend 不保證零停機。前端由 Vercel 在 push 到正式分支時部署；契約改了的話，backend 先部署再部署前端。

---

## 10. 資料庫自動備份 (Cron Job)

請務必設定資料庫定時備份。備份以容器內的 superuser 執行（`POSTGRES_USER`，只用於初始化與維運）：

```bash
mkdir -p /opt/backups
```

建立備份腳本 `/opt/myproject/backup.sh`：

```bash
#!/bin/bash
set -euo pipefail
cd /opt/myproject
BACKUP_DIR="/opt/backups"
DATE=$(date +%Y%m%d_%H%M%S)
FILENAME="${BACKUP_DIR}/db_backup_${DATE}.dump"

# -T：不配置 TTY，輸出才不會被改寫換行；custom format 可用 pg_restore 選擇性還原
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB"' > "$FILENAME"

# 僅保留最近 7 天的備份
find "$BACKUP_DIR" -type f -name "*.dump" -mtime +7 -delete

echo "Database backed up to ${FILENAME}"
```

設定執行權限並加入 crontab（每天凌晨 3 點）：
```bash
chmod +x /opt/myproject/backup.sh
crontab -e
# 加入：
0 3 * * * /opt/myproject/backup.sh >> /var/log/db_backup.log 2>&1
```

還原到同一個 stack（會覆蓋現有資料）：
```bash
docker compose exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists' < /opt/backups/db_backup_<DATE>.dump
```

---

## 11. 維運常用指令

```bash
make up-prod          # 啟動（背景，含 tunnel）
make down             # 停止（保留資料）
make down-v           # 停止並刪除 volume（⚠️ 資料庫會清空）
make logs             # 查看所有日誌
make logs-backend     # 查看後端日誌
make build            # 重新建置 image
make restart          # 重啟服務
make migration        # 手動套用 migration（make up-prod 已會自動執行）
```

本指南假設資料庫是 compose 內的 PostgreSQL：角色由 `deploy/postgres/initdb/10-roles.sh` 在 volume 第一次初始化時建立。改用 managed PostgreSQL 時這支腳本不會執行，owner 與 app 角色要先以 superuser 照該腳本的內容建立。連線也要加密：`DATABASE_URL` 與 `MIGRATE_DATABASE_URL` 改用 `sslmode=verify-full`，並拿掉 compose 裡的 `DATABASE_PLAINTEXT=same-host`；沒加密時 backend 與 migrate 拒絕啟動。
