# 附件：步驟 3 auth 的 carry-over 盤點

快照：`template-go-nextjs@560106d`（2026-11-03）。範圍：`backend/internal/modules/auth/`、`backend/internal/httpapi/{auth,authz,users,server}.go`、`backend/cmd/app/{main,seed}.go`，以及它們的測試。去向：保留＝照 Go 版行為改寫成 Python；改寫成 X＝行為相同、做法換成 X；刪除＝不帶過來（附理由）。子步驟：3b、3c 見計畫步驟 3；5＝步驟 5（帳號濫用與管理）。

重跑：`git -C <go repo> show 560106d:<path>`；Go 測試清單 `rg -n '^func Test' backend/internal/httpapi/{auth,auth_resources,authz,server,throttle}_test.go backend/internal/modules/auth/service_test.go backend/cmd/app/seed_test.go`。

## 拆分（3b／3c）

依據：Go 版這部分的程式約 1,500 行、測試約 1,700 行；除了密碼雜湊與 CSRF，幾乎每個測試都要資料庫，本機不跑（只在 CI），每輪驗證都要推到 CI，一個 session 做不完 11 個操作加 seed-admin。

- **3b 帳號與登入**：設定、密碼、session 核心、認證 middleware 與 principal、CSRF、存取標記與契約／路由一致檢查，以及 7 個操作：`createUser`、`createSession`、`getCurrentUser`、`deleteCurrentSession`、`getUser`、`getSession`、`getCurrentSession`（最後一個 2026-11-04 從 3c 移入：`/sessions/{id}` 先上線時會吃掉 `/sessions/current`）。`getUser`、`getSession` 放進來，是因為 `createUser`、`createSession` 回的 201 `Location` 指向它們（ADR api-and-auth-baseline D9：201 的 `Location` 要能 GET；Go 測試的 `followLocation` 對每個 201 都這樣檢查）；`getUser` 原本在 inventory 標步驟 5，改到 3b。CI stack job 的 curl 流程只用到這 6 個操作，也放 3b。
- **3c session 資源、刪帳號與 seed-admin**：`listSessions`、`deleteAllSessions`、`deleteSession`、`deleteCurrentUser`、`seed-admin`（含根目錄 `make seed`）、每小時的清理。

## 資料存取與組裝（3b 開工時定，design review 2026-11-03）

- 查詢：SQLAlchemy Core 的運算式，欄位取自 `models.py` 的 ORM 類別（`select(User.id, …)`、`insert(…).returning(…)`、`postgresql.insert(…).on_conflict_do_update(…)`），在 `AsyncConnection` 上執行；不用 ORM `Session` 的 unit of work 與 identity map：每個呼叫就是一句明確的 SQL，與 Go 的 sqlc 對得上，async 下也沒有 lazy load。不用 `text()` 拼 SQL：ruff 的 S608 會擋字串拼接的 SQL，威脅建模的 SQL injection 列改用這兩項當證據。
- 交易：service 的方法用 `async with engine.begin()`（要交易的）或 `engine.connect()`（只讀）；雜湊在交易外（#12）。複雜的查詢（節流 upsert、session 與 user 的 join）放 `repository.py`，接受呼叫端給的連線，交易仍由 service 決定。
- 組裝：`app.main.server`（composition root）建立 `AuthService`（engine、`Settings` 的期限、時鐘、hasher），交給 `create_app(auth=…)`；router 經 `dependencies.py` 的依賴從 `request.app.state` 取得；認證 middleware 在建構時收到。
- principal：認證 middleware 把 principal（`id`、`role`）放進 `app.platform` 的 ContextVar，給 openapi-core 的 security provider（它只拿得到 `RequestParameters`）與 `app.web.access` 的 `SIGNED_IN` 標記用（各模組共用，模組之間不能互相 import）；`session_invalid` 也定義在 `app.platform.principal`，只有一份；router 用依賴取得目前的使用者，再**顯式**傳給 service（FastAPI 文件「Get Current User」的做法，pyright strict 擋漏傳），service 不讀 ContextVar（取代 #17 原本的寫法）。
- import-linter：`sql-below-the-router` 改成只擋直接 import（`allow_indirect_imports = True`），並把 `app.modules.*.repository`、`app.modules.*.models`、`app.platform.db` 列為禁止（完成前 design review：只禁 sqlalchemy 時 router 可以經它們跑 SQL）。原設定連間接 import 一起擋，而 router → service → sqlalchemy 是 D7 要的分層，第一個呼叫 service 的 router 就會讓合約失敗（審查者在 scratchpad 實測：`Routers do not run SQL BROKEN … router -> service -> sqlalchemy`）；合約要守的是「router 不跑 SQL」，直接 import 就足以涵蓋。`http-stays-at-the-edge` 仍檢查間接 import（service 經 `app.web` 碰到 starlette 也要擋）。改 `.importlinter` 的 commit 帶 `Quality-Gate`。
- middleware 順序（外到內）：`RequestId → Client → AccessLog → SecurityHeaders → Recover → CrossOrigin（CSRF）→ OriginGuard → Authenticate（只 `/api`）→ ContractValidation`。CSRF 與 OriginGuard 只看 header，放在查 DB 的認證外面：密鑰不符的請求帶 cookie 也不會碰 DB；認證在 Recover 裡面，DB 例外變成帶 request id 與安全 header 的 500（Go 版 `authenticate` 也在 `recoverPanics`、`CrossOriginProtection` 裡面）。
- 認證 middleware 放 `app.modules.auth.dependencies`（模組的 HTTP 層；module-layers 合約只認 router／schemas／dependencies／service／repository／models），`AGENTS.md` 的分層表寫明 `dependencies.py` 也放 app factory 掛上的 ASGI middleware。

## 文件（AGENTS.md「在同一個改動裡更新」）

| 文件 | 改什麼 | 子步驟 |
|---|---|---|
| `docs/security/threat-model-auth.md` | 入口表與各列改指 Python 的檔案與測試：session、cookie、CSRF（`APP_ORIGIN`）、密碼（argon2-cffi、專用執行緒池的並行上限）、節流、SQL injection（Core 運算式加 ruff S608） | 3b；3c 補刪帳號、session 撤銷、seed-admin |
| `docs/rules/api-design.md` | 分層、認證、授權、限流段：`internal/httpapi`、`operationAccess`、`limitOperations` 換成 router、存取標記、`RATE_LIMITED` | 3b |
| `docs/rules/database-design.md` | 查詢與 Transaction 段換成上一節的寫法 | 3b |
| `docs/privacy/data-inventory.md` | 帳號、密碼雜湊、session 的存放與日誌欄位核對（Go 版已列，核對 Python 的欄位與 log 名稱） | 3b |
| `AGENTS.md` | Status 段、分層表的 `dependencies.py` 說明 | 3b、3c |

## service（`modules/auth`）

| # | Go 機制（位置） | 去向 | 子步驟 |
|---|---|---|---|
| 1 | `RoleAdmin`／`RoleUser` 常數（`auth.go:34`） | 保留 | 3b |
| 2 | 錯誤 code 常數（`auth.go:42`）：`account_taken`、`password_too_common`、`invalid_credentials`、`login_throttled`、`session_invalid` | 保留（值與契約相同） | 3b |
| 3 | 同上：`last_admin`、`reauthentication_required` | 保留 | 3c |
| 4 | `Config` 檢查（`auth.go:124`）：期限為正、idle 不超過 absolute、`Now` 必填 | 改寫：期限規則只在 `Settings.from_env` 檢查一次（和其他設定一起收集後回報）；service 收 `Settings` 的值與時鐘，不重驗 | 3b |
| 5 | `Argon2Hasher`（`password.go:45`）：argon2id，OWASP 下限 m=19 MiB、t=2、p=1，PHC 字串，用雜湊裡的參數驗證，無法解析的雜湊判為不符 | 改寫成 `argon2-cffi`（`PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1, hash_len=32, salt_len=16)`，`verify` 讀雜湊裡的參數；不是 `$argon2id$` 開頭、`VerifyMismatchError`、`InvalidHashError`、`VerificationError` 都判為不符）。依賴已核准（2026-11-03 Will），見計畫步驟 3 的依賴段 | 3b |
| 6 | 雜湊並行上限（`password.go:53`，最多 `NumCPU` 個，等待時隨 context 取消） | 改寫：專用的 `ThreadPoolExecutor(max_workers=os.process_cpu_count())`，以 `loop.run_in_executor` 執行（argon2-cffi 經 cffi 呼叫時釋放 GIL）。上限由 worker 數保證：等待者被取消時，還沒開始的工作一起取消，已開始的跑完才釋放 worker，所以並行數不會超過上限（`Semaphore` 包 `to_thread` 的寫法在等待者被取消時會先釋放名額，design review 2026-11-03）。uvicorn 在 client 斷線時不取消 app 的 task，所以「client 走了就不再排隊」不成立：Go 版靠 net/http 的 context 取消，這裡接受排隊的請求照樣算完（觸發條件：出現雜湊排隊造成的延遲時，加排隊上限並回 503） | 3b |
| 7 | `dummyHash`（`auth.go:119`）：不存在的帳號也算一次雜湊 | 保留：app 啟動時（lifespan 呼叫 `AuthService.start`）算一次；沒經過 lifespan 的呼叫在第一次用到時算 | 3b |
| 8 | `normalizePassword` NFKC（`password.go:19`） | 保留（`unicodedata.normalize("NFKC", …)`）；契約的長度限制算原始輸入 | 3b |
| 9 | `accountKey` 轉小寫（`auth.go:148`） | 保留（契約 pattern 限 ASCII，`str.lower` 與 Go `ToLower` 結果相同） | 3b |
| 10 | `blocklist`（SecLists 常見密碼 5,406 行與 MIT 授權檔）；載入時每一行先 NFKC 正規化 | 保留：照搬資料檔與授權檔到 `app/modules/auth/`，第一次用到時逐行 NFKC 後載入成 `frozenset`；wheel 要包含 `.txt`（CI stack job 的註冊流程會用到） | 3b |
| 11 | `checkNewPassword` 的 HIBP 檢查與失敗放行（`auth.go:154`） | 保留（`AuthService._breached`；用戶端在 `app.platform.hibp`，標準庫 `http.client` 放在執行緒，不加依賴；失敗的錯誤訊息不含請求路徑） | 5c |
| 12 | `CreateUser`（`auth.go:184`）：雜湊在交易外、建帳號與 session 同一交易、unique violation 回 409 `account_taken`、記 `auth.sign_up` | 保留；改寫：用 `INSERT … ON CONFLICT (account) DO NOTHING RETURNING`（與 Go 的 `CreateUserIfAbsent` 同形），沒有回傳列就是 409，不靠例外控制流程 | 3b |
| 13 | `EnsureAdmin`（`auth.go:212`） | 保留 | 3c |
| 14 | `RecoverAdmin`（`auth.go:237`） | 保留（`AuthService.recover_admin`、`repository.recover_admin`） | 5c |
| 15 | `audit()` 與 `AuditUserDeleted`（`auth.go:273`） | 保留，`deleteCurrentUser` 用到；其他 audit action 在步驟 5 | 3c |
| 16 | `keepAnAdmin` 與 `LockEnabledAdmins … FOR UPDATE`（`auth.go:297`） | 保留，`deleteCurrentUser` 用到 | 3c |
| 17 | `caller(ctx)`（`auth.go:310`） | 改寫：router 用依賴取得目前的使用者，顯式傳給 service（見「資料存取與組裝」的 principal）；`app.platform` 的 ContextVar 只給 security provider | 3b |
| 18 | `User(id)`（`auth.go:320`）：本人或 admin 可讀，其他人 404 | 保留（inventory 原標步驟 5，改 3b，理由見「拆分」） | 3b |
| 19 | `ListUsers`、`SetDisabled`（`auth.go:340`、`359`） | 步驟 5 | 5 |
| 20 | `isUniqueViolation`（`auth.go:387`） | 刪除：#12 改用 `ON CONFLICT DO NOTHING` | — |
| 21 | `ReauthenticateWithin` 10 分鐘、`recentlySignedIn`、`DeleteOwnAccount`、`deleteUser`（`auth.go:395-456`） | 保留 | 3c |
| 22 | `DeleteUser`（admin 刪別人） | 步驟 5 | 5 |
| 23 | `touchEvery` 1 分鐘，idle 短於 2 分鐘時改用 idle/2（`sessions.go:23`、`77`） | 保留 | 3b |
| 24 | `newToken`（32 bytes base64url，43 字元）、`hashToken`（SHA-256，DB 只存雜湊） | 保留（`secrets.token_urlsafe(32)`、`hashlib.sha256`） | 3b |
| 25 | `newSession`、`Authenticate`（`sessions.go:43-84`）：讀 session 與 user、absolute 與 idle 期限、停用的帳號、touch | 保留 | 3b |
| 26 | `SignIn` 的 per-client 節流（`reserveAttempt`，client key 是 `ip:` 加上 `limitKey`：IPv4（IPv4-mapped 先轉回 IPv4）或 IPv6 的 /64，沒有位址時是 `unknown`，`users.go:86-91`；ADR account-abuse D4）。key 的規則與 2c 的 `IPLimiter` 共用：`app.web.ratelimit._limit_key` 改成公開的函式，router 算好 client key 再傳給 service；`verifyCredentials`（dummy hash、停用與密碼錯誤同一個回應、`signInFailed` 記原因）、成功時清節流、刪掉請求帶的舊 session、發新 session | 保留 | 3b |
| 27 | `SignIn` 的 trusted device（`device:<id>` client key、發 device cookie）與帳號層級的 untrusted 計數與鎖定（`devices.go` 全部） | 步驟 5 | 5 |
| 28 | `ReserveLoginAttempt` SQL（一句 upsert：第 5 次起等 2^(n-5) 分鐘、最多 1 小時，被擋的嘗試不計）、`GetLoginThrottle`、`ClearLoginThrottles` | 保留（同一句 SQL 的語意；用 SQLAlchemy Core 的 `insert().on_conflict_do_update(where=…)`，等待時間用 `make_interval`（`Interval` 乘整數在 SQLAlchemy 2.1 是 deprecated），行為由 Go 的 schedule 與並行測試守住；成功時清除的範圍改寫：只清這次的來源（3c 的 `clear_login_throttle`），清整個帳號留給 admin 解鎖與 `recover-admin`（計畫 D10，2026-11-04 Will）；整個帳號的 `ClearLoginThrottles` 在 3c 沒有呼叫端，vulture 不讓沒用到的函式留著，所以 3c 刪掉 3b 的 `clear_login_throttles`，步驟 5 照 Go 版加回） | 3b；D10 的改寫在 3c |
| 29 | `SignOut`（`sessions.go:222`）：空或未知的 token 不是錯誤，記 `auth.sign_out` | 保留 | 3b |
| 30 | `endEverywhere`（刪 sessions 與 trusted devices）、`SignOutAll` | 保留刪 sessions 的部分（3c）；刪 trusted devices 的那句在步驟 5 與 devices 一起加（3c 時沒有任何 device 會被建立） | 3c、5 |
| 31 | `Sessions`、`Session`、`RevokeSession`（`sessions.go:264-325`）：只看本人、只看未過期的 | `Session`（`getSession`）3b；其餘 3c | 3b、3c |
| 32 | `Cleanup` 與 serve 的每小時排程（`sessions.go:334`、`main.go:109`） | 保留刪過期 sessions 與 1 天未動的節流列（3c）；刪過期 devices 在步驟 5 | 3c、5 |

## HTTP（`httpapi`）

| # | Go 機制（位置） | 去向 | 子步驟 |
|---|---|---|---|
| 33 | cookie 名稱：`COOKIE_SECURE` 為 true 時 `__Host-session`，false 時 `session`（`config.go:46`） | 保留 | 3b |
| 34 | device cookie 名稱與 `SameSite=Strict`（`auth.go:115`） | 步驟 5 | 5 |
| 35 | `authenticate` middleware（`auth.go:67`）：讀 cookie、`Authenticate`、principal 放進 context；無效時在回應清掉 cookie（handler 自己設 cookie 時取代它）；DB 錯誤回 500；在契約驗證之前 | 改寫成 ASGI middleware，放 `app.modules.auth.dependencies`（模組的 HTTP 層），由 `app.main.factory` 裝在 `ContractValidation` 外面；完整的 user、session id 與原始 token 放在 request scope（auth 模組自己用），`app.platform` 只放 principal | 3b |
| 36 | `requireSession`（`auth.go:101`）：契約 security 的檢查看 principal | 改寫：openapi-core 的 `security_provider_factory` 換成讀 `app.platform` principal 的 provider；取代 2b 只看 `__Host-session` 存不存在的做法（計畫 3b 範圍 (a)） | 3b |
| 37 | cookie 屬性（`auth.go:126`）：HttpOnly、`Path=/`、無 `Domain`、`Secure` 依設定、`SameSite=Lax`、`Max-Age` 為 absolute 期限；清除用 `Max-Age=0` | 保留 | 3b |
| 38 | `operationAccess` 表與 `adminOperations` 檢查（`authz.go:41-98`）：每個操作都要列、public 必須等於契約 `security: []` | 改寫成 route 層級的存取標記（列舉 route 用 `fastapi.routing.iter_route_contexts(app.routes)`、讀 `context.dependant`，非 `APIRoute` 的只能是 docs 與 health；並斷言列舉結果含已知的 route，空列表不能讓「每個 route 剛好一個標記」空洞地通過。FastAPI 0.137 起 `include_router` 延遲展開，`wiki/tech/fastapi-included-router-route-walking.md`）（D7「啟動時檢查每個路由都標了存取權限」）：每個 `/api` route 用 `PUBLIC` 或 `SIGNED_IN` 依賴（步驟 5 加 `ADMIN`），`app.main` 啟動時檢查：每個 `/api` route 剛好一個標記；`PUBLIC` 等於契約 `security: []`；契約的 `(method, path)` 集合等於 `/api` route 集合加上「尚未實作」集合、路徑參數名稱相同（計畫 3b 範圍 (b)）。「尚未實作」集合列在 `app.main`，3c、4、5 各自移除自己的操作，步驟 5 完成時刪掉這個集合（必要條件：還有契約操作沒有 route；沒有它，3b 的一致檢查在 4、5 完成前無法成立） | 3b |
| 39 | `requireRole`、`insufficient_role`、`authz.denied` 記錄（`authz.go:109-130`） | 步驟 5（3b、3c 沒有 admin 操作） | 5 |
| 40 | `methodName`、`operations()`（Go 的 strict middleware 以產生的方法名稱辨識操作） | 刪除：Go 專屬；Python 的限流與存取標記直接掛在 route 上，沒有名稱查表 | — |
| 41 | handler：`toUser`、`toSession`（時間轉 UTC）、201 的 `Location`、`createSession` 回 `Session` 並標 `current`（`users.go`） | 保留，用 3a 產生的 `app.web.contract_models`（D8） | 3b、3c |
| 42 | `getCurrentSession`：session 在 authenticate 之後被撤銷時回 401 `session_invalid`（`users.go:205`） | 保留（2026-11-04 從 3c 移入 3b） | 3b |
| 43 | `deleteSession` 撤銷目前的 session 時清 cookie（`users.go:221`） | 保留 | 3c |
| 44 | 限流掛在 `CreateUser`、`CreateSession`（`server.go:97`） | 保留：兩個 route 加 2c 的 `RATE_LIMITED` | 3b |
| 45 | CSRF（位置見「資料存取與組裝」的 middleware 順序；`Sec-Fetch-Site` 與 `Origin` 能原樣穿過 Vercel 與 Cloudflare 是未驗證的前提，列入計畫前提段「未驗證」與步驟 7 的檢查清單）：`http.CrossOriginProtection`（`server.go:189`）：非 GET／HEAD／OPTIONS 的請求，`Sec-Fetch-Site` 不是 `same-origin`／`none` 就 403；沒有 `Sec-Fetch-Site` 時比對 `Origin` 與 `Host`；兩個 header 都沒有就放行（非瀏覽器）；403 problem 帶 `cross_origin_request` | 改寫成 `app.web` 的 ASGI middleware；後備的 `Origin` 改比對設定的 `APP_ORIGIN`（D6：backend 收到的 `Host` 不是瀏覽器看到的網域）。`APP_ORIGIN` 在 `Settings` 必填，必須是 `scheme://host[:port]`、沒有路徑 | 3b |
| 46 | 安全事件記錄：`auth.sign_up`、`auth.sign_in`、`auth.sign_in_failed`（含 `reason`，不記送來的帳號名稱）、`auth.sign_out`，帶 `user_id` 與 `request_id` | 保留（logger 的 `extra`）；`auth.sign_out_all` 在 3c；`authz.denied` 在步驟 5 | 3b、3c |

## CLI 與部署（`cmd/app`）

| # | Go 機制（位置） | 去向 | 子步驟 |
|---|---|---|---|
| 47 | 設定：`SESSION_ABSOLUTE`（預設 168h）、`SESSION_IDLE`（24h）、`COOKIE_SECURE`（true） | 保留，加進 `Settings`（compose 與 `.env-example` 已有，2c） | 3b |
| 48 | `PASSWORD_HIBP_CHECK` | 保留（`Settings`、`SeedSettings`） | 5c |
| 49 | `seed-admin`：`ADMIN_ACCOUNT`、`ADMIN_PASSWORD`（可用 `_FILE`，`make seed` 用 `/dev/stdin`）、以契約的 `CreateUserRequest` 驗證輸入（訊息只寫變數名與規則，不帶值）、`EnsureAdmin`、輸出訊息；根目錄 `make seed` | 保留；改寫：用 3a 產生的 `contract_models.CreateUserRequest` 驗證（`gen-check` 保證與契約同步），錯誤用 `errors(include_input=False)`，不讀契約檔，所以計畫 3b 範圍 (c) 的「契約路徑」不再需要決定（Go 讀內嵌契約是因為 oapi-codegen 不產生驗證程式） | 3c |
| 51 | 契約 `pattern` 的語意：Go 的 kin-openapi 用 RE2，`$` 只匹配字串結尾 | 改寫：openapi-core 經 jsonschema 用 Python `re.search`，`$` 也匹配結尾換行之前，`"abc\n"` 會通過 `CreateUserRequest.account` 的 `^[A-Za-z0-9_.-]+$`（審查者實測；DB 的 CHECK 也擋不住）。OpenAPI 規定 `pattern` 是 ECMA-262 方言，`$` 只匹配結尾，所以修 openapi-core 這一層的 `pattern` 判斷，回 `pattern` 欄位錯誤；先寫會失敗的測試（`"abc\n"` 回 422、`pattern`） | 3b |
| 50 | `recover-admin` 與 `make recover-admin` | 保留（`app.main.recover`；和 `seed-admin` 共用 `run_admin_command` 與建 `AuthService` 的組裝，session 期限改成 `SessionLimits` 預設值，不再傳） | 5c |

## 測試對照

| Go 測試 | 子步驟 | 備註 |
|---|---|---|
| `TestCreateUserSetsSessionCookie`、`TestCreateUserDuplicate`、`TestCreateUserConcurrentSameAccount`、`TestCreateUserPasswordTooShort`、`TestCreateUserNoCompositionRule`、`TestCreateUserCommonPassword` | 3b | 加 `"abc\n"` 帳號回 `pattern`（#51） |
| `blocklist_test.go` `TestContains`；`config_test.go` `TestConfigDefaults`、`TestCookieNameFollowsSecure`、`TestConfigSessionIdleNotAboveAbsolute` | 3b | 不需資料庫 |
| `config_test.go` `TestLoadSeed` | 3c | |
| `throttle_test.go` `TestSignInThrottlePerClient` | 3b | 另一個位址登入同一帳號不受影響；加 IPv6 同 /64 不同位址共用節流 |
| `TestCreateUserBreachedPassword` | 5c | `test_create_user_breached_password`（db）；不需資料庫的部分在 `test_hibp.py` |
| `TestCreateSession`、`TestCreateSessionWrongPassword`、`TestCreateSessionUnknownAccountRunsHash`、`TestCreateSessionIssuesNewToken`、`TestCreateSessionMixedCaseAccount`、`TestPasswordNormalized`、`TestCreateSessionDisabledAccount`、`TestCreateSessionReturnsSession` | 3b | 停用帳號用 SQL 設定（`SetDisabled` 在步驟 5） |
| `TestMe`、`TestMeWithoutCookie`、`TestMeWithUnknownToken`、`TestInvalidSessionCookieCleared`、`TestMeIgnoresBearer`、`TestMeDisabledAccount`、`TestMeAfterUserDeleted`、`TestSessionIdleExpiry`、`TestSessionAbsoluteExpiry`、`TestShortIdleLimitKeepsUsedSession` | 3b | |
| `TestDeleteCurrentSessionClearsCookie`、`TestDeleteCurrentSessionRevokes` | 3b | |
| `TestGetUser`、`TestGetOtherUserNotFound` | 3b | admin 讀別人：用 SQL 把角色設成 admin（role 每個請求從 DB 讀） |
| `TestUnauthenticatedBeforeValidation` | 3b | Go 用 `PATCH /users/{id}`（步驟 5）；改用 `POST /api/items` 帶不合法 body（契約驗證在 route 之前，route 不必存在）；另加無效 cookie 的版本（計畫 3b 驗收） |
| `TestOperationAccessEnforced` | 3b | 對契約的每個操作：沒 session 時非 public 回 401；admin 部分在步驟 5 |
| `TestAdminOperationsChecksTable` | 3b | 改寫成 #38 的啟動檢查測試：缺標記、多標記、public 與契約不符、少 route、多 route、參數名不同 |
| `TestLoginThrottlePerAccount`、`TestLoginThrottleConcurrent`、`TestLoginThrottleSchedule`、`TestRateLimitAuthOperations` | 3b | |
| `TestCredentialsNeverLogged`、`TestSecurityEventsLogged` | 3b、5c | 3b 去掉 HIBP 與 `authz.denied` 的部分，5c 補回 |
| `TestArgon2Hash`、`TestArgonConcurrencyBounded` | 3b | 不需資料庫，本機跑；加「等待者被取消後並行數仍不超過上限」 |
| （新增）認證 middleware 的位置 | 3b | 密鑰不符但帶 cookie 的請求回 403，且沒有查 DB |
| `TestCrossOriginPostRejected` | 3b | 不需資料庫；加一案：`Origin` 等於 `APP_ORIGIN` 時放行 |
| `TestGetCurrentSession` | 3b | 2026-11-04 從 3c 移入 |
| `spec_test.go` `TestEveryWritablePathHasGet` | 3b | 契約檢查，原本漏列 |
| `TestListSessions`、`TestListSessionsExcludesExpired`、`TestDeleteSession`、`TestDeleteOtherUsersSession`、`TestDeleteAllSessions` | 3c | |
| `TestDeleteCurrentUser`、`TestDeleteCurrentUserLastAdmin` | 3c | Go 用 `mustCreateItem`（步驟 4）；改成 SQL 直接插一筆 item 驗 cascade |
| `TestSeedAdminCreatesAdmin`、`TestSeedAdminRejectsShortPassword`、`TestSeedAdminExistingAccountUnchanged`、`TestCleanup` | 3c | |
| `TestAdminDisablesUserRevokesSessions`、`TestLastAdminCannotBeDisabled`、`TestConcurrentAdminDisableLeavesOne`、`TestNonAdminCannotPatchUser`、`TestListUsers`、`TestNonAdminCannotListUsers`、`TestAdminDeletesUser`、`TestAdminChangesAreAudited`、`TestRecoverAdmin`、`throttle_test.go` 其餘 | 5 | |
| `TestOperationsRejectsUnknownName` | — | 刪除，隨 #40 |
