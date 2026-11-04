# template-fastapi-nextjs：用 FastAPI 重做 template-go-nextjs 的後端

**Goal：** 這個 repo 和 `template-go-nextjs` 對外行為相同：同一份 API 契約（21 個操作加 health）、同一個 Next.js 前端不改程式就能跑、`make dev` 起整個 stack、`make rename NAME=x` 產生衍生專案；CI 綠燈。驗收見步驟 8。

**上層：** 見本機個人脈絡檔（主清單在 repo 外）。沒有主計畫，下一段暫代。

**附件：** [`2026-11-03-fastapi-template/inventory.md`](2026-11-03-fastapi-template/inventory.md)：Go 版行為盤點，快照 `template-go-nextjs@560106d`。

## 全域約束與完成定義（暫代主計畫）

- 規格是 Go 版 `560106d` 的行為：`backend/api/openapi.yaml`、`docs/adr/` 三份 ADR、`docs/rules/`、Go 測試。舊 FastAPI 實作（tag `archive/pre-go-rewrite`）只當骨架參考，不當規格。
- 前端應用程式不改，前端仍從 `backend/api/openapi.yaml` 產生型別（`frontend/scripts/gen-api.mjs:15`）。D6 允許的例外：部署設定（新增 `frontend/vercel.json`；`frontend/.env.example` 的 `API_URL` 與 `ORIGIN_SECRET` 說明），以及伺服器端呼叫加上來源密鑰（`frontend/src/lib/env.ts` 加 `originSecret()` getter，因為只有它能讀 `process.env`（`frontend/biome.json:63` 的 `noProcessEnv`）；`frontend/src/lib/api/client.ts`、`frontend/src/features/auth/server.ts` 只在伺服器端送 `X-Origin-Secret`，值取自不帶 `NEXT_PUBLIC_` 的 `ORIGIN_SECRET`；測試加在既有的 `frontend/src/features/auth/__tests__/server.test.ts`），以及改寫提到 Caddy 或 `TRUSTED_PROXIES` 的註解（`client.ts:5-12`、`next.config.ts:7-9`）。其他前端改動代表契約漂移了，先停下回報。
- E2E 與瀏覽器測試不在本機跑，只在 CI 跑。
- 完成定義（每一步）：pytest 對該步的操作有行為測試；`make -C backend check` 綠燈；`check_locks.py` 的 backend 測試數鎖調到新數字；inventory 該列的「狀態」欄改成完成。

## 前提（開工時核對過，2026-11-03）

- 會改到或取代的既有部署是否在線：無既有部署，全新 repo，不受回滾與相容性窗口約束。
- 前端只依賴 OpenAPI 契約與 cookie 行為，不依賴後端語言（來源：`frontend/package.json` 用 `openapi-typescript` 加 `openapi-fetch`；未確認前端有沒有依賴 Go 專屬的錯誤細節，由步驟 0 確認）。
- 工具鏈預設（可推翻）：Python 3.14、uv、ruff（lint 加 format）、pyright、pytest 加 testcontainers-python、uvicorn。
- 安全參數沿用 Go 版 ADR `api-and-auth-baseline`：opaque DB session、`__Host-session` HttpOnly cookie、密碼 argon2id、NIST 800-63B-4 密碼規則；不另開決定題。
- GitHub repo 建立在 `Will413028/template-fastapi-nextjs`，用 dev branch 模型（預設，可推翻）。
- 部署目標（2026-11-03 Will，D6）：前端放 Vercel（理由：前端免維運），backend 與 Postgres 放一台 VM，backend 經 Cloudflare Tunnel 對外。
- 未驗證（2026-11-03 Will：template 不實際部署到 Vercel 與 Cloudflare，所以不實測）：Vercel 的 external rewrite 會轉送 `Cookie` 並把 `Set-Cookie` 傳回瀏覽器（Vercel 文件沒寫）；請求經 Vercel 與 Cloudflare 兩層之後，backend 收到的 `X-Real-IP` 仍是使用者 IP、`Host` 是什麼；`vercel.json` 的 `routes`（`dest` 與 transform 用環境變數）能和 Next.js 產生的路由並存；前端網域的 DNS 記錄不開 Cloudflare 代理時，Vercel 看到的是使用者 IP（部署文件規定不開代理）；Vercel 環境變數改值後，rewrite 的 transform 與 SSR function 要不要 redeploy 才生效（決定密鑰輪換的順序）；瀏覽器送的 `Sec-Fetch-Site` 與 `Origin` 經 Vercel 與 Cloudflare 後原樣到達 backend（CSRF 防護靠它們；被剝掉時跨來源 POST 會被當成非瀏覽器而放行，步驟 3b design review 2026-11-03 補列）。這幾項由步驟 7 寫成部署文件的「第一次部署檢查清單」，衍生專案第一次部署時逐項確認；其中 cookie 不能穿過 rewrite 時，改走 D6 的 B。
- 已讀來源，2026-11-03（[Vercel Request headers](https://vercel.com/docs/headers/request-headers)、[Rewrites](https://vercel.com/docs/routing/rewrites)、[Static IPs](https://vercel.com/docs/networking/static-ips)、[Limits](https://vercel.com/docs/limits)）：Vercel 覆寫 `X-Forwarded-For`，client 偽造的值傳不到上游；`X-Real-IP` 是使用者 IP；出口 IP 不固定，付費 Static IPs 只涵蓋 Functions；external rewrite 單一請求上限 120 秒；2026-04-06 之後建立的專案，external rewrite 預設照上游 `Cache-Control` 快取。

**驗收基線：** 新 repo 未建，無。規格那一側（Go 版 `560106d`）：CI 6 個 job 全綠（run `41491264746`），後端 241 個測試、前端 40 個。

## 本階段依賴的不變量

| 不變量 | 依賴它的機制 |
|---|---|
| API 契約與 Go 版 `560106d` 相同（路徑、方法、request／response schema、problem code）；D11 起是分家點，不再跟 Go 版之後的改動 | `frontend/src/lib/api/`、前端生成型別、步驟 1–5 的契約測試 |
| 錯誤一律 RFC 9457 problem 加機器可讀的 `code` | 前端錯誤處理、`docs/rules/api-design.md`、步驟 2b 的 exception handler |
| DB 分 owner 與 app 兩個角色，app 角色只有 DML | `deploy/postgres/initdb/10-roles.sh`、compose 的 `MIGRATE_DATABASE_URL`／`DATABASE_URL`、CI stack job 的 `intruder` 檢查 |
| 同源：瀏覽器看到前端與 `/api` 同一個網域，由 Vercel rewrite 轉送 `/api/*`（D6） | `frontend/vercel.json` 的 `/api/` route、CI policy job 的 `vercel.json` 檢查、cookie 的 `__Host-` 前綴、CSRF 防護（`Sec-Fetch-Site`）、前端 `src/proxy.ts` 的 CSP `connect-src 'self'` |
| backend 只信任帶正確來源密鑰的請求所附的 `X-Real-IP`（D6，取代 Go 版的 `TRUSTED_PROXIES`） | `vercel.json` 設定 `X-Origin-Secret` 的 transform、前端伺服器端呼叫帶的 `X-Origin-Secret`、backend 的來源密鑰 middleware、per-IP 限流的 key、access log 的 `client_ip` |
| 來源密鑰只存在 Vercel 環境變數與 VM 的 compose secret（`.secrets/origin_secret`），不進 git | `scripts/init_env.py`、compose 的 `origin_secret` secret、`vercel.json` 以 `$ORIGIN_SECRET` 引用 |
| backend 不發佈任何 port，只經 Cloudflare Tunnel 對外 | compose 的 `cloudflared` 服務（`profiles: [tunnel]`、`--token-file`）、backend 服務沒有 `ports`、CI stack job 的「沒有服務發佈 port」檢查（用 `--profile tunnel` 的 config） |
| health 端點不對外：`/health`、`/health/ready` 只接受 loopback 來源 | backend 的 health 路由、步驟 2c 的 loopback 測試；Cloudflare 後台的 `/api/*` 路由規則是第二層，寫進部署文件的檢查清單 |
| 契約檔固定在 `backend/api/openapi.yaml` | `frontend/scripts/gen-api.mjs:15`、CI `pnpm gen:api:check`（`ci.yml:171`）、`lefthook.yml:44` |
| DB schema 與 Go 版等價（表、欄位、nullable、約束、外鍵；D2 選 Alembic，所以靠測試守住，不是共用檔案） | 步驟 2a 的 Alembic baseline revision、SQLAlchemy 模型、對照 Go 版 `backend/migrations/` 的 schema 比對測試 |
| 佔位名稱 `template-fastapi-nextjs`／`template_fastapi_nextjs` 只出現在 rename 腳本認得的檔案 | `scripts/rename.py` 的 `STACK_FILES`、`BRAND_FILES`、CI rename smoke job |

## 規模

| | auth（users、sessions） | items | 帳號濫用與管理 | 產生計數的位置 |
|---|---|---|---|---|
| HTTP 操作 | 11 | 5 | 5 | inventory.md「HTTP 操作」表的「步驟」欄（`getUser` 2026-11-03 從步驟 5 移到 3b） |

## 範圍

- 做：inventory 裡每一列「改寫」與「保留」。
- 不做：在兩個 template 之間共用程式碼或 submodule（2026-11-03 決定各自獨立）；改前端功能。
- 延後：Go 版 Pending「延後」段的全部項目（email 驗證、MFA、OTel、背景工作等），觸發條件照 Go 版。

## 步驟

- [x] **0. spike：契約與工具**（被擋於：無）
  - 範圍：在暫存目錄確認三件事，產出事實，不寫產品程式碼。(a) FastAPI 輸出的 OpenAPI 3.1 能不能讓現有前端的 `openapi-typescript` 產出相同型別（拿 `items` 兩個操作試）；(b) 用手寫 `openapi.yaml` 驗證 FastAPI request／response 的做法有哪些可行（例如 schemathesis、openapi-core middleware）；(c) Atlas 套用 Go 版 migrations 後，SQLAlchemy 能不能 reflect 出同一張表。
  - 消費端：無（repo 未建）。
  - 驗收：每項寫一段結論加上可重跑的指令與原始輸出，存成 `2026-11-03-fastapi-template/spike.md`。
  - 停止條件：(a) 和 (b) 都不可行，代表前端無法不改，回報 Will。
- **0b. spike：Vercel 與 Cloudflare Tunnel 的實際行為**：取消（2026-11-03 Will：template 不實際部署到 Vercel 與 Cloudflare）。原本要實測的項目見前提段「未驗證」那條，由步驟 7 寫成部署文件的第一次部署檢查清單。
- [x] **1. walking skeleton**（被擋於：D1、D2、步驟 0）
  - 範圍：先跑 quality-baseline skill 決定第一版 CI 要有哪些檢查；`git init`（`.gitignore` 要含 `AGENTS.local.md`）、建 GitHub repo、dev branch；從 Go 版 `560106d` 照搬 inventory 的「保留」檔案；FastAPI 只做 `GET /health`、`serve`、`healthcheck`；Dockerfile、compose 起得來（D6：base compose 只有 postgres、migrate、backend 與 `cloudflared`，沒有 caddy 與 frontend，沒有任何服務發佈 port，刪除給 `TRUSTED_PROXIES` 用的固定 IP 與 ipam；`cloudflared` 放在 `profiles: [tunnel]`，只有正式部署用 `--profile tunnel` 啟動，是在 Cloudflare 後台管理的 tunnel，用 `--token-file /run/secrets/tunnel_token`（compose secret）啟動；「只把 `/api/*` 轉給 backend」的路由規則設在 Cloudflare 後台，寫進部署文件，不在 repo 裡（2026-11-03 Will），backend 不依賴它（步驟 2 的 `/health` 只接受 loopback）；dev compose 自己完整定義 frontend 服務，由 `next dev` 在 `127.0.0.1:3000` 提供頁面，`/api` 走 `next.config.ts` 既有的 development rewrite）；CI stack job 改寫：「只有 caddy 發佈 port」改成「沒有服務發佈 port」（`== []`），刪除「Caddy 同源提供 API 與頁面」的 curl 檢查（同源改由步驟 2 的 `vercel.json` 檢查與部署文件的第一次部署檢查清單涵蓋），health 用 `docker compose exec -T backend app healthcheck` 檢查；「每個服務只拿到自己的 secrets」裡檢查 frontend 的 4 行（Go 版 `ci.yml:117-121`）改成對 `docker compose -f docker-compose.yml -f docker-compose.dev.yml config` 檢查 dev 的 frontend 沒有 DB 變數與 secret；ports 與 log 輪替兩個檢查改用 `docker compose --profile tunnel config` 的輸出，才看得到 cloudflared；根目錄 `Makefile` 的 `up` 不再印 `http://localhost`，`logs-frontend` 改用 dev compose，新增 `up-prod`（帶 `--profile tunnel`、`MODE=prod`）；CI 的 backend job 改成 Python（ruff、pyright、pytest、pip-audit 或 osv-scanner）；lefthook backend 段改寫；`check_locks.py` 支援 pytest 報告與 Python suppression（`# noqa`、`# type: ignore`），`quality-locks.json` 重設為新 repo 的實際數字（移除 `go` 鍵、加 `python` 鍵，backend 測試數改成實際值；commit 寫明這是新 repo 的起始基線，不是放寬）；inventory「Go 專屬引用」表標步驟 1 的列全部處理。
  - 消費端：inventory「Go 專屬引用」表。
  - 不能動：`frontend/` 程式。
  - 驗收：`make -C backend check` 在本機綠燈；CI 的 backend、frontend、policy、stack job 綠燈，stack job 用 `docker compose exec` 跑 healthcheck 成功（不帶 `--profile tunnel`，因為 CI 沒有 tunnel token）；mutation：讓 `/health` 回 500，stack job 要失敗；mutation：給 backend 加一個 `ports`，「沒有服務發佈 port」檢查要失敗；mutation：給 cloudflared 加一個 `ports`，同一個檢查也要失敗。未涵蓋：`cloudflared` 在 CI 不會啟動，正式路徑由部署文件的第一次部署檢查清單涵蓋；dev 走 `next dev` rewrite、正式走 Vercel，兩者不是同一套 proxy（D6 已接受的代價）。
  - 需要人做的事：無（建 repo 是可逆的）。
- [ ] **2. 共用平台**（被擋於：步驟 1、D2、D3）：一個 session 做不完，2026-11-03 Will 同意拆成 2a、2b、2c，依序做，各自有驗收。
  - 依賴套件（2026-11-03 Will 核准；PyPI 發佈日都超過 7 天）：`sqlalchemy[asyncio]==2.1.6`、`alembic==1.20.1`、`asyncpg==0.32.0`、`openapi-core==0.23.1`、`testcontainers==4.15.0`（dev）。`openapi-core` 不帶 `[starlette]`：那個 extra 限制 `starlette<1.1.0`，實測會把 starlette 從 1.10.0 降到 1.0.1，而 1.0.1 在 OSV 有 8 筆漏洞（例如 GHSA-jp82-jpqv-5vv3），osv-scanner 會擋；改在 `app.web` 寫一個實作 openapi-core Request 協定的 adapter。`pydantic-settings==2.16.1` 核准了但不裝，理由見 2a 的設定。
  - 消費端（三個子步驟共用）：`rg -n 'problem|cursor|ratelimit' backend/` 與前端 `rg -n 'code' frontend/src/lib/api`。
  - 不能動：problem 的 `type`／`code` 值必須和 Go 版 `backend/internal/httpapi/problem.go` 相同。
- [x] **2a. 資料庫**（被擋於：步驟 1、D2、D3）
  - 範圍：
    - 設定：`app.platform.config` 照 Go 版 `config.go` 加上 serve 的 `DATABASE_URL`、`DATABASE_PASSWORD`、`DATABASE_PLAINTEXT`、`DB_MAX_CONNS`、`DB_STATEMENT_TIMEOUT`，與 migrate 的 `MIGRATE_DATABASE_URL`、`MIGRATE_DATABASE_PASSWORD`、`DB_APP_USER`：任何 `KEY` 都可以改給 `KEY_FILE`，兩者都設是錯誤；URL 不得含密碼、不得帶 driver 後綴；不是 `sslmode=verify-full` 時必須有 `DATABASE_PLAINTEXT=same-host`；所有問題收集後一次回報。session、cookie、限流的設定在用到它們的步驟才加。繼續用標準函式庫、不用 pydantic-settings：上面這些規則它都不提供，要自己寫，它只省掉型別轉換；但它會讓每幾秒跑一次的 `app healthcheck` 多載入 pydantic（`.importlinter` 的 `light-healthcheck` 合約就是為了避免這種事）。
    - DB 連線：`app.platform.db`，SQLAlchemy async engine 加 asyncpg，密碼不進 URL；每條連線設 `statement_timeout`、`lock_timeout` 3s、`idle_in_transaction_session_timeout` 30s、`TimeZone=UTC`（Go 版 `db.go`）；`sslmode=verify-full` 時驗證伺服器憑證。共用的 `Base` 也放這裡。
    - 模型：`app.modules.auth.models`（users、sessions、sign_in_throttles、trusted_devices、audit_events）與 `app.modules.items.models`（items），router 與 service 在步驟 3、4 才加。
    - Alembic：環境放在 `backend/src/app/main/migrations/`，加一個 baseline revision，內容等價於 Go 版 6 個 migration 套用後的結果（沒有 `login_throttles`）。`app migrate` 用 owner 角色跑 `alembic upgrade head`，再照 Go 版 `migrate.go` 給 app 角色 DML，並套用 append-only 規則（`audit_events` 的表註解）。
    - 檢查：`alembic check`（模型和 revision 一致）寫成 `db` 測試；新增 `scripts/check_migrations_immutable.py`：base 上已有的 revision 檔不能修改、刪除或改名，backend `make check` 呼叫它（取代 Go 版的 `check-migrations-immutable.sh`）。squawk 與 `atlas-age` 不移植：前者檢查的是 SQL 檔，後者檢查的是 Atlas 版本，Alembic 的 revision 用不到。
    - `/health/ready`：DB ping，失敗回 503 `/problems/unavailable`；`app healthcheck` 改打 `/health/ready`（Go 版 `main.go:208`）。problem 型別表與回應寫成 `app.web` 的最小版本，exception handler 與 request id 在 2b 加。
    - compose 與 Makefile：恢復 `migrate` 服務（owner 角色、只掛 `db_owner_password`），backend 改成 `depends_on: migrate: service_completed_successfully`；根目錄 Makefile 加 `migration`（跑 migrate）與 `generate-migration MSG=<名稱>`（在 migrate 容器裡跑 `alembic revision --autogenerate`，掛進 revision 目錄）。
    - 資料庫測試（testcontainers）標成 pytest marker `db`；本機的 `make check` 與 lefthook pre-push 跑 `-m "not db"`，CI 的 backend job 跑全部，並檢查 `db` 測試真的有執行（`check_locks.py` 的測試數）。原因：本機不起 Postgres 容器（2026-11-03 Will，本機負載高）。
    - CI stack job：migrate 只拿到 `db_owner_password`；migrate 的 exit code 是 0；backend ready。
  - 驗收：schema 比對測試：一個資料庫依序執行 Go 版 `560106d` 的 6 個 migration SQL（複製到測試 fixture），另一個用 Alembic 套本 repo 的 revision，比對兩邊 catalog 的表、欄位（型別、nullable、default）、約束（`pg_get_constraintdef`）、索引（`pg_get_indexdef`）與表註解，結果要一致。改成直接執行 SQL、不跑 Atlas，因為這 6 個檔沒有 Atlas 指令（`rg 'atlas:'` 沒有輸出，2026-11-03），兩種跑法結果相同，CI 也不必下載 Atlas。mutation：在 revision 拿掉一個 CHECK，比對要失敗。`alembic check` 的 mutation：改模型但不加 revision，它要失敗。migrate 測試：app 角色可以對一般表做 DML，對 `audit_events` 不能 UPDATE 或 DELETE；超過保留期的列被刪掉；重跑結果相同；mutation：拿掉 append-only 的 REVOKE，測試要失敗。設定測試涵蓋每一種錯誤。`/health/ready` 在 DB 可用時回 200、不可用時回 503 problem。`check_migrations_immutable.py` 有單元測試，mutation：改一個已在 base 的 revision，它要失敗。CI stack job 的 app 角色 `CREATE TABLE intruder` 要失敗；mutation：把 app 角色改成 owner，那個檢查要轉紅。
  - 停止條件：baseline revision 和 Go 版 schema 對不上，而且差異來自 Alembic 或 SQLAlchemy 表達不了的東西時，停下回報。
  - 執行中的決定與偏離（2026-11-03）：
    - `make migration MSG=` 不在 migrate 容器裡跑，改在主機上用 db 測試的 testcontainers PostgreSQL 產生（`backend/tests/generate_migration.py`），不依賴 stack 在跑。本機不起容器，所以在這台機器上 revision 是手寫的，由 CI 的 catalog 比對與 `alembic check` 驗證；CI 另跑一次 `make migration` 確認產生工具能用、而且模型與 revision 一致時產生的是空 revision。
    - 模型與 revision 的一致性有兩道互相重疊的檢查：catalog 比對涵蓋 autogenerate 看不到的 CHECK 與 default；`alembic check` 留著，因為它的錯誤訊息用 autogenerate 的語彙列出差異。
    - `alembic_version` 放在 owner 擁有的 `alembic` schema，不在 `public`，app 角色碰不到（Go 版 `TestBaselineAppliesWithCommunity` 對 Atlas 版本表的同一個要求；design review 2026-11-03）。
    - 不可修改檢查的基準照 Go 版：PR 用 base 分支，push 用 `github.event.before`，所以推上 `dev` 的 revision 也不能再改；本機 `make check` 預設 `origin/main`。
    - 連線池加 `pool_pre_ping` 與 `pool_recycle` 1h，與 pgxpool 的預設（取出閒置連線前 ping、`MaxConnLifetime` 1h）對齊。
    - `app migrate` 刻意不設整體上限：Go 版 30 分鐘的 `applyTimeout` 限制的是 Atlas 子程序，Python 版沒有子程序；等鎖由 `lock_timeout` 5s 限制。觸發條件：出現長時間的 backfill revision 時，再決定要不要用 `asyncio.timeout` 包住。
    - squawk 移植（2026-11-03 Will）：原本不移植的理由「squawk 檢查 SQL 檔，revision 是 Python」不成立。`tests/revision_sql.py` 用 Alembic 的 offline 模式把每支 revision 輸出成各自的 SQL 檔（拿掉 Alembic 自建的版本表那一句），`make squawk` 交給 `squawk-cli` 2.70.0（npm，`backend/tools/`，2026-11-03 Will 核准；與 Go 版同版），設定沿用 Go 版的 `.squawk.toml`（`assume_in_transaction` 改成 false，因為輸出本身帶 BEGIN／COMMIT）。不需要資料庫，所以在本機的 `make check` 跑。mutation：probe revision 刪欄位、對既有表建非 CONCURRENTLY 索引、加沒有 default 的 NOT NULL 欄位、改欄名都被擋下，空 revision 與 `autocommit_block` 的 CONCURRENTLY 索引通過。
    - `app.modules.auth`、`app.modules.items` 先有只放 docstring 的 `router.py`、`service.py`：`.importlinter` 的 `module-layers` 把這兩層列為必要層，不為暫時狀態放寬合約。
- [x] **2b. HTTP 共用層**（被擋於：2a）
  - 範圍：problem+json exception handler（`type`、`title`、`code` 與 Go 版 `problem.go` 相同，含 `request_id`）；request id 一律由 backend 產生、不信任傳入值；安全 header，含 `Cache-Control: no-store`（Go 版 `securityHeaders`，`middleware.go:99`）；用 openapi-core 依 `backend/api/openapi.yaml` 驗證 request（自寫 adapter，見上面的依賴說明；正式環境只驗 request，response 驗證放在測試裡，`spike.md` (b)），錯誤轉成 `/problems/validation`；cursor 分頁工具。
  - 驗收：Go 版 `problem_test.go`、`problem_contract_test.go`、`validate_test.go` 的同名案例；mutation：讓驗證失敗時回 openapi-core 原本的 `{"errors": [...]}`，契約測試要失敗；mutation：信任傳入的 `X-Request-ID`，request id 測試要失敗。
  - 執行中的決定與偏離（2026-11-03）：
    - 組裝順序（外到內）：`RequestIdMiddleware` → `SecurityHeadersMiddleware` → `RecoverMiddleware` → `ContractValidation` → FastAPI 的 exception handler → 路由。未預期的 exception 由自寫的 `RecoverMiddleware` 轉成 500 problem，不交給 Starlette 的 `ServerErrorMiddleware`：後者在所有 middleware 外面，它回的 500 拿不到 request id 與安全 header。response 已開始後的失敗照 Go 版切斷連線，不附加 problem。
    - 路由比對與 405 由 openapi-core 依契約判斷（Go 版也是 validator 先回 404／405）；契約外的路徑由 Starlette 的 404／405 轉成 problem。
    - 契約有 security 的 operation，openapi-core 目前只檢查 `__Host-session` cookie 存不存在，缺了回 401（Go 版 `requireSession` 的形狀）。換成看 principal 的工作列在步驟 3 的範圍 (a)。
    - JSON 讀取：`application/json`、`application/merge-patch+json`、`application/problem+json` 都註冊嚴格的讀取器。openapi-core 預設把 merge-patch 當 bytes（契約的兩個 PATCH 會全部判成型別錯誤），而且接受 `NaN`／`Infinity`；非 UTF-8 的 body 回 400（RFC 8259 §8.1；Go 的 `encoding/json` 會把無效位元組換成 U+FFFD，這裡刻意較嚴）。
    - 欄位錯誤的 `detail` 由規則產生（例如「must be at least 3 characters long」），不用 jsonschema 的訊息：後者會帶出輸入值，可能是密碼。Go 版用 kin-openapi 的 `Reason`，同樣不含輸入值。
    - 路由自己的型別比契約嚴、FastAPI 解析失敗時（`RequestValidationError`）回 422 `invalid_type`，不回 FastAPI 的 `{"detail": [...]}`（Go 版 `bind.go`）。
    - 契約檔的路徑相對於工作目錄（`api/openapi.yaml`）：`make run` 在 `backend/`，image 的 `WORKDIR /app` 已 `COPY api api`。
    - MODE=dev 提供 `/api/openapi.yaml` 與 Swagger UI（Go 版 `docs.go`，同版本同 SRI hash），在驗證之外；prod 下這兩個路徑是 404。
    - 測試：`tests/apitest.py` 的 `checked_client` 對每個 problem 檢查契約的 enum（Go 版 `checkProblems`），並用 openapi-core 驗證每個契約 operation 的 response（`spike.md` (b)）；測試契約照搬 Go 版 `testdata/test-openapi.yaml`，加一個 merge-patch 的 PATCH 與每個 operation 的 default problem response。
    - 沒移植的 Go 測試與理由：信任 proxy 的 request id 與 `TestClientIP`（D6，request id 一律產生；client IP 在 2c）；`TestPanicAbortHandlerPropagates` 與「wrapped error 保留 kind」（Go 專屬的 `http.ErrAbortHandler` 與 `%w` 包裝，Python 直接 raise `AppError`）；`TestErrorAfterResponseStartedAborts` 併入「response 開始後失敗就切斷」一案；`TestResponseControllerReachesConnection`（Go 專屬）；log 相關的三個（`TestPanicLogAndAccessLogCarryRequestID`、`TestAccessLogAbortBeforeWriting`）移到 2c，見下。
    - vulture 不認得 openapi-core 透過協定讀的屬性，由 `tests/vulture_whitelist.py` 逐一列出（審查後從全域 `--ignore-names` 改來，避免整個 repo 忽略 `data` 這類常見名稱）。
    - 審查（design-review、`/code-review high`、diff 視角各一輪）之後的修正：Starlette／FastAPI 自己丟的其他 HTTPException 依 status 對應 problem 並保留 header（405 的 `Allow`）；`Retry-After` 只在等待時間大於 0 時送（Go 版同）；路由解析時缺值回 `required`；`app serve` 讀不到或讀到無效的契約時印 `serve: ...` 並回 1。照 Go 版維持、不改：client 讀 body 中途斷線回 500 並記 error（Go `readErr`）；openapi-core 回報未知種類的錯誤時回 500（Go「新種類的錯誤要被看見」）；驗證層的 405 不帶 `Allow`（Go 版也沒有）。
- [x] **2c. D6 邊界與限流**（被擋於：2b）
  - 範圍：per-IP 限流（Go 版 `ratelimit`），以及以下 D6 新增的範圍：
    - 來源密鑰 middleware：`/api/*` 請求的 `X-Origin-Secret` 不符就回 403 problem，`type` 用既有的 `/problems/forbidden`、不帶 `code`（契約的 code 清單不變）；先轉成 bytes 再用 `hmac.compare_digest` 比對，非 ASCII 的 header 直接視為不符；`ORIGIN_SECRET` 可以放兩個值（逗號分隔，目前值與上一個值），輪換時新舊並存，啟動時有空項目就拒絕啟動；`MODE=prod` 沒設定就拒絕啟動。`MODE=dev` 時，沒帶 header 的請求放行（client IP 用連線來源位址，不採信 `X-Real-IP`），帶了但不符一律 403（2026-11-03 Will，因為 `next dev` 的 rewrite 帶不上 header）。`/health`、`/health/ready` 不檢查密鑰，但只接受來自 loopback 的請求（容器內的 `app healthcheck`），其他來源回 404，不依賴 tunnel 的路由規則（2026-11-03 Will）。
    - client IP 只在密鑰相符時取 `X-Real-IP`，否則用連線來源位址；request id 一律由 backend 產生，不信任傳入值；`Cache-Control: no-store` 沿用 Go 版 `securityHeaders`（`middleware.go:99`），external rewrite 的快取預設因此不會生效。
    - 結構化 log（2026-11-03 從 2b 移入：Go 版 `logx` 與 `accessLog` 原本沒有分到任何步驟，而 access log 要記 client IP，這裡才有）：JSON 格式；每一行帶 `request_id`（2b 的 `app.web.request_id.current_request_id()`）；每個請求一行 access log，欄位 `method`、`path`（不含 query string）、`status`、`duration_ms`、`client_ip`，health 用 debug 等級；response 開始後被切斷的請求記 warning 與已送出的 status。驗收加上 Go 版 `TestPanicLogAndAccessLogCarryRequestID`、`TestAccessLogAbortBeforeWriting` 的同名案例；mutation：拿掉 log 的 request id，對應測試要失敗。
    - 設定與 secret：`scripts/init_env.py` 產生 `.secrets/origin_secret`；compose 給 backend 掛 `origin_secret` secret，加 `APP_ORIGIN` 環境變數（步驟 3 的 CSRF 用）；根目錄 `.env-example` 刪掉 `TRUSTED_PROXIES`、`SITE_ADDRESS`，加上 `APP_ORIGIN`；`backend/.env-example` 同步；CI stack job 的 secrets 檢查改成 `.services.backend.secrets | map(.source) == ["db_app_password","origin_secret"]`（和 Go 版 `ci.yml` 同寫法，`docker compose config` 輸出的 secrets 是物件陣列）。
    - `frontend/vercel.json`：一條 `/api/(.*)` route，`"dest": "${BACKEND_ORIGIN}/api/$1"`，route 的 `env` 含 `BACKEND_ORIGIN`；transform 用 `set` 設 `X-Origin-Secret`，`args` 為 `$ORIGIN_SECRET`、`env` 含 `ORIGIN_SECRET`（寫法照 Vercel 文件「Restricting your origin to Vercel traffic」與 vercel.json `routes` 的 `env` 說明）。新增 `scripts/check_vercel_config.py` 與它的 `scripts/test_check_vercel_config.py`，CI policy job 跑它：route 存在、route `env` 含 `BACKEND_ORIGIN`、`dest` 以 `/api/$1` 結尾、transform 的 `args` 剛好等於 `$ORIGIN_SECRET` 並帶 `env`、檔案裡沒有其他看起來像密鑰的字串。
  - 驗收：Go 版 `ratelimit` 測試的同名案例，加上：缺密鑰回 403（`/problems/forbidden`）；密鑰相符時限流 key 是 `X-Real-IP`；密鑰不符時自帶的 `X-Real-IP` 被忽略；舊密鑰在輪換期間仍可用；非 ASCII header 回 403；`MODE=prod` 缺密鑰啟動失敗；設定有空項目啟動失敗；`MODE=prod` 下沒帶 header 回 403、`MODE=dev` 下沒帶 header 放行且限流 key 是連線位址、`MODE=dev` 下帶錯 header 回 403；非 loopback 來源打 `/health` 回 404；mutation：把放行判斷改成不看 `MODE`，`MODE=prod` 的缺 header 測試要失敗；mutation：拿掉 `/health` 的 loopback 判斷，非 loopback 的測試要失敗；mutation：拿掉密鑰比對，缺密鑰的測試要失敗；mutation：密鑰不符時仍信任 `X-Real-IP`，偽造 IP 的測試要失敗；mutation：刪掉 `vercel.json` 的 transform，或拿掉 route 的 `env`，policy 檢查要失敗。
  - 執行中的決定與偏離（2026-11-03）：
    - 組裝順序（外到內）：`RequestIdMiddleware` → `ClientMiddleware`（解出 client 位址與來源密鑰是否相符）→ `AccessLogMiddleware` → `SecurityHeadersMiddleware` → `RecoverMiddleware` → `OriginGuardMiddleware`（403／health 的 404）→ `ContractValidation`。拒絕的請求也有 request id、安全 header 與 access log。
    - 前端伺服器端呼叫帶密鑰：全域約束允許但原本沒有排進任何步驟；2c 的 guard 上線後正式環境的 SSR 呼叫會被 403，所以併入 2c。做在 `client.ts` 的共用 middleware（只在伺服器端、有設 `ORIGIN_SECRET` 時加 header），`server.ts` 透過它自動帶上，不必改。
    - 非 ASCII 的密鑰：bytes 的 `hmac.compare_digest` 本來就判為不符，明確的 `isascii` 檢查在 mutation 下沒有測試失敗，判定多餘並刪除；行為（403）有測試。
    - `ORIGIN_SECRET` 比計畫嚴：最多兩項、每項是不含空白的可列印 ASCII、至少 32 字元（`init_env` 產生 43 字元；Vercel 沒設變數時會原樣送出 `$ORIGIN_SECRET`，14 字元，因此永遠不會相符）。
    - log 格式照 Go 版 `logx`：dev 是文字、prod 是 JSON（計畫原寫「JSON 格式」；`.env-example` 的 MODE 說明本來就這樣寫）。文字格式的值與訊息會跳脫換行，JSON 的核心欄位不能被 `extra=` 蓋掉。uvicorn 的 access log 關閉、`log_config=None`，所有 logger 經同一個 handler 寫到 stderr。
    - 被切斷的請求：request id 只在 app 正常返回時重設，讓 uvicorn 在同一個 task 記的錯誤那行也帶 `request_id`。
    - 限流：`IPLimiter` 照 Go 版的 token bucket（每分鐘 N 次、IPv6 以 /64、閒置的 bucket 每個 refill 掃一次），以 `dependencies=[RATE_LIMITED]` 掛在 operation 上（在契約驗證之後，被拒的請求不扣 token）。目前沒有 operation 用它：步驟 3 的 `CreateUser`、`CreateSession` 掛上。`create_app` 收 `IPLimiter`，測試用停住的時鐘。
    - 伺服器端呼叫不帶 `X-Real-IP`，backend 以連線位址（正式環境是 cloudflared）為限流 key，所以會限流的 operation 不從伺服器端呼叫（寫在 `client.ts` 與威脅建模）。
    - `vercel.json` 的 `set` 只在 header 缺少時設定（Vercel 文件）：client 自帶 `X-Origin-Secret` 時那個值傳到 backend、不相符而回 403，不會被放行；檢查腳本接受 `$ORIGIN_SECRET` 與 `${ORIGIN_SECRET}`（文件寫兩者等價）。
    - `APP_ORIGIN` 只加進 compose 與 `.env-example`，設定讀取在步驟 3（CSRF）。`backend/.env-example` 不存在，根目錄的 `.env-example` 在 2b 前就沒有 `TRUSTED_PROXIES`、`SITE_ADDRESS`。
    - 依 AGENTS.md「在同一個改動裡更新」，`docs/security/threat-model-auth.md` 的信任邊界、偽造位址、request id、access log 列，與 `docs/privacy/data-inventory.md` 的 client IP、user agent、日誌段先改成 D6 的樣子；兩份文件其餘的 Go 段落仍由步驟 7 改寫。
    - 審查（`/code-review high`、diff 視角各一輪）後的修正見 commit `f992a96`；不改：密鑰相符卻缺 `X-Real-IP` 時靜默退回連線位址（Vercel 轉送一定會帶；改列步驟 7 的檢查清單）、`_limit_key` 也處理 IPv4-mapped 位址（`IPLimiter` 對外接受任意位址）。
- [ ] **3. auth**（被擋於：無）：一個 session 做不完，2026-11-03 拆成 3a（D8 的產生工具）與 3b（auth 的操作與防護），依序做，各自有驗收；3b 開工讀完 Go 版的 auth 後仍做不完，再拆成 3b（帳號與登入）與 3c（session 資源、刪帳號、seed-admin），理由與每個 Go 機制的去向見附件 [`auth-carryover.md`](2026-11-03-fastapi-template/auth-carryover.md)。
  - 依賴套件（2026-11-03 Will 核准）：`datamodel-code-generator==0.86.0`（dev；2026-10-18 發佈，0.87、0.88 未滿 `exclude-newer` 的 7 天），連帶新增 12 個間接套件（argcomplete、black、genson、inflect、isort、jaraco-functools、jinja2、mypy-extensions、pathspec、platformdirs、pytokens、typeguard，最早的 2008 年、最晚的 2024 年首次發佈），osv-scanner 無發現。`argon2-cffi==25.1.0`（2026-11-03 Will 核准；2025-06-03 發佈），連帶新增 3 個間接套件：`argon2-cffi-bindings==26.1.0`（2026-08-20，首版 2021）、`cffi==2.1.1`（2026-08-03，首版 2012）、`pycparser==3.12`（2026-10-22，首版 2011），osv-scanner 無發現。
- [x] **3a. D8 的產生工具**（被擋於：無）
  - 範圍（2026-11-03 加入）：`make -C backend gen` 從契約產生 Pydantic 模型到 `backend/src/app/web/contract_models.py`（或依模組拆檔，開工時定），`gen-check` 重新產生後 `git diff --exit-code`，接進 `make check`、CI backend job 與 pre-push；產生檔在 `.gitattributes` 標 `linguist-generated`；`docs/rules/api-design.md` 第 7、102、204 行的 `make -C backend gen` 與 `gen-check` 改指向這組指令。
  - 驗收：`make -C backend check` 含 `gen-check`；mutation：改契約一個欄位不重新產生，`gen-check` 要失敗。
  - 執行中的決定與偏離（2026-11-03）：
    - 單一檔 `app/web/contract_models.py`，不依模組拆：產生器一次讀整份契約、輸出一個模組，schema 之間互相引用（`Session.user` 是 `User`，`ValidationProblem` 繼承 `Problem`），依模組拆要自己過濾 schema。放在 `app.web`：modules 可以 import 它，它只依賴 pydantic。
    - `gen-check` 照 Go 版 `gen-openapi-check`，產生到暫存目錄再 `cmp`，不用 `git diff --exit-code`：不改工作樹，產生檔不存在、產生器或 ruff 任一步失敗都會失敗（失敗時 `gen` 也不會寫入）。代價：重新產生但還沒 commit 時，本機 pre-push 會放行，由 CI 擋下（其他 pre-push 檢查也都看工作樹）。
    - 產生選項放 `backend/pyproject.toml` 的 `[tool.datamodel-codegen]`；產生器自己的 formatter 關掉，輸出經 ruff 的 fix 與 format（從 stdin 讀、帶目標檔名，`ruff.toml` 照常套用）。
    - 非必填的欄位預設為 `pydantic.MISSING`（Pydantic 2.14 起是穩定 API；審查後從 `T | None = None` 改來）：契約不允許 null 的欄位送 null 會被拒；PATCH 分得出「沒送」（`MISSING`）與「送 null」（`None`），步驟 4、5 的 PATCH 用 `is MISSING` 判斷，對應 `api-design.md` 的 merge-patch 規則；dump 時省略沒設定的欄位。`gen-check` 看不出選項被改（它用同一組選項重新產生），所以 `tests/test_contract_models.py` 釘住這三條規則；拿掉 `use-missing-sentinel` 後 4 個測試全部失敗。
    - ruff：`pydantic.BaseModel` 加進 `runtime-evaluated-base-classes`（否則 TC003 要把 `UUID` 移進 `TYPE_CHECKING`，pydantic 會壞）；產生檔免 E501（docstring 是契約的描述，契約與 Go 版 `560106d` 逐字相同，不為行長改契約）與 RUF036（只管 union 裡 `None` 的位置）。vulture 排除產生檔，理由同 `models.py`。
    - 接受的取捨（審查提出）：vulture 整檔排除後，契約裡沒有 router 用到的 schema 不會被報出來；`Problem`、`ValidationProblem`、`FieldError` 由 `app.web.problems` 手寫產生，類別層級的檢查會需要長期的白名單，而每個 operation 都有 router 由 3b 的契約與路由一致檢查負責。模組在另一個檔裡繼承產生的模型時，ruff 認不出它是 pydantic 模型，會報 TC003（實測；修正是 unsafe fix，`make fmt` 不會自動套用，所以只會變成 lint 失敗，不會悄悄改壞）。觸發條件：第一個繼承契約模型的類別出現時，把它的基底類別加進 `runtime-evaluated-base-classes`，或改用組合。
    - 新依賴帶進兩個 pytest plugin（typeguard、platformdirs），沒設定時都不作用（讀過程式碼：typeguard 要 `--typeguard-packages`，platformdirs 只提供一個要主動使用的 fixture）。
    - `docs/rules/api-design.md` 第 102、204 行原本就寫 `make -C backend gen`／`gen-check`，只改第 7 行的產生檔路徑；`AGENTS.md` 的 `make check` 清單、Quick Reference、Conventions 與 `ci.yml` 的註解一起更新。
- [x] **3b. 帳號與登入**（被擋於：3a）
  - 範圍：附件 `auth-carryover.md` 標 3b 的列，含它的「資料存取與組裝」與「文件」兩節。7 個操作：`createUser`、`createSession`、`getCurrentUser`、`deleteCurrentSession`、`getUser`、`getSession`（後兩個是前兩個的 201 `Location`，ADR api-and-auth-baseline D9）、`getCurrentSession`（2026-11-04 從 3c 移入，見下面的偏離）。設定（`SESSION_ABSOLUTE`、`SESSION_IDLE`、`COOKIE_SECURE`、`APP_ORIGIN`）；密碼（NFKC、常見密碼清單、argon2id 與並行上限）；session（token、認證與 touch、登入的 per-client 節流、登出）；認證 middleware 與 principal；CSRF 先看 `Sec-Fetch-Site`，後備的 Origin 比對改成比對 `APP_ORIGIN`，因為 backend 收到的 `Host` 不是瀏覽器看到的網域（D6）；route 的存取標記；`createUser`、`createSession` 掛限流；安全事件 log；CI stack job 的 curl 流程。
    - 從 2b 審查移入（2026-11-03）：(a) 契約 security 的檢查改看認證 middleware 放進 context 的 principal，不看 cookie 存不存在（openapi-core `Config.security_provider_factory`），同時涵蓋 `COOKIE_SECURE=false` 時 cookie 叫 `session` 而不是 `__Host-session`（2b 的存在性檢查只認後者，dev 下每個要登入的 operation 都會 401）；(b) 啟動時檢查契約的 `(method, path)` 集合等於 FastAPI `/api` 路由的集合加上「尚未實作」集合、路徑參數名稱相同（Go 版由 codegen 在編譯期保證，這裡沒有），和 D7 的存取標記檢查放同一處；「尚未實作」集合由 3c、4、5 各自移除自己的操作，步驟 5 完成時刪掉。(c) 契約路徑的決定已不需要：seed-admin 改用產生的模型驗證（附件 #49）。
    - router 的 request 與 response 型別用 3a 產生的 `app.web.contract_models`，不在模組的 `schemas.py` 重寫契約已有的形狀（D8）。
  - 消費端：`rg -n 'sessions|users' frontend/src -g '!**/schema.d.ts'`（`features/auth/api/auth-api.ts`、`features/auth/server.ts`、`lib/api/client.ts` 與它們的測試）；`rg -n '_limit_key|security_provider|SESSION_COOKIE' backend/`。
  - 不能動：cookie 名稱與屬性、session 期限、錯誤 code、契約。
  - 驗收：每個操作有成功與至少一個失敗案例的測試，案例對照附件「測試對照」標 3b 的 Go 測試；無效 cookie 打有 security 的 operation、body 也不合法時回 401 而不是 422，mutation：改回只看 cookie 存在，這個測試要失敗；`COOKIE_SECURE=false` 時帶 `session` cookie 打 `GET /api/users/me` 回 200；契約與路由不一致（少一個路由、多一個路由、參數名不同）或 route 缺存取標記時啟動檢查失敗；`"abc\n"` 帳號回 422 `pattern`；CI stack job 在 compose network 上用一個 curl 容器直接連 `backend:8000`，每個請求帶 `.secrets/origin_secret` 的值，跑一次「註冊 → 登入 → `GET /api/users/me` 回 200 → 登出 → 再讀回 401」；Go 版「client 自帶的 request id 會被覆寫」檢查改用同一個 curl 容器對 backend 驗；mutation：拿掉 cookie 的 `HttpOnly`，測試要失敗；mutation：節流的 client key 不含位址（只依帳號），`TestSignInThrottlePerClient` 的對應案例要失敗；mutation：雜湊不經執行緒池，並行上限測試要失敗；`backend/src/app/modules/auth/router.py`、`service.py` 不再只有 docstring。前端伺服器端帶密鑰的測試與 CI frontend job 的 bundle 哨兵原本列在這裡，已在 2c 完成（`315d07c`、`8ac82fa`，2026-11-03 核對）。
  - 開工時的 design review（2026-11-03）：findings 14（blocking 1），處理見附件；第 2 輪不派：修正都落在實作，由上面的測試與 mutation 證明，完成前的 design review 審 diff。
  - 執行中的決定與偏離（2026-11-04）：
    - CI 的登入流程不用 curl 容器，改用 backend image 的一次性容器（`docker compose run --no-deps backend`）跑 `scripts/stack_auth_smoke.py`：同樣在 compose network 上從另一個位址連 `backend:8000`，但不必多釘一個 image 的 digest，而且來源密鑰直接從 backend 自己的 compose secret 讀、不經過 CI log。登出後帶著原本的 cookie 再讀一次，證明 session 被刪掉，不只是 cookie 被清掉（diff 視角審查 D4）。
    - `getCurrentSession` 從 3c 移入：`GET /sessions/current` 是契約的操作，`/sessions/{id}` 先上線時 Starlette 會把它交給 `get_session`、回 422（`/code-review` 指出）。router 依宣告順序比對，字面路徑寫在同一層的樣板路徑之前（`router.py` 開頭的註解）；`test_the_current_session_is_not_taken_for_a_session_id` 守住。
    - 查詢用 SQLAlchemy Core 運算式（附件 #28 的「實作時定」）；auth 的 service 改成 `service/` 套件放密碼的程式與清單檔（`AGENTS.md` 分層表同步）。
    - ruff：`runtime-evaluated-decorators` 列 `APIRouter` 的 method decorator（FastAPI 在宣告 route 時讀參數的 `Annotated`，否則 TC001 會把它們移進 `TYPE_CHECKING`）；router 檔放行 A002，讓參數名稱與契約的 `{id}` 相同（`Path(alias="id")` 執行時可用，但 ruff 的 FAST003 看不懂 alias）。
    - import-linter 的 `sql-below-the-router` 只看直接 import，並把 `app.modules.*.repository`、`app.modules.*.models`、`app.platform.db` 也列為禁止：只禁 sqlalchemy 時，router 可以經 repository 或 engine 跑 SQL 而不必 import sqlalchemy（完成前 design review 實測）。
    - dummy hash 在 app 啟動時（lifespan）算好，不是第一次用到才算：開機後第一個不存在的帳號不會多一次雜湊（附件 #7；兩份審查都指出）。
    - 比計畫多的防護：`MODE=prod` 下 `COOKIE_SECURE=false` 拒絕啟動（`.env-example` 與部署文件都寫「必須是 true」；Go 版沒有檢查）；`APP_ORIGIN` 拒絕結尾冒號與非 ASCII 的網域；契約的 `pattern` 在載入時全部編譯，`\s`、`\S`、`.` 照 ECMA 的意義轉譯，轉不過去的語法在啟動時失敗；`serve` 遇到路由與契約不一致時印 `serve: ...` 並回 1。Go 版 `TestEveryWritablePathHasGet`（契約檢查，盤點表漏列）一併移植。
    - 記錄、不改：`Deps.auth` 一個欄位同時決定掛 auth router、跑路由檢查與裝認證 middleware（觸發條件：步驟 4 加第二個模組時，改成列出所有模組、有模組就一定檢查）；每個帶 cookie 的請求都開交易（SQLAlchemy 的 `connect()` 也會 autobegin；觸發條件：認證延遲成為熱點時，只讀路徑改用 `isolation_level="AUTOCOMMIT"`）。
    - 駁回：註冊時不刪請求帶來的舊 session（`/code-review`）：Go 版 `users.go:28-38` 的 `CreateUser` 也不刪，舊 session 屬於另一個帳號、cookie 已被新的取代，沒有可利用的情境。
    - 交給 Will：登入成功時清掉該帳號所有來源的節流列（Go 版 `ClearLoginThrottles` 的行為，Python 照搬）。`/code-review` 指出：攻擊者在某個位址累積的退避，會在本人從別處登入成功時歸零。已決（D10，2026-11-04），由 3c 實作。
    - 審查（完成前，2026-11-04）：`/code-review high` 10 項、diff 視角 9 項（blocking 3）、design-review 9 項，合計 28 項（重疊的算一次後 23 項）：修 18、記錄 2、駁回 1、交給 Will 1；已修的一項是節流 SQL 的 deprecation（`915f11f`）。第 2 輪不派：每個修正都有先失敗後通過的測試或複本裡的 mutation（`red-proof.py`），文件由 `rg` 核對。
- [x] **3c. session 資源、刪帳號與 seed-admin**（被擋於：3b）
  - 範圍：附件標 3c 的列：`listSessions`、`deleteAllSessions`、`deleteSession`、`deleteCurrentUser`（10 分鐘內登入過、唯一的 admin 不能刪、`user.deleted` 稽核紀錄）、`seed-admin` 與根目錄 `make seed`、每小時清理過期 session 與 1 天未動的節流列（lifespan 裡的背景 task，單一副本的前提成立：D6 單台 VM、單一 uvicorn 程序）；「尚未實作」集合移除這 4 個操作；登入成功只清這次來源的節流列（D10）：新增只刪 `(account_key, client_key)` 的查詢給 `sign_in` 用，清整個帳號留給步驟 5 的 admin 解鎖與 `recover-admin`（3b 的 `clear_login_throttles` 在 3c 沒有呼叫端，vulture 不收，3c 刪掉、步驟 5 照 Go 版加回）；ADR account-abuse-and-lifecycle D4 補寫成功時清除的範圍（走 ADR 修訂流程）；威脅模型第 45、96 列、`sign_in` docstring、附件第 28 列同步；文件補 3c 的部分。
  - 消費端：`rg -n "sessions|users/me|seed" frontend/src Makefile .github/workflows -g '!**/schema.d.ts'`。
  - 不能動：同 3b。
  - 驗收：附件「測試對照」標 3c 的 Go 測試；mutation：拿掉重新驗證的時間檢查，`TestDeleteCurrentUser` 的 403 案例要失敗；mutation：拿掉 `keepAnAdmin`，`TestDeleteCurrentUserLastAdmin` 要失敗；seed-admin 的錯誤訊息不含密碼；D10 的 db 測試：來源 A 失敗後來源 B 登入成功，A 仍要等、成功的來源自己的列被清掉、連續成功不累積等待，mutation：改回清整個帳號、改成完全不清，各自要有測試失敗。
  - 執行中的決定與偏離（2026-11-04）：
    - `clear_login_throttles`（清整個帳號）在 3c 沒有呼叫端，vulture 不收，刪掉；步驟 5 加回（步驟 5 範圍已列）。
    - 清理 task 啟動時先跑一次，之後每小時（Go 版 ticker 第一次要等一整個間隔）：`/code-review` 指出重啟比一小時頻繁時（compose watch、頻繁部署）永遠不會清。
    - 刪帳號只在呼叫者是 admin 時鎖 admin 列；同時刪兩次時第二個回 401（帳號連 session 一起沒了），不回契約沒列的 404；`ensure_admin` 先查帳號是否存在，重跑 seed 對既有帳號一律不動、不檢查密碼（以上 `/code-review`）。`make seed` 的帳號名經環境變數傳給 shell，不貼進 recipe。
    - Limit 的預設 20 放在 `app.platform.pagination.DEFAULT_LIMIT`，測試對照契約（design review：步驟 4、5 會再用）。
    - 記錄不修：清理的 DELETE 沒有分批、`expires_at`／`last_used_at`／`updated_at` 沒有索引。單一副本的範本量級用不到；觸發條件：清理 log 出現 statement timeout，或任一表超過 10 萬列時，改成分批刪除並加索引（新 revision）。
    - 駁回：`delete_own_account` 多讀一次 session 的 `created_at`（可放進 `Authenticated`）。nit；分開讀也涵蓋請求中途被撤銷的 session，Go 版同樣做法。
- [x] **4. items**（被擋於：步驟 3）
  - 範圍：inventory 標步驟 4 的 5 個操作，含 owner 權限與分頁。
  - 消費端：`rg -n 'items' frontend/src`。
  - 驗收：對照 Go 版 `items_test.go`、`authz_test.go`；mutation：拿掉 owner 檢查，跨使用者讀取的測試要失敗；`backend/src/app/modules/items/router.py`、`service.py` 不再只有 docstring。
- [x] **5. 帳號濫用與管理**（被擋於：步驟 3）：一個 session 做不完（Go 版對應約 1,000 行加測試），2026-11-04 拆成 5a、5b、5c，依序做，各自有驗收；下面「範圍」「驗收」是整步的總表，各子步驟分走。
  - 範圍：inventory 標步驟 5 的 5 個 admin 操作、`recover-admin`、trusted devices、登入鎖定、稽核紀錄、HIBP 密碼檢查（ADR account-abuse-and-lifecycle），以及附件 `auth-carryover.md` 標 5 的列：`ADMIN` 存取標記與 `insufficient_role`、`authz.denied`；登出全部、停用與 recover 也刪 trusted devices；清理也刪過期的 devices；「尚未實作」集合刪除；加回清整個帳號節流的 `clear_login_throttles`（3c 刪掉，附件第 28 列）；`recover-admin` 與 `seed-admin` 共用建 `AuthService` 的組裝，不再各自塞用不到的 session 期限（3c design review）。
  - 驗收：對照 Go 版 `backend/internal/httpapi/throttle_test.go`、`users.go` 對應的 `authz_test.go` 案例、`backend/internal/modules/auth/service_test.go` 的 devices 與鎖定案例；mutation：把鎖定門檻加大，鎖定測試要失敗。
- [x] **5a. ADMIN 標記與 admin 操作**（被擋於：無）
  - 範圍：`ADMIN` 存取標記、`insufficient_role`、`authz.denied` 記錄；`listUsers`、`patchUser`（停用、最後一個 admin 不能停用、並行停用留一個）、`deleteUser`；對應的 audit action；`TestOperationAccessEnforced` 的 admin 部分。
  - 消費端：`rg -n 'users|disabled|insufficient_role' frontend/src`、`rg -n 'UNIMPLEMENTED|SIGNED_IN' backend/src`。
  - 驗收：對照 `authz_test.go` 的 `TestAdminDisablesUserRevokesSessions`、`TestLastAdminCannotBeDisabled`、`TestConcurrentAdminDisableLeavesOne`、`TestNonAdminCannotPatchUser`、`TestListUsers`、`TestNonAdminCannotListUsers`、`TestAdminDeletesUser`、`TestAdminChangesAreAudited`；mutation：把 `ADMIN` 換成 `SIGNED_IN`，非 admin 的測試要失敗；拿掉最後一個 admin 的檢查，對應測試要失敗（db 測試的 mutation 推分支給 CI）。
- [x] **5b. trusted devices 與帳號鎖定**（被擋於：5a）
  - 範圍：附件第 27、30（刪 devices）、32（清過期 devices）、34 列；`getSignInLock`、`deleteSignInLock`；加回 `clear_login_throttles`（第 28 列，D10）。
  - 驗收：對照 `throttle_test.go` 其餘與 `service_test.go` 的 devices 與鎖定案例；mutation：把鎖定門檻加大，鎖定測試要失敗。
  - 執行中的決定與偏離（2026-11-04）：
    - 裝置 cookie 名稱由 `Settings.device_cookie_name` 決定（跟 session cookie 同規則），`SessionCookie` 多一個 `device_name` 欄位與 `device()`；`sign_in` 回傳 `SignedIn`（user、session、device）。
    - `/code-review medium` 9 項（blocking 0）：修 1（威脅模型仍寫 `_client_key`）；駁回 5：信任裝置登入成功也把帳號合計歸零（ADR D4 2026-11-04 修訂與 D10 的 B 寫明，C 已被否決）、被拒的嘗試也更新 `updated_at` 使「一天沒有嘗試」延後（ADR 的字面意思，D10 C 已記這個性質）、鎖定狀態只靠每小時清理過期（最多晚 1 小時，與 Go 版相同）、鎖定拒絕前已計入該來源的退避（Go 版同順序）、`SessionCookie` 名稱（nit）；記錄 3（皆與 Go 版相同）：一個瀏覽器只有一個裝置 cookie，兩個帳號共用同一瀏覽器時互相覆蓋信任——觸發條件：有多帳號共用瀏覽器的需求時改成 cookie 存多個 token；不帶 cookie 的客戶端每次成功都新增一列 `trusted_devices`、沒有每人上限——觸發條件：任一使用者的裝置列超過 100 時加上限或去重；一次登入開 4–5 次連線——觸發條件：登入延遲或連線池等待出現在監控時合併成一個交易。
- [x] **5c. recover-admin、HIBP 與收掉「尚未實作」集合**（被擋於：5b）
  - 範圍：`recover-admin` 與 `make recover-admin`（附件第 14、50 列），和 `seed-admin` 共用建 `AuthService` 的組裝；HIBP 與 `PASSWORD_HIBP_CHECK`（第 11、48 列；用標準庫 HTTP，不加依賴；要加依賴就停下問）；刪掉 `UNIMPLEMENTED`；補回 `TestCredentialsNeverLogged`、`TestSecurityEventsLogged` 的 HIBP 與 `authz.denied` 部分。
  - 驗收：對照 `TestRecoverAdmin`、`TestCreateUserBreachedPassword`；mutation：HIBP 失敗改成拒絕，失敗放行的測試要失敗。
  - 執行中的決定與偏離（2026-11-04）：
    - HIBP 用戶端放 `app.platform.hibp`（標準庫 `http.client` 在執行緒裡跑，沒有加依賴）；`AuthService` 收一個 `BreachCheck`，捕捉 `HIBPError` 後放行。錯誤訊息不含請求路徑，所以 Go 版「5 字元前綴寫進日誌」那項接受的風險在這裡不存在。
    - `AuthService` 的兩個 session 期限合成 `SessionLimits`（有預設值），admin 命令不再傳；`seed-admin` 與 `recover-admin` 共用 `app.main.seed.run_admin_command`。
    - 刪 `UNIMPLEMENTED` 之後，`test_routes.py` 改成用「路由全部 operation，再替換或拿掉幾個」的 app 來測。
    - `/code-review medium` 7 項（blocking 0）：修 4——補 `User-Agent`；整個檢查加一個 deadline（`asyncio.timeout`，DNS 也算在內）；`recover_admin` 先確認帳號是 admin 才雜湊（打錯帳號就回報「no admin account」，不會先去查 HIBP）；role 改用 `ROLE_ADMIN` 常數，不寫死字串。記錄 1：每次檢查都開新的 TLS 連線，跑在預設執行緒池——觸發條件：監控看到註冊延遲或預設執行緒池排隊時，改成專用池或重用連線。駁回 2：Makefile 的讀密碼流程和 `seed` 重複（nit，Go 版也一樣）；admin 命令也是失敗就放行（與 Go 版 `auth.go:154`、ADR account-abuse-and-lifecycle 一致）。
- [x] **6. rename 腳本與 smoke test**（被擋於：步驟 1）
  - 2026-11-05 被 D12 取代：原地改名改成「產生到新目錄」，由步驟 6b 接手；本步保留為紀錄（`874239a`）。開工時漏讀專案頁 Pending 裡 2026-11-04 已決的這個做法，計畫也沒寫進去。
- [ ] **6b. 從範本產生衍生專案到新目錄**（被擋於：D12；排在 7b 之前，因為 7b 會動到 `scripts/rename.py`）
  - 範圍：照 template-nextjs 的 D10（含其 2026-11-04 修訂）與它的參考實作 `template/new_project.py`、`template/test_new_project.py`（template-nextjs@aa54892），改寫成本 repo 的佔位名稱與檔案：`make new NAME=x DEST=<目錄> [REPO=owner/name]` 取代 `make rename` 與 `scripts/rename.py`、`scripts/test_rename.py`，同一波刪除，不留 shim。產生器放範本專用位置（例如 `template/`），不進產出。檔案集合以 `git archive` 取追蹤中的檔、`tarfile` 的 `filter="data"` 解壓到 DEST 旁的暫存目錄；排除清單一處定義（產生器與測試、`docs/plans/`、漂移腳本與其測試（D11 後刪掉就不用列）、範本版 README／AGENTS.md 由 `template/derived/` 取代）；共用檔裡只屬於範本的幾行用 `>>> template-only`／`<<< template-only` 標記，產出時整段移除；替換表單趟替換（`template-fastapi-nextjs`、`template_fastapi_nextjs`、`Will413028/template-fastapi-nextjs`），路徑含舊 token 就拒絕；先算後寫；產出重新 `git init`，只有 `main`。名字限制沿用步驟 6（PostgreSQL 保留字、長度、不得含佔位名稱）。CI 的 rename job 換成標記區塊內的 `generate` job：產生專案後在產出跑 `uv lock --check`、`make -C backend check` 不含 db 的部分與 `pnpm test`，`gate (ci)` 的 needs 也用標記列出。
  - 消費端：`rg -n 'make rename|rename\.py|test_rename|Rename smoke' -g '!docs/plans/**' .`
  - 驗收：產出裡 `rg` 找不到三個佔位 token；產出不含產生器、`docs/plans/`、template-only 標記區塊；CI `generate` job 綠；mutation：拿掉一個替換 token、拿掉一個排除項、拿掉 template-only 區塊移除，各自讓產生器測試失敗。
  - 範圍：恢復步驟 1 拿掉的根目錄 `make rename` target 與 CI rename job（步驟 1 時 rename 腳本還是 Go 版，先移除）；`scripts/rename.py` 改成 Python 版（套件名、DB 與角色名、image、品牌字，沒有 module path）；CI rename job。（2a 起 `backend/tests/dbtest.py` 寫著角色與資料庫的佔位名稱，要納入替換或改成從設定讀取。）
  - 驗收：CI 跑 `make rename NAME=ci-probe` 後 `rg` 找不到佔位名稱、pytest 通過；mutation：刪掉一個替換規則，smoke job 要失敗。
- [ ] **7. 與 Go 版的漂移檢查與文件**（被擋於：D4、步驟 3）
  - 2026-11-05 拆成 7a、7b（一個 session 做不完；依序做，各自有驗收）：
    - [x] **7a 漂移腳本與 ADR**：`scripts/check_go_drift.py` 與測試（D4）；補回步驟 1 漏搬的 `frontend/AGENTS.md`（同源段改寫成 Vercel rewrite）；ADR `docs/adr/2026-11-05-fastapi-backend-stack.md`（D1–D5、D7、D8，加上 Go 版 ADR go-backend 仍成立的型別與 DB 角色規則）與 `docs/adr/2026-11-05-vercel-rewrite-and-cloudflare-tunnel.md`（D6）；刪除 `go-backend` ADR 並改指向；原 ADR `api-and-auth-baseline` 標 2026-11-05 修訂。dependabot 的 cloudflared 與 digest 在步驟 1 已完成。驗收：腳本對故意改過的共用檔回報差異、對未改的回報乾淨（`scripts/test_check_go_drift.py` 與 mutation）；`--ref 560106d` 對本 repo 回報乾淨；D6 的 ADR 列出取代的段落，原 ADR 那幾段都有修訂標記。
    - [ ] **7b 部署指南與其餘文件**（被擋於：無；D11 已決）：先做 D11 的後果：ADR `2026-11-05-fastapi-backend-stack.md` 的標題、Context、D8 與 Revocation Triggers 改成「以 `560106d` 為分家點」並記下 D11；刪除 `scripts/check_go_drift.py`、`scripts/test_check_go_drift.py` 與 `rename.py` 的 DROPPED（剩下真正共用的檔只有 LICENSE、SECURITY.md、security.yml 這類，不值得一支腳本）；部署拓樸 ADR 的 Rationale 與 Expected Outcome 不再拿「能用比對腳本追蹤 Go 版」當理由；`README.md` 第 3 行「same HTTP contract and the same Next.js frontend」改成以 `560106d` 為分家點（全域約束本來就以 `560106d` 為規格，不用改）。驗收另加：`rg -n 'check_go_drift' -g '!docs/plans/**' .` 沒有結果。然後：下面範圍裡剩下的文件改寫（`docs/deploy-vps.md`、威脅模型、個資盤點、`docs/frontend-known-issues.md`、`docs/rules/api-design.md` 的 Go 專屬段、`frontend/README.md`）與下面驗收裡的兩個 `rg` 掃描與第一次部署檢查清單（D6 的 ADR 檔名：`2026-11-05-vercel-rewrite-and-cloudflare-tunnel.md`）。
  - 範圍：依 D4 做共用檔清單與比對腳本；`docs/` 的 Go 專屬段改寫；刪除 `go-backend` ADR，D1–D3、D6 寫成本 repo 的 ADR；D6 的 ADR 取代 Go 版 ADR `api-and-auth-baseline` 的 D1（拓樸）、D6（IP 來源）、D8（proxy 部分），以及它的約束段、Expected Outcome、Revocation Triggers 中提到 Caddy 的部分，照 ADR 修訂流程在原 ADR 標註；D6 的 ADR 寫明 dev 與正式不再是同一套 proxy 這個代價；`docs/deploy-vps.md` 改寫成「Vercel + VM + Cloudflare Tunnel」部署指南（含 Vercel Function 區域、前端 DNS 不開代理、密鑰輪換步驟）；`docs/security/threat-model-auth.md` 的信任邊界改寫（瀏覽器 → Vercel → Cloudflare → backend）；`docs/privacy/data-inventory.md` 把 Caddy log 換成 Vercel 與 Cloudflare（兩者都終止 TLS，是新的資料處理者）；`docs/frontend-known-issues.md` 的同源說明；`frontend/AGENTS.md`、`frontend/README.md`；`.github/dependabot.yml` 的 caddy 註解換成 cloudflared，cloudflared image 釘 digest。
  - 驗收：腳本對一個故意改過的共用檔回報差異，對未改的回報乾淨；`rg -n -i 'golang|gofmt|sqlc|go test|golangci' docs/ AGENTS.md README.md` 沒有結果，或每筆都附保留理由；`rg -n -i -g '!docs/plans/**' -g '!docs/adr/2026-10-18-api-and-auth-baseline.md' -g '!docs/adr/<D6 ADR 檔名>' 'caddy|TRUSTED_PROXIES|SITE_ADDRESS|lb_try_duration|172\.30' .` 沒有結果（原 ADR 只標修訂、不改原文；D6 的 ADR 必須寫出它取代了什麼；兩者都排除）；D6 的 ADR 檔案存在並列出它取代原 ADR 的哪幾段，`rg -n '修訂' docs/adr/2026-10-18-api-and-auth-baseline.md` 在那幾段都找得到標記；部署文件有「第一次部署檢查清單」，逐項對應前提段「未驗證」那條（cookie 穿過 rewrite、`X-Real-IP`、`Host`、transform 覆寫、`no-store` 不被快取、SSR 帶密鑰、access log 的 `client_ip` 是使用者位址而不是 cloudflared 的、HSTS、改環境變數後是否要 redeploy、tunnel 只轉送 `/api/*`、從另一個網域對 `/api/sessions` 發 POST 經 Vercel 收到 403 `cross_origin_request`），每項附確認用的指令；`rg -c` 檢查清單的項數等於那條列的項數。
- [ ] **8. 收尾**：完成定義逐項核對；design-review skill 獨立審查；quality-baseline skill 重跑一次確認等級；漂移檢查接進 CI 或週回顧；延後項搬回專案頁 Pending。驗收：`rg -n '^\| ' inventory.md | rg -v '完成 \|$|^[^|]*\| (方法|子命令|路徑|檔案)|---'` 沒有結果（每一列狀態都是完成）；CI 全綠的 run 連結。

## 決定

- **D1 契約來源**（狀態：已決 2026-11-03，Will；擋住步驟：1；需要的事實：步驟 0 (a)(b)）
  - A：手寫 `backend/api/openapi.yaml`（從 Go 版照搬）是唯一來源，FastAPI 用 middleware 或契約測試驗證符合它——兩個 template 的契約可以直接 diff，前端保證不用改；代價是 FastAPI 的 code-first 優勢用不到，型別要寫兩次（建議）。
  - 結論：A（照搬 Go 版 `openapi.yaml`，用 openapi-core 驗證；依據 `spike.md` (a)(b)）；ADR：步驟 7 併入本 repo ADR
  - 選 B 時：FastAPI 產物要輸出到 `backend/api/openapi.yaml` 並 commit，CI 加「export 後 diff」的過期檢查，前端才能不改。
  - B：FastAPI code-first，從 Pydantic 模型產生 OpenAPI，前端從產物生成型別——Python 社群的標準做法、寫一次就好；代價是產出的 schema 細節（nullable、enum、命名）很可能和 Go 版不同，前端可能要改。
- **D2 migration 工具**（狀態：已決 2026-11-03，Will；擋住步驟：1、2；需要的事實：步驟 0 (c)）
  - A：沿用 Atlas 與 Go 版的 `schema.sql` 和 6 個 migration——schema 和 CI 的 migration 檢查（squawk、不可修改既有 migration）照搬，兩版 DB 完全一樣；Dockerfile 內附 Atlas、CI stack job 的 migrate 步驟照搬；代價是 Python 開發者不熟，SQLAlchemy 模型要另外和 schema 對齊（建議）。
  - B：Alembic——Python 標準做法，可從 SQLAlchemy 模型自動產生；代價是 6 個 migration 要重寫，CI 的 migration 檢查要重做，Dockerfile 與 stack job 的 migrate 步驟也要改；「DB schema 與 Go 版相同」不變量不再成立。
  - 結論：B（Alembic）。spike (c) 顯示 A 可行，Will 仍選 B；後果已改進步驟 1、2 與不變量表；ADR：步驟 7 併入本 repo ADR
- **D3 資料存取**（狀態：已決 2026-11-03，Will；擋住步驟：2）
  - A：SQLAlchemy 2.0 async（ORM 或 Core）——Python 標準做法，生態最完整（建議）。
  - B：psycopg 3 加手寫 SQL——最接近 Go 版的 sqlc 寫法，SQL 可以對照；代價是要自己處理對應與交易。
  - 結論：A（SQLAlchemy 2.0 async）；ADR：步驟 7 併入本 repo ADR
- **D5 要不要加前端 E2E**（狀態：已決 2026-11-03，Will；擋住步驟：無，可延後）
  - A：不加，跟 Go 版一樣，靠 stack job 的 curl 流程（建議）。
  - B：加 Playwright E2E job——能驗整條 UI 流程；代價是 CI 時間與維護成本，而且 Go 版沒有，兩版不對稱。
  - 結論：A（不加 E2E，用 stack job 的 curl 流程）；ADR：步驟 7 併入本 repo ADR
- **D4 共用檔怎麼追蹤**（狀態：已決 2026-11-03，Will；擋住步驟：7）
  - A：共用檔清單加比對腳本，跟 Go 版某個 commit 比，週回顧時跑——簡單，不擋 CI（建議）。
  - B：CI 每次比對 Go 版 `dev` 的最新 commit——最即時；代價是 Go 版一改，這邊 CI 就紅。
  - 結論：A（比對腳本，週回顧時跑）；ADR：步驟 7 併入本 repo ADR

- **D6 部署拓樸**（狀態：已決 2026-11-03，Will；擋住步驟：無；結論在本段最後一行）
  - 已決：拿掉 Caddy（compose 不再有 caddy 服務，`deploy/Caddyfile` 不照搬）；前端要放 Vercel。
  - Caddy 原本負責、要有新的負責者的事（來源：Go 版 `deploy/Caddyfile`、ADR api-and-auth-baseline D1、D8）：`/api/*` 同源路由；backend 對外的 HTTPS 與 HSTS；backend 不直接對外；固定的 proxy 位址給 `TRUSTED_PROXIES`，per-IP 限流靠它取真實 IP；覆寫 `X-Request-ID`；backend 重建時暫留請求（`lb_try_duration`）。
  - 待決：Vercel 和 backend 怎麼接。查核來源：Vercel 文件 [Rewrites](https://vercel.com/docs/routing/rewrites)、[Request headers](https://vercel.com/docs/headers/request-headers)、[Static IPs](https://vercel.com/docs/networking/static-ips)、[Limits](https://vercel.com/docs/limits)，2026-11-03 由 subagent 讀取。
    - A：Vercel rewrite 把 `/api/*` 轉給公開的 backend（例如 `api.example.com`），瀏覽器仍同源，`__Host-` cookie、CSP、CSRF 都不變。要做的事：新增 `vercel.json`，一條 `routes` 同時做 external rewrite 與 transform，目的地與共享密鑰都從 Vercel 環境變數取，密鑰不進 git（`set` 會覆寫 client 帶的值；Vercel 文件「Restricting your origin to Vercel traffic」的做法），`next.config.ts` 不用改；backend 只接受帶正確密鑰的請求，並從 `X-Real-IP` 取使用者 IP（Vercel 會覆寫 `X-Forwarded-For`，client 偽造的值傳不到 backend）；SSR 直接連 backend 的公開網址（`API_URL=https://api.example.com`），由伺服器端程式碼自己帶密鑰，Vercel Function 的區域設在靠近 VM 的地方（2026-11-03 Will 改定，原本讓 SSR 繞經 Vercel 自己的網域取得密鑰，那樣多一段路程，而且依賴未驗證的行為）；SSR 請求不帶 `X-Real-IP`，backend 用連線來源位址，限流只作用在瀏覽器發出的註冊、登入，不受影響；API 回應帶 `Cache-Control: no-store`（2026-04-06 之後建立的專案，external rewrite 預設會照上游的 `Cache-Control` 快取）。限制：每個請求最多 120 秒。Vercel 的出口 IP 不固定，付費 Static IPs 也只涵蓋 Functions，所以不能用 IP 白名單。未證實：文件沒寫 `Cookie`／`Set-Cookie` 會穿過 external rewrite，template 不實測，列入部署文件的第一次部署檢查清單。
    - B：前後端分兩個子網域，cookie 改帶 `Domain`、不再用 `__Host-`，加 CORS（帶憑證），CSP 列 API 網域，推翻 Go 版 ADR D1。前端至少改 4 個程式檔（`lib/api/client.ts`、`lib/env.ts`、`proxy.ts`、`next.config.ts`），加上 `.env.example`、`README.md`、`AGENTS.md`。
    - D：FastAPI 也部署在 Vercel（rewrite 可以指向 Vercel 上的另一個 service），Postgres 放外部。backend 不用自己處理 HTTPS，但要確認 FastAPI 在 Vercel 上的執行限制（冷啟動、連線池）。
  - 不論 A、B，backend 都要對外公開，原本由 Caddy 負責的 HTTPS 要換人負責：託管平台內建（Fly、Railway、Render 等）、Cloudflare Tunnel，或 uvicorn 直接掛憑證（要自己續約）。
  - 影響全域約束：A 不改前後端契約，前端改動限於全域約束第 2 條列出的例外（部署設定、伺服器端帶密鑰、Caddy 註解）；B 會改更多前端程式。全域約束已照 A 改寫。
  - backend 的 HTTPS 由誰負責：Cloudflare Tunnel（建議，VM 不開 port、不用自己續約，代價是網域 DNS 交給 Cloudflare）、uvicorn 加 certbot（port 和續約自己管）、只放回 Caddy 管 HTTPS。
  - Caddy 原本的工作交給誰：`/api` 路由 → Vercel rewrite；前端 HTTPS 與 HSTS → Vercel（推論，列入第一次部署檢查清單）；backend HTTPS 與不直接對外 → Cloudflare Tunnel；`TRUSTED_PROXIES` 固定 IP → 來源密鑰加 `X-Real-IP`；覆寫 `X-Request-ID` → backend 自己產生；`lb_try_duration` → 沒有替代，backend 重建的空檔請求會失敗，與 Go 版 ADR D8 已接受的代價相同（觸發條件：要求零停機時，在 VM 上加 blue-green 或放回一層 proxy）。
  - 接受的代價：dev 走 `next dev` rewrite、正式走 Vercel，不再是同一套 proxy；來源密鑰與 `X-Real-IP` 的正式路徑在 CI 沒有自動化覆蓋，靠 backend 單元測試、`vercel.json` 的 policy 檢查，以及衍生專案第一次部署時的檢查清單；template 本身不實際部署（2026-11-03 Will）。Vercel preview 部署不設 `ORIGIN_SECRET`，所以 preview 呼叫不了 API（延後，觸發條件：需要在 preview 上操作 API 時，加一個 staging backend）。
  - 細項（2026-11-03 Will）：`MODE=dev` 沒帶密鑰放行、帶錯一律 403；tunnel 在 Cloudflare 後台管理，backend 自己讓 health 端點只接受 loopback，不依賴 tunnel 的路由規則。
  - 審查收斂規則（2026-11-03 Will）：D6 的計畫審查再跑最後一輪，只修會讓執行做錯或無法驗收的問題；其他設計延伸寫進「範圍」的延後並附觸發條件，然後開始執行。
  - 結論：A（Vercel 同源 rewrite）加 Cloudflare Tunnel，2026-11-03 Will；依據：前端免維運、backend 放 VM。D 不適用（backend 放 VM）。衍生專案第一次部署時若 cookie 不能穿過 rewrite，改選 B。ADR：步驟 7 併入本 repo ADR。

- **D7 後端分層**（狀態：已決 2026-11-03，Will；擋住步驟：1 起的所有後端程式）
  - A：按功能分模組的模組化單體——`app.main`（app factory、middleware 組裝、CLI、Alembic `env.py`、啟動時檢查每個路由都標了存取權限）> `app.modules.{auth,items}`（模組內 `router`（含 `schemas`、`dependencies`）> `service` >（可選）`repository` > `models`；模組互不 import）> `app.platform`（`platform.web` 可用 starlette，其餘不可）。來源：[zhanymkanov/fastapi-best-practices](https://github.com/zhanymkanov/fastapi-best-practices)、Netflix Dispatch（2025-09 封存）、Kraken 的 import-linter 用法（建議）。
  - B：照 Go 版集中 HTTP（`app.http` 放全部 router）——Go 這樣分是因為 oapi-codegen 只產生一個 `ServerInterface`，Python 在 D1 下沒有這個限制；代價是改一個功能要動兩個地方。
  - C：六角形架構（Cosmic Python）——業務規則複雜時最穩，對這個以 CRUD 為主的規模是多出來的樣板。
  - 結論：A，實作時把 `platform.web` 拆成獨立的 `app.web` 層（`main > modules > web > platform`），這樣 `forbidden` 可以整包禁止 `app.platform` 用 web 框架，不必逐檔列出（2026-11-03，步驟 1 實測後調整）。機械檢查用 import-linter 2.15：`layers`（頂層與 `app.modules.*` 都用 `containers` 加 `exhaustive = true`，不屬於任何一層的檔案會失敗）、`independence`、`acyclic_siblings`、`forbidden`（`service`、`repository`、`models` 與整個 `app.platform` 不得 import fastapi、starlette；router、schemas、dependencies 不得 import sqlalchemy；檢查間接 import），每條合約設 `broken_contract_guidance`，`ignore_imports` 為 0 並計數。跨模組的需求經 `platform`（`Principal` 由 `main` 裝上的 auth middleware 設定），外鍵用字串 `"users.id"`、不用 ORM relationship。交易邊界由 service 負責，不強制 repository 層。升級觸發條件：某模組要同步呼叫另一模組時，加 `<m>/api.py` 與 `protected` 合約；某模組的業務規則超出 CRUD 時，只對它改成 ports and adapters。ADR：步驟 7 併入本 repo ADR。

- **D8 Pydantic 模型的來源**（狀態：已決 2026-11-03，Will；擋住步驟：3 的第一個 router）
  - A：從 `backend/api/openapi.yaml` 產生，`make -C backend gen` 寫出、`gen-check` 在 CI 與 `make check` 比對過期——等同 Go 版 oapi-codegen 加 `gen-check`，契約與程式不會分岔（建議）。
  - B：手寫——不多一個工具；代價是契約改了模型可能沒跟上，只能靠 response 驗證的測試發現。
  - 結論：A，2026-11-03 Will。產生工具是新依賴，套件與版本另交核准（見步驟 3 的「被擋於」）；ADR：步驟 7 併入本 repo ADR
- **D9 `jsonschema` 列為直接依賴**（狀態：已決 2026-11-03，Will；擋住步驟：無）
  - 結論：列入 `backend/pyproject.toml` 的 `dependencies`，`jsonschema==4.26.0`（`app.web.validation` 直接用它的 `ValidationError`；lock 裡已是這個版本，不多裝套件）。

- **D12 衍生專案怎麼產生**（狀態：已決 2026-11-05，Will；擋住步驟：6b）
  - 背景：專案頁 Pending 記著 Will 2026-11-04 的決定——各 template 改用 template-nextjs D10 的「產生到新目錄」（A′）；原地改名有四個根因：依序替換互相干擾、範本專用機制留在衍生專案、先改後查、`FILES` 白名單。步驟 6（`874239a`）開工時漏讀，做成原地改名；審查又加了改名後跳過的 CI job 與測試、rename 時刪漂移腳本，正是第二個根因。
  - 選項與取捨照 template-nextjs D10（A′、Copier、GitHub template repo 加清理 workflow）；結論：A′，重做排在 7b 之前（Will 2026-11-05）。推翻條件同 template-nextjs D10：要讓衍生專案之後收範本修正時改走 Copier。ADR：7b 併入本 repo ADR（範本專用、不進產出的位置）。
- **D11 Go 版改用 Protobuf＋Connect 後，還要不要和它共用契約與前端**（狀態：已決 2026-11-05，Will；擋住步驟：7b）
  - 事實：Go 版 `dev`（f91118e）把 REST 與 `openapi.yaml` 刪掉，改成根目錄 `api/` 的 `.proto` 加 Connect，前端 `src/lib/api/` 重寫（Go 版 ADR `2026-11-03-api-contract-protobuf-connect.md`）。那份 ADR 寫明選 Connect 的決定性理由是跟 hawkr 一致，讓 Go 專案只用一套工具，技術上和 OpenAPI 相近。
  - A：跟上 Connect：proto 照搬、backend 換 Python 的 Connect server、前端照搬 Go 版——兩版又相同；代價是 21 支 API 的 HTTP 層與測試重寫，Python 的 Connect 實作成熟度未查證。
  - B：分家，維持 OpenAPI——`560106d` 是最後的共同點，之後 Go 版的前端修正手動挑著搬；代價是兩版前端漸行漸遠，漂移腳本對契約與前端失去意義。
  - C：先不決定——漂移腳本滿版差異，題目遲早要回答。
  - 結論：B（Will 2026-11-05）。取代 D4 的共用前提與全域約束裡「前端與契約和 Go 版相同」的部分；後果寫進步驟 7b。重新評估：有 Python 專案要和 hawkr 共用 Connect 契約工具時。
- **D10 登入成功時清除哪些節流列**（狀態：已決 2026-11-04，Will；擋住步驟：3c 的節流部分）
  - A：清該帳號所有來源的列（Go 版照搬）——攻擊者分散在多個位址時，本人每登入一次，每個攻擊來源都拿回 5 次免等待的機會。
  - B：只清成功的那個來源；步驟 5 的帳號合計（`client_key` 為空字串的列）成功時歸零（NIST SP 800-63B-4 3.2.2「成功後忽略先前的失敗」，它的計數不分來源）；admin 解鎖與 `recover-admin` 仍清整個帳號（建議）。業界：django-axes 以帳號＋IP 鎖定時只重設成功的那組（讀原始碼）；Auth0 以使用者＋IP 封鎖，文件沒有寫別處成功會解鎖（推論）；OWASP device cookies 不讓別處的成功解鎖其他來源。代價：3c 到步驟 5 之間沒有裝置 cookie，本人與攻擊者在同一個 NAT 後面時會卡在那個位址的退避（每輪最多 1 小時），步驟 5 的信任裝置解決。
  - C：B 加上帳號合計只在未信任來源成功時歸零——和 NIST 衝突，且被拒的嘗試也會更新 `updated_at`，持續攻擊時「一天沒有嘗試」永遠不成立；屬步驟 5 的範圍，不選。
  - 結論：B；信任裝置維持在步驟 5（template 不部署，NAT 的情況現在不會發生）。重新評估：本人常在共用 IP 下被卡住且等不到步驟 5 時，提前信任裝置而不是退回 A；加了 email 重設或 MFA 時（ADR D4 的 Revocation Triggers）。ADR：3c 實作時補進 account-abuse-and-lifecycle D4。

拍板順序：D1–D10 全部已決（D10 於 2026-11-04）。

## 進度

狀態值：未開始／進行中／程式完成待驗收／完成／取消。

| 步驟 | 狀態 | 已跑的驗收 | 未跑的驗收與原因 | commit |
|---|---|---|---|---|
| 0 spike | 完成 | (a)(b)(c) 三項實測，輸出與重跑方式在 `2026-11-03-fastapi-template/spike.md` | 無 | 未提交（repo 在步驟 1 才 `git init`） |
| 0b 部署 spike | 取消 | | template 不實際部署（2026-11-03 Will），改成部署文件的檢查清單 | |
| 1 walking skeleton | 完成 | 本機：`make -C backend check`（14 測試、7 條 import-linter 合約、pyright strict 0 錯誤）、scripts 單元測試、actionlint／zizmor／gitleaks／osv-scanner；mutation：`/health` 回 500 時 `up --wait` 與 `app healthcheck` 失敗、加 `ports` 時 ports 檢查失敗、加 `# noqa` 時 suppression 鎖失敗、Action 改 tag 時 zizmor 失敗、8 種違反分層的 import 都被合約擋下。CI：run `41579063830` 五個 job 全綠、Security run `41579063799` 全綠 | stack job 的 `/health` 500 mutation 只在本機跑過（同一組 compose 指令），沒有推到 CI；之後本機不再起 compose（Will 2026-11-03） | `3643a05`、`ec63840` |
| 2a 資料庫 | 完成 | 本機：`make -C backend check`（63 個非 db 測試、7 條 import-linter 合約、pyright strict 0 錯誤）、scripts 單元測試 27 個、actionlint／zizmor／osv-scanner／gitleaks／hadolint、`docker compose config` 的 secrets 與 ports 檢查；本機 mutation 7 項（plaintext 規則、URL 密碼、healthcheck 路徑、ready 不 ping、FK 不 cascade、加 `downgrade()`、模組沒登記）各讓一個測試失敗，light-healthcheck 合約擋下 config 匯入 sqlalchemy。CI：run `41592990891` 五個 job 全綠（10 個 db 測試、`make migration` 產生空 revision、migrate exit 0 並在 baseline、app 角色 `CREATE TABLE intruder` 被拒）。CI mutation（PR #10，run `41593118433`、`41593260148`）：baseline 拿掉 `items_title_check` 讓兩個 catalog 比對與不可修改檢查失敗（`alembic check` 照預期抓不到）；模型加欄位不加 revision 讓 `alembic check` 失敗；拿掉 append-only 的 REVOKE 讓 append-only 測試失敗；app 角色可在 `public` 建表讓 db 測試與 stack 的 intruder 檢查失敗；`alembic_version` 搬回 `public` 讓 catalog 比對與 migrate 測試失敗。design review 與 `/code-review` 各一輪，處理見 2a 的「執行中的決定與偏離」 | db 測試與 `make migration` 沒在本機跑（本機不起容器，2026-11-03 Will）；| `43cda9a`、`03a161e`、`f72dfde`、`c7eeb4b`、`b84ff93`、`0c545a4`、`3efb7af`、`41a03c4`、`84905f8`、`c78a926`、`353ef8e` |
| 2b HTTP 共用層 | 完成 | CI：run `41613479903` 五個 job 全綠（含 db 測試與 compose stack 的 migrate、health）、Security run `41613479911` 全綠。本機：`make -C backend check` exit 0（149 個非 db 測試、7 條 import-linter 合約、pyright strict 0 錯誤、vulture、squawk）、`check_locks.py` 通過、osv-scanner 對新 `uv.lock` 無發現；先寫的測試在實作前全部失敗（缺模組），審查後新增的 6 個測試在修正前失敗。mutation 11 項各讓測試轉紅：驗證失敗回 openapi-core 原本的 `{"errors": [...]}`（23 個失敗）、採信傳入的 `X-Request-ID`（3）、merge-patch 不註冊 JSON 讀取器（2）、接受 `NaN`（2）、拿掉 `Cache-Control`（5）、`RecoverMiddleware` 不轉 500（6）、串流 body 不設上限（1）、欄位錯誤不去重（1）、response 開始後不重丟（2）；cursor 末字元限制的 mutation 沒被抓到，判定那條限制多餘並刪除。真實 `app serve`（MODE=prod，不接資料庫）用 curl 驗過 401、422（偽造的 `X-Request-ID` 被換掉）、415、400、prod 下 `/api/docs` 404、`/health/ready` 503，每個回應都帶安全 header 與 request id。審查：design-review、`/code-review high`、diff 視角各一輪，處理見 2b 的「執行中的決定與偏離」 | 第 2 輪審查沒跑：修正都由先失敗後通過的測試證明 | `4da9346`、`f54aec0`、`b2ac84a`、`f1d2c83`、`a03673b` |
| 2c D6 邊界與限流 | 完成 | CI：run `41643279027` 五個 job 全綠（含 db 測試、compose stack 掛 `origin_secret` 後的 migrate 與 health、frontend build 的 bundle 哨兵）、Security run `41643278985` 全綠。本機：`make -C backend check` exit 0（216 個非 db 測試、7 條 import-linter 合約、pyright strict、vulture、squawk）、`check_locks.py` 通過、scripts 的 unittest 通過、`check_vercel_config.py` 通過、前端 `pnpm lint`、42 個 vitest 通過；先寫的測試在實作前失敗（設定 14 個失敗，其餘因缺 `app.web.origin` 無法載入；前端「送出來源密鑰」一案失敗）。mutation 全部讓測試轉紅：放行不看 MODE（2）、拿掉 loopback 判斷（8）、拿掉密鑰比對（10）、沒帶密鑰也信 `X-Real-IP`（3）、密鑰不符也信 `X-Real-IP`（1）、log 拿掉 request id（2）、不限流（6）、IPv6 用完整位址（1）、health 記 info（1）、文字 log 不加引號（1）、`X-Real-IP` 用 ASCII 解碼（1）、request id 在 finally 重設（1）、`vercel.json` 刪 transform 或 route 的 `env`（policy 檢查 exit 1）、`ORIGIN_SECRET` 改成 `NEXT_PUBLIC_` 並在瀏覽器端讀取（bundle 哨兵找到）；非 ASCII 檢查的 mutation 沒被抓到，判定多餘並刪除。真實 `app serve`（MODE=prod，不接資料庫）用 curl 驗過：缺密鑰與錯誤密鑰 403、相符時 401 且 access log 的 `client_ip` 是 `X-Real-IP`、`path` 不含 query、loopback 的 `/health` 200 且不記 log。審查：`/code-review high`、diff 視角各一輪，處理見 2c 的「執行中的決定與偏離」 | 第 2 輪審查沒跑：修正都由先失敗後通過的測試證明 | `1bf5b42`、`5eea538`、`8c7ea4b`、`315d07c`、`f992a96` |
| 3a D8 產生工具 | 完成 | CI：run `41654771234` 五個 job 全綠（`make check` 含 `gen-check`，Ubuntu 上重新產生的結果與 commit 的檔案相同；220 個非 db 測試、10 個 db 測試）、Security run `41654771563` 全綠。本機：`make -C backend check` exit 0（220 個非 db 測試、7 條 import-linter 合約、pyright strict 0 錯誤）、`check_locks.py` 通過、actionlint 通過、osv-scanner 對新 `uv.lock` 無發現；開工前 `make -C backend gen-check` 回 `No rule to make target`。mutation：契約 `minLength` 3→4 不重新產生、手改產生檔、契約檔不存在，`gen-check` 都 exit 2；拿掉 `use-missing-sentinel` 後重新產生，`gen-check` 照樣通過而 4 個新測試全部失敗（所以補了測試）。審查：`/code-review medium` 與 diff 視角各一輪，findings 12（blocking 0）：修 8、記錄 2、否決 2，處理見 3a 的「執行中的決定與偏離」 | 第 2 輪審查沒跑：修正由先失敗後通過的測試證明 | `adafb3c`、`115c482` |
| 3b 帳號與登入 | 完成 | CI：run `41683936466` 五個 job 全綠（47 個 db 測試、303 個非 db 測試；compose stack 的登入流程：註冊 201、登入 201、me 200、登出 204、帶登出前的 cookie 再讀 401、自帶的 `X-Request-ID` 被換掉）。第一次推送的 run `41678595360` db 測試 21 個失敗（節流 SQL 的 deprecation），`915f11f` 修正並補上本機編譯每句查詢的測試（在 `ab17582` 是紅的）。本機：`make -C backend check` exit 0（303 個非 db 測試、7 條 import-linter 合約、pyright strict、vulture、squawk）、`check_locks.py` 通過、actionlint／zizmor 通過、osv-scanner 對新 `uv.lock` 無發現。先寫的測試在實作前失敗（密碼、設定、CSRF、`pattern`）。mutation 全部在複本裡跑（`red-proof.py`，baseline 與 canary 先過）並被殺：改回只看 cookie 存在、拿掉 `HttpOnly`、認證放在 edge 檢查外面、不清失效 cookie、雜湊不經執行緒池、CSRF 不比對 Origin 或不看 `Sec-Fetch-Site`、`pattern` 用 Python 語意或不轉 `$`、路由檢查不數標記或不比公開、`create_app` 不跑路由檢查、`serve` 不接路由錯誤、`MODE=prod` 放行不安全的 cookie、`APP_ORIGIN` 放行結尾冒號或非 ASCII、`\s` 與 `.` 不轉譯、載入時不編譯 pattern、router 直接 import sqlalchemy 或 repository（import 合約）。CI mutation：節流 key 不含位址（PR #11，run `41684339737`，2 個預期的 db 測試失敗）、dummy hash 不在啟動時算（PR #12，run `41684347386`，1 個失敗）。審查：開工 design review 14 項；完成前 `/code-review high`、diff 視角、design review 合計 23 項（去重後），處理見 3b 的偏離段 | 第 2 輪審查沒跑：每個修正都有先失敗後通過的測試或 mutation；登入成功時清掉所有來源節流的問題交 Will，2026-11-04 決定為 D10，3c 實作 | `3a343dc`、`622fe66`、`5ccfe6b`、`6346659`、`ed1f523`、`13c8e11`、`f8a3890`、`ab17582`、`915f11f`、`9a2301a`、`edba07d` |
| 3c session 資源、刪帳號與 seed-admin | 完成 | CI：run `41712342058` 五個 job 全綠（64 個 db 測試、315 個非 db 測試；compose stack 的 `make seed` 兩次：第一次 created、第二次 nothing changed、角色是 admin）、Security run `41712341826` 全綠。本機：`make -C backend check` exit 0、`check_locks.py` 通過、actionlint／zizmor 無發現；`make seed ADMIN_ACCOUNT='x$(echo PWNED)"'` 不執行帳號裡的指令。本機 mutation（`red-proof.py`，baseline 與 canary 先過）全被殺：改回清整個帳號、admin 列不加鎖、清理失敗一次後 task 停掉、清理第一次延後、seed 錯誤帶出輸入、seed 錯誤指錯變數。CI mutation：拿掉 10 分鐘重新驗證（PR #13，run `41712476678`，`test_delete_current_user` 失敗）、唯一 admin 可刪自己（PR #14，run `41712663661`，`test_the_only_enabled_admin_cannot_delete_itself` 失敗）、登入成功不清任何列（PR #15，run `41712737079`，3 個節流測試失敗）。審查：design review 3 項（修 2、記 1 進步驟 5）；`/code-review high` 10 項（修 8、記 1、駁回 1），處理見 3c 的偏離段 | 改回清整個帳號的 db mutation 沒跑：mutation 分支會讓非 db 的 `test_signing_in_clears_only_that_clients_count` 失敗而被 pre-push 擋下，本機已由它殺掉；`test_signing_in_clears_only_that_clients_wait` 斷言只剩 A 的列，涵蓋同一行為。第 2 輪審查沒跑：每個修正都有先失敗後通過的測試或 CI 結果 | `ee0869b` |
| 4 items | 完成 | CI：run `41716291944` 五個 job 全綠（77 個 db 測試、332 個非 db 測試）。本機：`make -C backend check` exit 0。本機 mutation（`red-proof.py`，baseline 與 canary 先過）5 個全被殺：get／list／update／delete 各拿掉 owner 條件、update 不設 `updated_at`（`test_items_repository.py`）。審查：`/code-review` 10 項（修 6：威脅模型補 items 列、指南不提私人工具、dependency 改 async、拿掉多餘的 state 防呆、Item 依欄位名建立、指南搬到 `docs/rules/add-module.md`；駁回 4：UUIDv7 排序與 Go 版和 sessions 相同、`Modules` 一起建是刻意（items 需要 auth）、共用的不連線 engine 沒有測試會連、Location 字面值與 auth 一致）；契約對照審查 0 blocking、2 nit（指南私人工具已修；`sleep(0.01)` 駁回：防同一微秒）。 | db 層的 owner mutation 沒推 mutation 分支跑（本機的查詢結構測試已涵蓋同一個拿掉 owner 條件的改動） | 下一步：5 |
| 5 帳號濫用與管理 | 進行中（5a 完成） | | | |
| 5a ADMIN 標記與 admin 操作 | 完成 | CI：run `41722671968` 五個 job 全綠（84 個 db 測試、335 個非 db 測試）、Security run `41722671961` 全綠。本機：`make -C backend check` exit 0、`check_locks.py` 通過、pre-push 全過；先寫的 `test_a_user_who_is_not_an_admin_is_refused_and_it_is_logged` 在實作前 3 個參數都失敗（405）。本機 mutation（`red-proof.py`，baseline 與 canary 先過）2 個全被殺：`admin` 不檢查角色、`listUsers` 標記換成 `SIGNED_IN`。CI mutation：`_keep_an_admin` 永不拒絕（PR #16，run `41723074893`，4 個預期的 db 測試失敗：唯一 admin 不能停用、並行停用留一個、admin 刪除、唯一 admin 刪自己）。審查：`/code-review medium` 8 項（blocking 0）：修 3（角色常數共用 `app.platform.principal.ROLE_ADMIN`、同一交易的 audit 時間與重新驗證用同一個 `now`、過時註解）、駁回 5（admin 停用或刪除自己時不清 cookie：下個請求 middleware 會清，Go 版相同；重複停用也寫 audit、每次停用都鎖 admin 列、停用不寫應用日誌：與 Go 版 `SetDisabled` 相同） | 第 2 輪審查沒跑：修正是重構，由既有測試與 CI 證明 | `d8272a2`（拆分）、`6827c63`；下一步：5b |
| 5b trusted devices 與帳號鎖定 | 完成 | CI：run `41763385013` 全綠（90 個 db 測試、339 個非 db 測試）、Security run `41763384999` 全綠；第一次 run `41763118445` 只有 `test_an_admin_sees_and_lifts_the_lock` 失敗，是測試的預期值算錯（等待中的來源那次不計入帳號合計，104 不是 105），`6d159c1` 修正。本機：`make -C backend check` exit 0、`check_locks.py` 通過、pre-push 全過；先寫的非 db 測試在實作前無法載入（`SessionCookie` 沒有 `device_name`）。本機 mutation（`red-proof.py`）：成功只清該來源不含帳號合計、裝置 cookie 改 `Lax`、`deleteSignInLock` 標記換成 `SIGNED_IN`，全被殺；從 `ADMIN_OPERATIONS` 拿掉解鎖在本機存活（只由 db 測試的 `set(AUDITED)` 抓）。CI mutation（PR #18，run `41763478998`）：鎖定門檻 101 加 `delete_user_trusted_devices` 不刪，4 個預期的 db 測試失敗（鎖定與信任裝置、裝置依帳號、全部登出與停用各一）；PR #17 的第一版 mutation 被 ruff 擋在 db 測試之前，不算。審查：`/code-review medium`，處理見 5b 的「執行中的決定與偏離」 | 第 2 輪審查沒跑：唯一的修正是文件符號名稱 | `d841c0c`、`6d159c1`；下一步：5c |
| 5c recover-admin 與 HIBP | 完成 | CI：run `41820270850` 全綠（94 個 db 測試、353 個非 db 測試）、Security run `41820270959` 全綠。本機：`make -C backend check` exit 0、`check_locks.py` 通過。本機 mutation（`red-proof.py`）全被殺：HIBP 失敗改成拒絕、拿掉 breach 檢查、檢查時不先做 NFKC、送出完整雜湊、不帶 padding、count 0 也算外洩、忽略 HTTP 狀態、拿掉 deadline、拿掉 User-Agent、路由檢查放過沒有 route 的 operation、recover 不限定 admin、recover 不啟用帳號。CI mutation（PR #19，run `41820506403`）：recover 不刪 sessions 與信任裝置、不清節流，1 個預期的 db 測試失敗。審查：`/code-review medium`，處理見 5c 的「執行中的決定與偏離」 | 第 2 輪審查沒跑：每個修正都有先失敗後通過的測試或 mutation。第一次 push 時 pre-push 的 `backend-check` 失敗過一次，輸出被截斷、看不到原因；重推就通過了，本機連跑 6 次也都通過，原因未查明 | `f45ed34`；下一步：6 |
| 6 rename | 完成（2026-11-05 被 D12 取代，由 6b 接手） | 本機：`scripts/test_rename.py`（在 tracked 檔的複本跑 rename：每類佔位名稱都換掉、`--repo`、未列出的檔案在寫入前就擋下且不留改動、保留字／長度／含佔位名稱的名字被拒、已改名的 clone 被拒）；複本 smoke：`rename.py ci-probe` → `uv lock --check` → `make test` 353 通過，改名後 `test_rename` 全部 skip；`make -C backend check`、`pnpm test`（42）、`pnpm gen:api:check`、scripts 單元測試、actionlint、zizmor。mutation（`red-proof.py`）全被殺：拿掉 dbtest.py、uv.lock、repo 替換、寫入前檢查、保留字、長度上限。審查：`/code-review medium` 10 條，修 8（衍生專案的 rename job 與測試在改名後跳過、先檢查再寫入、名字限制、data-inventory 的舊角色名、刪掉重複的 grep、簡化 regex），駁回 2（測試耗時實測 4 秒；rename 後跑 pytest 是本步驟驗收） | CI：run `41838884562` 六個 job 全綠（含 Rename smoke test）。衍生專案裡 job 跳過的分支只靠 `grep` 條件推論，沒在 CI 實跑。執行中的偏離：前端與契約殘留的 `template-go-nextjs` 品牌字一併改成本 repo 名稱，rename 才認得 | `874239a`；下一步：7 |
| 6b 產生到新目錄 | 未開始 | | | |
| 7a 漂移腳本與 ADR | 完成 | 本機：`scripts/test_check_go_drift.py`（5 個：一致時乾淨、共用檔改過、共用目錄只在一邊的檔、品牌名與 EXCEPTIONS 不算漂移、共用範圍外的檔不算）；`check_go_drift.py --go-repo <Go clone> --ref 560106d` 乾淨；mutation（`red-proof.py`）全被殺：拿掉品牌替換、不報只在 Go 的檔、不比對內容、不排除 EXCEPTIONS；`test_rename` 仍通過（兩支漂移腳本加進 rename 的 KEPT）；ADR 的相對連結逐一檢查存在；原 ADR 9 處 2026-11-05 修訂標記。審查：`/code-review medium` 10 條（blocking 0），修 9（rename 刪掉漂移腳本、改看工作區、EXCEPTIONS 改指向部署拓樸 ADR、偵測不再不同的例外、`sys.argv` 用 patch、測試的 git 不吃全域設定、斷言替換有效、重複註解、D5 引用補 ADR 名），駁回 1（每檔一次 `git show`：腳本不在 CI，測試實測約 25 秒內）；新加的兩個 mutation（不偵測過期例外、rename 不刪漂移腳本）都被殺 | 對 Go 版目前的 `dev`（f91118e）跑出大量漂移（Go 版加了 `frontend/buf.gen.yaml` 等），留給週回顧處理，見 ADR FastAPI 版後端的 Revocation Triggers | 見 git log；下一步：7b |
| 7b 部署指南與其餘文件 | 未開始 | | | |
| 8 收尾 | 未開始 | | | |
