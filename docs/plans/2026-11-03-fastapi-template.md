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
- 未驗證（2026-11-03 Will：template 不實際部署到 Vercel 與 Cloudflare，所以不實測）：Vercel 的 external rewrite 會轉送 `Cookie` 並把 `Set-Cookie` 傳回瀏覽器（Vercel 文件沒寫）；請求經 Vercel 與 Cloudflare 兩層之後，backend 收到的 `X-Real-IP` 仍是使用者 IP、`Host` 是什麼；`vercel.json` 的 `routes`（`dest` 與 transform 用環境變數）能和 Next.js 產生的路由並存；前端網域的 DNS 記錄不開 Cloudflare 代理時，Vercel 看到的是使用者 IP（部署文件規定不開代理）；Vercel 環境變數改值後，rewrite 的 transform 與 SSR function 要不要 redeploy 才生效（決定密鑰輪換的順序）。這幾項由步驟 7 寫成部署文件的「第一次部署檢查清單」，衍生專案第一次部署時逐項確認；其中 cookie 不能穿過 rewrite 時，改走 D6 的 B。
- 已讀來源，2026-11-03（[Vercel Request headers](https://vercel.com/docs/headers/request-headers)、[Rewrites](https://vercel.com/docs/routing/rewrites)、[Static IPs](https://vercel.com/docs/networking/static-ips)、[Limits](https://vercel.com/docs/limits)）：Vercel 覆寫 `X-Forwarded-For`，client 偽造的值傳不到上游；`X-Real-IP` 是使用者 IP；出口 IP 不固定，付費 Static IPs 只涵蓋 Functions；external rewrite 單一請求上限 120 秒；2026-04-06 之後建立的專案，external rewrite 預設照上游 `Cache-Control` 快取。

**驗收基線：** 新 repo 未建，無。規格那一側（Go 版 `560106d`）：CI 6 個 job 全綠（run `41491264746`），後端 241 個測試、前端 40 個。

## 本階段依賴的不變量

| 不變量 | 依賴它的機制 |
|---|---|
| API 契約與 Go 版相同（路徑、方法、request／response schema、problem code） | `frontend/src/lib/api/`、前端生成型別、步驟 1–5 的契約測試 |
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
| HTTP 操作 | 10 | 5 | 6 | inventory.md「HTTP 操作」表的「步驟」欄 |

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
- [ ] **2a. 資料庫**（被擋於：步驟 1、D2、D3）
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
    - squawk 不移植的理由原本寫「squawk 檢查 SQL 檔，revision 是 Python」，這不成立：`alembic upgrade --sql` 可以把 revision 輸出成 SQL 交給 squawk。**待 Will 決定**：CI 加 `alembic upgrade head --sql | squawk`（多一道 SQL 輸出步驟，換回 Go 版的自動鎖表與 expand/contract 檢查），或維持人工審查。
    - `app.modules.auth`、`app.modules.items` 先有只放 docstring 的 `router.py`、`service.py`：`.importlinter` 的 `module-layers` 把這兩層列為必要層，不為暫時狀態放寬合約。
- [ ] **2b. HTTP 共用層**（被擋於：2a）
  - 範圍：problem+json exception handler（`type`、`title`、`code` 與 Go 版 `problem.go` 相同，含 `request_id`）；request id 一律由 backend 產生、不信任傳入值；安全 header，含 `Cache-Control: no-store`（Go 版 `securityHeaders`，`middleware.go:99`）；用 openapi-core 依 `backend/api/openapi.yaml` 驗證 request（自寫 adapter，見上面的依賴說明；正式環境只驗 request，response 驗證放在測試裡，`spike.md` (b)），錯誤轉成 `/problems/validation`；cursor 分頁工具。
  - 驗收：Go 版 `problem_test.go`、`problem_contract_test.go`、`validate_test.go` 的同名案例；mutation：讓驗證失敗時回 openapi-core 原本的 `{"errors": [...]}`，契約測試要失敗；mutation：信任傳入的 `X-Request-ID`，request id 測試要失敗。
- [ ] **2c. D6 邊界與限流**（被擋於：2b）
  - 範圍：per-IP 限流（Go 版 `ratelimit`），以及以下 D6 新增的範圍：
    - 來源密鑰 middleware：`/api/*` 請求的 `X-Origin-Secret` 不符就回 403 problem，`type` 用既有的 `/problems/forbidden`、不帶 `code`（契約的 code 清單不變）；先轉成 bytes 再用 `hmac.compare_digest` 比對，非 ASCII 的 header 直接視為不符；`ORIGIN_SECRET` 可以放兩個值（逗號分隔，目前值與上一個值），輪換時新舊並存，啟動時有空項目就拒絕啟動；`MODE=prod` 沒設定就拒絕啟動。`MODE=dev` 時，沒帶 header 的請求放行（client IP 用連線來源位址，不採信 `X-Real-IP`），帶了但不符一律 403（2026-11-03 Will，因為 `next dev` 的 rewrite 帶不上 header）。`/health`、`/health/ready` 不檢查密鑰，但只接受來自 loopback 的請求（容器內的 `app healthcheck`），其他來源回 404，不依賴 tunnel 的路由規則（2026-11-03 Will）。
    - client IP 只在密鑰相符時取 `X-Real-IP`，否則用連線來源位址；request id 一律由 backend 產生，不信任傳入值；`Cache-Control: no-store` 沿用 Go 版 `securityHeaders`（`middleware.go:99`），external rewrite 的快取預設因此不會生效。
    - 設定與 secret：`scripts/init_env.py` 產生 `.secrets/origin_secret`；compose 給 backend 掛 `origin_secret` secret，加 `APP_ORIGIN` 環境變數（步驟 3 的 CSRF 用）；根目錄 `.env-example` 刪掉 `TRUSTED_PROXIES`、`SITE_ADDRESS`，加上 `APP_ORIGIN`；`backend/.env-example` 同步；CI stack job 的 secrets 檢查改成 `.services.backend.secrets | map(.source) == ["db_app_password","origin_secret"]`（和 Go 版 `ci.yml` 同寫法，`docker compose config` 輸出的 secrets 是物件陣列）。
    - `frontend/vercel.json`：一條 `/api/(.*)` route，`"dest": "${BACKEND_ORIGIN}/api/$1"`，route 的 `env` 含 `BACKEND_ORIGIN`；transform 用 `set` 設 `X-Origin-Secret`，`args` 為 `$ORIGIN_SECRET`、`env` 含 `ORIGIN_SECRET`（寫法照 Vercel 文件「Restricting your origin to Vercel traffic」與 vercel.json `routes` 的 `env` 說明）。新增 `scripts/check_vercel_config.py` 與它的 `scripts/test_check_vercel_config.py`，CI policy job 跑它：route 存在、route `env` 含 `BACKEND_ORIGIN`、`dest` 以 `/api/$1` 結尾、transform 的 `args` 剛好等於 `$ORIGIN_SECRET` 並帶 `env`、檔案裡沒有其他看起來像密鑰的字串。
  - 驗收：Go 版 `ratelimit` 測試的同名案例，加上：缺密鑰回 403（`/problems/forbidden`）；密鑰相符時限流 key 是 `X-Real-IP`；密鑰不符時自帶的 `X-Real-IP` 被忽略；舊密鑰在輪換期間仍可用；非 ASCII header 回 403；`MODE=prod` 缺密鑰啟動失敗；設定有空項目啟動失敗；`MODE=prod` 下沒帶 header 回 403、`MODE=dev` 下沒帶 header 放行且限流 key 是連線位址、`MODE=dev` 下帶錯 header 回 403；非 loopback 來源打 `/health` 回 404；mutation：把放行判斷改成不看 `MODE`，`MODE=prod` 的缺 header 測試要失敗；mutation：拿掉 `/health` 的 loopback 判斷，非 loopback 的測試要失敗；mutation：拿掉密鑰比對，缺密鑰的測試要失敗；mutation：密鑰不符時仍信任 `X-Real-IP`，偽造 IP 的測試要失敗；mutation：刪掉 `vercel.json` 的 transform，或拿掉 route 的 `env`，policy 檢查要失敗。
- [ ] **3. auth**（被擋於：步驟 2c）
  - 範圍：inventory 標步驟 3 的 10 個操作（註冊、登入登出、session 列表與撤銷、`users/me`）、`seed-admin`、CSRF 防護、依帳號節流。CSRF 照 Go 版先看 `Sec-Fetch-Site`；後備的 Origin 比對改成比對設定的公開網址（`APP_ORIGIN`），因為 backend 收到的 `Host` 不是瀏覽器看到的網域（D6）。
  - 消費端：`rg -n 'sessions|users/me' frontend/src`。
  - 不能動：cookie 名稱與屬性、session 期限、錯誤 code。
  - 驗收：每個操作有成功與至少一個失敗案例的測試，案例對照 Go 版 `auth_test.go`、`auth_resources_test.go`；CI stack job 在 compose network 上用一個 curl 容器直接連 `backend:8000`，每個請求帶 `.secrets/origin_secret` 的值，跑一次「註冊 → 登入 → `GET /api/users/me` 回 200 → 登出 → 再讀回 401」；Go 版「client 自帶的 request id 會被覆寫」檢查改用同一個 curl 容器對 backend 驗；前端伺服器端呼叫帶密鑰的測試（加在 `server.test.ts`）；CI frontend job 用 `ORIGIN_SECRET=<哨兵值> NEXT_PUBLIC_ORIGIN_SECRET=<同一個哨兵值> pnpm build` 建置後跑 `! rg -F <哨兵值> .next/static`，mutation：把 `env.ts` 改讀 `NEXT_PUBLIC_ORIGIN_SECRET`，這個檢查要失敗；mutation：拿掉 cookie 的 `HttpOnly`，測試要失敗。；`backend/src/app/modules/auth/router.py`、`service.py` 不再只有 docstring。
- [ ] **4. items**（被擋於：步驟 3）
  - 範圍：inventory 標步驟 4 的 5 個操作，含 owner 權限與分頁。
  - 消費端：`rg -n 'items' frontend/src`。
  - 驗收：對照 Go 版 `items_test.go`、`authz_test.go`；mutation：拿掉 owner 檢查，跨使用者讀取的測試要失敗；`backend/src/app/modules/items/router.py`、`service.py` 不再只有 docstring。
- [ ] **5. 帳號濫用與管理**（被擋於：步驟 3）
  - 範圍：inventory 標步驟 5 的 6 個 admin 操作、`recover-admin`、trusted devices、登入鎖定、稽核紀錄、HIBP 密碼檢查（ADR account-abuse-and-lifecycle）。
  - 驗收：對照 Go 版 `backend/internal/httpapi/throttle_test.go`、`users.go` 對應的 `authz_test.go` 案例、`backend/internal/modules/auth/service_test.go` 的 devices 與鎖定案例；mutation：把鎖定門檻加大，鎖定測試要失敗。
- [ ] **6. rename 腳本與 smoke test**（被擋於：步驟 1）
  - 範圍：恢復步驟 1 拿掉的根目錄 `make rename` target 與 CI rename job（步驟 1 時 rename 腳本還是 Go 版，先移除）；`scripts/rename.py` 改成 Python 版（套件名、DB 與角色名、image、品牌字，沒有 module path）；CI rename job。（2a 起 `backend/tests/dbtest.py` 寫著角色與資料庫的佔位名稱，要納入替換或改成從設定讀取。）
  - 驗收：CI 跑 `make rename NAME=ci-probe` 後 `rg` 找不到佔位名稱、pytest 通過；mutation：刪掉一個替換規則，smoke job 要失敗。
- [ ] **7. 與 Go 版的漂移檢查與文件**（被擋於：D4、步驟 3）
  - 範圍：依 D4 做共用檔清單與比對腳本；`docs/` 的 Go 專屬段改寫；刪除 `go-backend` ADR，D1–D3、D6 寫成本 repo 的 ADR；D6 的 ADR 取代 Go 版 ADR `api-and-auth-baseline` 的 D1（拓樸）、D6（IP 來源）、D8（proxy 部分），以及它的約束段、Expected Outcome、Revocation Triggers 中提到 Caddy 的部分，照 ADR 修訂流程在原 ADR 標註；D6 的 ADR 寫明 dev 與正式不再是同一套 proxy 這個代價；`docs/deploy-vps.md` 改寫成「Vercel + VM + Cloudflare Tunnel」部署指南（含 Vercel Function 區域、前端 DNS 不開代理、密鑰輪換步驟）；`docs/security/threat-model-auth.md` 的信任邊界改寫（瀏覽器 → Vercel → Cloudflare → backend）；`docs/privacy/data-inventory.md` 把 Caddy log 換成 Vercel 與 Cloudflare（兩者都終止 TLS，是新的資料處理者）；`docs/frontend-known-issues.md` 的同源說明；`frontend/AGENTS.md`、`frontend/README.md`；`.github/dependabot.yml` 的 caddy 註解換成 cloudflared，cloudflared image 釘 digest。
  - 驗收：腳本對一個故意改過的共用檔回報差異，對未改的回報乾淨；`rg -n -i 'golang|gofmt|sqlc|go test|golangci' docs/ AGENTS.md README.md` 沒有結果，或每筆都附保留理由；`rg -n -i -g '!docs/plans/**' -g '!docs/adr/2026-10-18-api-and-auth-baseline.md' -g '!docs/adr/<D6 ADR 檔名>' 'caddy|TRUSTED_PROXIES|SITE_ADDRESS|lb_try_duration|172\.30' .` 沒有結果（原 ADR 只標修訂、不改原文；D6 的 ADR 必須寫出它取代了什麼；兩者都排除）；D6 的 ADR 檔案存在並列出它取代原 ADR 的哪幾段，`rg -n '修訂' docs/adr/2026-10-18-api-and-auth-baseline.md` 在那幾段都找得到標記；部署文件有「第一次部署檢查清單」，逐項對應前提段「未驗證」那條（cookie 穿過 rewrite、`X-Real-IP`、`Host`、transform 覆寫、`no-store` 不被快取、SSR 帶密鑰、HSTS、改環境變數後是否要 redeploy、tunnel 只轉送 `/api/*`），每項附確認用的指令；`rg -c` 檢查清單的項數等於那條列的項數。
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

拍板順序：D1–D7 全部已決（2026-11-03）。

## 進度

狀態值：未開始／進行中／程式完成待驗收／完成／取消。

| 步驟 | 狀態 | 已跑的驗收 | 未跑的驗收與原因 | commit |
|---|---|---|---|---|
| 0 spike | 完成 | (a)(b)(c) 三項實測，輸出與重跑方式在 `2026-11-03-fastapi-template/spike.md` | 無 | 未提交（repo 在步驟 1 才 `git init`） |
| 0b 部署 spike | 取消 | | template 不實際部署（2026-11-03 Will），改成部署文件的檢查清單 | |
| 1 walking skeleton | 完成 | 本機：`make -C backend check`（14 測試、7 條 import-linter 合約、pyright strict 0 錯誤）、scripts 單元測試、actionlint／zizmor／gitleaks／osv-scanner；mutation：`/health` 回 500 時 `up --wait` 與 `app healthcheck` 失敗、加 `ports` 時 ports 檢查失敗、加 `# noqa` 時 suppression 鎖失敗、Action 改 tag 時 zizmor 失敗、8 種違反分層的 import 都被合約擋下。CI：run `41579063830` 五個 job 全綠、Security run `41579063799` 全綠 | stack job 的 `/health` 500 mutation 只在本機跑過（同一組 compose 指令），沒有推到 CI；之後本機不再起 compose（Will 2026-11-03） | `3643a05`、`ec63840` |
| 2a 資料庫 | 進行中 | | | |
| 2b HTTP 共用層 | 未開始 | | | |
| 2c D6 邊界與限流 | 未開始 | | | |
| 3 auth | 未開始 | | | |
| 4 items | 未開始 | | | |
| 5 帳號濫用與管理 | 未開始 | | | |
| 6 rename | 未開始 | | | |
| 7 漂移檢查 | 未開始 | | | |
| 8 收尾 | 未開始 | | | |
