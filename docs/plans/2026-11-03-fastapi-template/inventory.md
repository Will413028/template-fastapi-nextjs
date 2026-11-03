# 附件：Go 版行為盤點（規格來源）

快照：`template-go-nextjs` commit `560106d`（2026-11-03）。去向：保留＝照搬檔案；改寫＝用 FastAPI 重做、行為照 Go 版；刪除＝不帶過來（附理由）。

## HTTP 操作（21 個；指令：在 Go repo 跑 `rg -n 'operationId:' backend/api/openapi.yaml`）

| 方法 | 路徑 | operationId | 去向 | 步驟 | 狀態 |
|---|---|---|---|---|---|
| POST | `/users` | `createUser` | 改寫 | 3 | 未開始 |
| GET | `/users` | `listUsers` | 改寫 | 5 | 未開始 |
| GET | `/users/me` | `getCurrentUser` | 改寫 | 3 | 未開始 |
| DELETE | `/users/me` | `deleteCurrentUser` | 改寫 | 3 | 未開始 |
| GET | `/users/{id}` | `getUser` | 改寫 | 5 | 未開始 |
| PATCH | `/users/{id}` | `patchUser` | 改寫 | 5 | 未開始 |
| DELETE | `/users/{id}` | `deleteUser` | 改寫 | 5 | 未開始 |
| GET | `/users/{id}/sign-in-lock` | `getSignInLock` | 改寫 | 5 | 未開始 |
| DELETE | `/users/{id}/sign-in-lock` | `deleteSignInLock` | 改寫 | 5 | 未開始 |
| POST | `/sessions` | `createSession` | 改寫 | 3 | 未開始 |
| GET | `/sessions` | `listSessions` | 改寫 | 3 | 未開始 |
| DELETE | `/sessions` | `deleteAllSessions` | 改寫 | 3 | 未開始 |
| GET | `/sessions/current` | `getCurrentSession` | 改寫 | 3 | 未開始 |
| DELETE | `/sessions/current` | `deleteCurrentSession` | 改寫 | 3 | 未開始 |
| GET | `/sessions/{id}` | `getSession` | 改寫 | 3 | 未開始 |
| DELETE | `/sessions/{id}` | `deleteSession` | 改寫 | 3 | 未開始 |
| POST | `/items` | `createItem` | 改寫 | 4 | 未開始 |
| GET | `/items` | `listItems` | 改寫 | 4 | 未開始 |
| GET | `/items/{id}` | `getItem` | 改寫 | 4 | 未開始 |
| PATCH | `/items/{id}` | `updateItem` | 改寫 | 4 | 未開始 |
| DELETE | `/items/{id}` | `deleteItem` | 改寫 | 4 | 未開始 |
| GET | `/health` | （非契約） | 改寫 | 1 | 完成 |
| GET | `/health/ready` | （非契約） | 改寫 | 2a、2c | 完成（2a：DB ping 與 503 problem；2c：只接受 loopback） |

## CLI 子命令（來源：`backend/cmd/app/main.go:64-72`）

| 子命令 | 去向 | 步驟 | 狀態 |
|---|---|---|---|
| `serve` | 改寫（uvicorn 啟動） | 1 | 完成 |
| `migrate` | 改寫成 Alembic（D2） | 2a | 完成 |
| `seed-admin` | 改寫 | 3 | 未開始 |
| `recover-admin` | 改寫 | 5 | 未開始 |
| `healthcheck` | 改寫（容器 healthcheck 用） | 1 | 完成 |

## 非後端檔案（照搬，起點 commit `560106d`）

| 路徑 | 去向 | 步驟 | 狀態 |
|---|---|---|---|
| `frontend/` | 保留，程式不改（型別仍從 `backend/api/openapi.yaml` 產生） | 1 | 完成 |
| `frontend/AGENTS.md`、`frontend/README.md` | 保留，Caddy 段改寫（D6） | 1、7 | 進行中 |
| `AGENTS.md`、`README.md` | 改寫（Go 專屬規則換成 Python） | 1、7 | 進行中 |
| `SECURITY.md`、`LICENSE` | 保留（`SECURITY.md` 網址換 repo 名） | 1 | 完成 |
| `bin/hadolint` | 保留 | 1 | 完成 |
| `.claude/`（openspec 指令、`add-crud`、`add-module` 指南） | 步驟 1 不照搬：內容是 Go 的寫法。第一個 module 做完後，依 D7 寫 Python 版的 `add-module` 指南（給 agent 照著做的範例）；openspec 指令不帶過來 | 4 | 未開始 |
| `deploy/postgres/`（initdb 角色腳本） | 保留 | 1、2a | 完成 |
| `deploy/Caddyfile`、compose 的 `caddy` 服務、`docker-compose.dev.yml` 的 caddy override | 刪除（D6：2026-11-03 Will 決定拿掉 Caddy） | 1 | 完成 |
| `docker-compose.yml` 的 `frontend` 服務、固定 IP 與 ipam | 刪除（D6：前端在 Vercel；固定 IP 原本給 `TRUSTED_PROXIES`）；frontend 服務改由 `docker-compose.dev.yml` 完整定義 | 1 | 完成 |
| compose 的 `cloudflared` 服務 | 新增（D6：`profiles: [tunnel]`，Cloudflare 後台管理，`--token-file` 讀 compose secret；路由規則在後台，寫進部署文件；image 釘 digest） | 1 | 完成 |
| `.github/workflows/ci.yml` stack job 的 ports、同源 curl、health、frontend secrets 檢查 | 修改（D6）：ports 改成 `== []` 並用 `--profile tunnel` 的 config；刪除 Caddy 同源 curl；health 改用 `docker compose exec`；frontend 的檢查改對 dev compose | 1 | 完成 |
| 根目錄 `Makefile` 的 `up`、`logs-frontend` | 修改（D6）：`up` 不再印 `http://localhost`，`logs-frontend` 改用 dev compose；新增 `up-prod` | 1 | 完成 |
| `.github/workflows/ci.yml` frontend job、`scripts/check_vercel_config.py` | 修改／新增（D6）：bundle 哨兵檢查；`vercel.json` 的 policy 檢查 | 2、3 | 完成（2c：兩項都做；`ORIGIN_SECRET` 的 bundle 哨兵在 frontend job 的 build） |
| `frontend/src/lib/env.ts`、`frontend/src/features/auth/__tests__/server.test.ts` | 修改（D6）：加 `originSecret()`；加伺服器端帶密鑰的測試 | 3 | 完成（2c 提前做：`315d07c`；2026-11-03 核對） |
| `.github/workflows/ci.yml` stack job 的 secrets、request id、auth curl 流程 | 修改（D6）：backend secrets 加 `origin_secret`；request id 與 auth 流程改用 compose network 上的 curl 容器並帶密鑰 | 2、3 | 進行中（2c 完成 backend secrets 加 `origin_secret`；curl 容器的 request id 與 auth 流程在步驟 3） |
| `frontend/vercel.json` | 新增：一條 `/api/(.*)` route，`dest` 取 `$BACKEND_ORIGIN`，`set` transform 設 `X-Origin-Secret` 為 `$ORIGIN_SECRET`；CI policy job 檢查它（D6） | 2 | 完成 |
| `frontend/next.config.ts` | 修改：第 7-9 行提到 Caddy 的註解改寫；rewrite 維持只在 development 啟用，production 的 `/api` 由 `vercel.json` 轉送（D6） | 2 | 完成 |
| `frontend/src/lib/api/client.ts`、`frontend/src/features/auth/server.ts` | 修改：伺服器端呼叫帶 `X-Origin-Secret`，值取自 `ORIGIN_SECRET`（不帶 `NEXT_PUBLIC_`），`client.ts:5-12` 的 Caddy 註解改寫；驗收：測試確認伺服器端請求帶密鑰，build 後瀏覽器 bundle 裡找不到密鑰的值（mutation：把變數改成 `NEXT_PUBLIC_ORIGIN_SECRET`，bundle 檢查要失敗）（D6） | 3 | 完成（2c 提前做：`315d07c`、bundle 哨兵 `8ac82fa`；`server.ts` 經 `client.ts` 的共用 middleware 帶上，不必改；2026-11-03 核對） |
| `frontend/.env.example` | 修改：`API_URL` 說明改成 backend 公開網址，加 `ORIGIN_SECRET`（D6） | 3 | 完成（2c 提前做：`315d07c`；2026-11-03 核對） |
| `scripts/init_env.py`、根目錄 `.env-example`、`backend/.env-example` | 修改（D6）：產生 `.secrets/origin_secret`；刪除 `TRUSTED_PROXIES`、`SITE_ADDRESS`，加 `APP_ORIGIN` | 2 | 完成（`backend/.env-example` 不存在；`TRUSTED_PROXIES`、`SITE_ADDRESS` 在步驟 1 就沒搬進來） |
| `docs/deploy-vps.md`、`docs/security/threat-model-auth.md`、`docs/privacy/data-inventory.md`、`docs/frontend-known-issues.md` | 改寫成 Vercel + VM + Cloudflare Tunnel（D6） | 7 | 未開始 |
| `.github/dependabot.yml` | 修改：caddy 註解換成 cloudflared（D6） | 7 | 未開始 |
| `docker-compose*.yml`、`Makefile`、`.env-example`、`scripts/init_env.py` | 保留後把 backend 相關部分改成 Python | 1 | 完成 |
| `lefthook.yml` | 改寫 backend 段（gofmt／golangci-lint → ruff／型別檢查） | 1 | 完成 |
| `.github/workflows/ci.yml` | 改寫 backend、rename job；frontend、stack、policy、gate 保留 | 1、6 | 進行中 |
| `.github/workflows/security.yml`、`tool-versions.yml` | 保留（osv-scanner 已涵蓋 `requirements.txt`，要加 `uv.lock`） | 1 | 完成 |
| `scripts/check_commit_policy.py`、`check_locks.py`、`quality-locks.json` | 保留；`check_locks.py` 加 Python 的 suppression 與 pytest 報告 | 1 | 完成 |
| `scripts/rename.py` | 改寫 module path 部分（Python 沒有 module path） | 6 | 未開始 |
| `scripts/tool_versions.py` | 改寫 Go 工具清單為 Python 工具 | 1 | 完成 |
| `docs/rules/`、`docs/security/`、`docs/privacy/`、`docs/deploy-vps.md` | 保留，Go 專屬段改寫 | 2、3、7 | 進行中（2a 改寫 `database-design.md` 的 schema、Migration、連線、角色；2b 改寫 `api-design.md` 的契約驗證、錯誤、分頁 cursor 與 service 回報錯誤；2c 改寫威脅建模的信任邊界、偽造位址、request id、access log 與個資盤點的 client IP、user agent、日誌段；查詢與 Transaction、handler 分層、認證在步驟 3） |
| `docs/adr/2026-10-22-account-abuse-and-lifecycle.md` | 保留（與語言無關的決定） | 1 | 完成 |
| `docs/adr/2026-10-18-api-and-auth-baseline.md` | 保留；D1、D6（IP 來源）、D8 的決定與 Rationale，以及約束、Expected Outcome、Revocation Triggers 中的 Caddy 部分，由本 repo 的 D6 ADR 取代並標修訂；第 13 行與 Related 段指向 `2026-10-18-go-backend.md` 的連結改指本 repo 的 ADR | 1、7 | 進行中 |
| `docs/adr/2026-10-18-go-backend.md` | 刪除（Go 專屬；以本計畫的 D1–D3 ADR 取代） | 7 | 未開始 |
| `backend/` 全部 | 改寫 | 1–5 | 進行中 |

## 舊 FastAPI 實作（tag `archive/pre-go-rewrite`）

只當參考，不當規格：它用 JWT 加 refresh token，Go 版已改成 opaque session（ADR api-and-auth-baseline）。可借用的是專案骨架（`backend/src/core`、`pyproject.toml`、`uv.lock`）與 Alembic 設定。

## Go 專屬引用（照搬後要改；指令：在 Go repo 跑 `rg -n 'template_go_nextjs|template-go-nextjs|setup-go|go vet|golangci|\.go\b|--go-test-report|atlas' .github scripts Makefile lefthook.yml backend/Dockerfile`）

| 檔案與位置 | 內容 | 去向 | 步驟 | 狀態 |
|---|---|---|---|---|
| `.github/workflows/ci.yml:143` | `intruder` 檢查寫死 DB 與角色名 | 換成本 repo 佔位名稱 | 1 | 完成 |
| `.github/workflows/ci.yml` stack job | 用 image 內附 Atlas 套用 migration | 改成 Alembic（D2） | 2a | 完成 |
| `.github/workflows/ci.yml:84-101` rename job | setup-go、`go vet`、module path 檢查 | 改成 Python | 6 | 未開始 |
| `.github/workflows/ci.yml` backend job | Go 工具鏈 | 改成 Python | 1 | 完成 |
| `.github/workflows/security.yml:12` | 註解寫死 `Will413028/template-go-nextjs` | 換 repo 名 | 1 | 完成 |
| 根目錄 `Makefile` 的 `migration`、`generate-migration`、`seed`、`recover-admin` | 呼叫 Go 後端子命令 | 改成 Python 子命令 | 2a、3、5 | 進行中（`migration`、`generate-migration` 在 2a 完成） |
| `scripts/check_commit_policy.py:27` | 寫死 `backend/.golangci.yml` | 改成 Python lint 設定檔 | 1 | 完成 |
| `scripts/check_locks.py:25`、`--go-test-report` | Go suppression 正規式與報告格式 | 改成 Python | 1 | 完成 |
| `lefthook.yml` backend 段 | gofmt、golangci-lint | 改成 ruff、pyright | 1 | 完成 |
| `backend/Dockerfile` | Go build 與 Atlas | 改寫（Python image，不含 Atlas） | 1、2a | 完成 |
| `backend/Makefile` 的 `drift`、`squawk`、`migrations-immutable`、`atlas-age`、`sqlc`、`sqlc-diff` | Atlas 與 sqlc 的檢查 | 換成 Alembic 對應檢查或刪除（D2、D3） | 2a | 完成（drift 換成 catalog 比對與 `alembic check`、`migrations-immutable` 換成 Python 腳本；`atlas-age`、`sqlc`、`sqlc-diff` 刪除；squawk 改成對 Alembic offline 模式輸出的 SQL 執行） |
