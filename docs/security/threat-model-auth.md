# 威脅建模：認證與 session

用 STRIDE 逐項檢查帳號、密碼、session 與角色在目前程式碼裡的做法。每個緩解措施都寫出程式碼或證明它的測試；找不到程式碼的威脅列為 Open。設計的決定在 [API 與認證基準](../adr/2026-10-18-api-and-auth-baseline.md)（D4 session、D5 CSRF、D6 密碼與節流、D7 授權）與 [FastAPI 版後端](../adr/2026-11-05-fastapi-backend-stack.md)（D7 資料庫角色）。

路徑相對於 repo 根目錄；`auth/` 指 `backend/src/app/modules/auth/`，`web/` 指 `backend/src/app/web/`，測試在 `backend/tests/`（`*_db.py` 需要資料庫，只在 CI 跑）。還沒移植的機制那幾列仍指 Go 版 `template-go-nextjs@560106d` 的檔案（`httpapi/` 是它的 `backend/internal/httpapi/`，Go 的 `auth/*.go` 是它的 `backend/internal/modules/auth/`），由計畫步驟 7（`docs/plans/2026-11-03-fastapi-template.md`）改寫。

## 範圍

**資產**

| 資產 | 存放位置 |
|---|---|
| 帳號 | `users` 表：`account`（小寫、唯一）、`role`、`is_disabled`（`auth/schema.sql`） |
| 密碼雜湊 | `users.password_hash`，argon2id PHC 字串（`auth/service/passwords.py`） |
| Session token | cookie 裡的 32 個隨機 byte；資料庫只存它的 SHA-256（`sessions.token_hash`，`auth/service/__init__.py`） |
| 角色 | `users.role`，`admin` 或 `user`；admin 只能由 `app seed-admin` 建立（`backend/src/app/main/seed.py`） |
| 節流狀態 | `sign_in_throttles`（`auth/schema.sql`） |

**信任邊界**

1. 瀏覽器 → Vercel：頁面與 `/api/*` 同一個網域；`/api/*` 由 `frontend/vercel.json` 的 rewrite 轉給 backend 的公開位址，並設定 `X-Origin-Secret`（值在 Vercel 環境變數）。Vercel 覆寫 client 送的 `X-Forwarded-For`，`X-Real-IP` 是使用者位址（Vercel 文件）。
2. Vercel → Cloudflare Tunnel → backend：沒有服務發佈 port（`docker-compose.yml`，CI 檢查）。tunnel 的位址任何人都能連，所以 backend 只把帶相符 `X-Origin-Secret` 的 `/api` 請求當成來自網站，也只在這時採信 `X-Real-IP`；`MODE=prod` 下沒帶或帶錯都回 403，`/health` 只接受 loopback（`backend/src/app/web/origin.py`）。`X-Request-ID` 一律由 backend 產生（`backend/src/app/web/request_id.py`）。
3. Next server → backend：server 端呼叫直接打 `API_URL`，只轉送請求的 cookie，並帶 `X-Origin-Secret`（`frontend/src/lib/api/client.ts`）。它不帶 `X-Real-IP`，所以限流的位址是連線來源（正式環境是 cloudflared），會限流的 operation 不從 server 端呼叫。
4. backend → PostgreSQL：用 app 角色，只有 DML 權限（`deploy/postgres/initdb/10-roles.sh`、`backend/internal/platform/migrate/migrate.go`）。
5. backend → Have I Been Pwned range API：只在 `PASSWORD_HIBP_CHECK=true` 時（`backend/src/app/platform/hibp.py`；`serve`、`seed-admin`、`recover-admin` 都讀這個設定）。

**入口**（`backend/api/openapi.yaml` 的 operation；每個 route 用 `web/access.py` 的 `PUBLIC` 或 `SIGNED_IN` 標出誰能呼叫，`backend/src/app/main/routes.py` 在啟動時與契約比對）

| operationId | Route | 誰能呼叫 |
|---|---|---|
| `createUser` | `POST /api/users` | 匿名；每個 IP 限流 |
| `createSession` | `POST /api/sessions` | 匿名；每個 IP 限流，每個帳號節流 |
| `deleteCurrentSession` | `DELETE /api/sessions/current` | 匿名也可，讓過期的 cookie 也能登出 |
| `getCurrentUser`、`getUser` | `GET /api/users/me`、`GET /api/users/{id}` | 登入者；`getUser` 只能看自己，admin 例外 |
| `listSessions`、`getSession`、`deleteSession`、`deleteAllSessions`、`getCurrentSession` | `/api/sessions…` | 登入者，只限自己的 session |
| `deleteCurrentUser` | `DELETE /api/users/me` | 登入者；session 必須是 10 分鐘內登入的，否則 403 `reauthentication_required` |
| `listUsers`、`patchUser`、`deleteUser` | `GET /api/users`、`PATCH`／`DELETE /api/users/{id}` | 只有 admin |

不在範圍內：`items` 各 operation 擁有者範圍以外的部分、主機與 Docker 的強化、Caddy 的 TLS 設定。

## Spoofing（冒充）

| 威脅 | 程式碼裡的緩解 | 狀態 |
|---|---|---|
| 對單一帳號線上猜密碼 | 每個「帳號＋來源」（位址，IPv6 以 /64，與限流同一個 `web/ratelimit.py` `limit_key`；或信任的裝置）各自節流，在比對密碼之前計數，第 5 次失敗起等待時間加倍、上限 1 小時（`auth/repository.py` `reserve_login_attempt`、`auth/router.py` `_address_key`）。測試：`test_one_clients_failures_do_not_slow_another`、`test_ipv6_addresses_in_one_64_are_one_client`、`test_sign_in_is_throttled_per_account_name_whether_or_not_it_exists`、`test_attempts_sent_at_once_cannot_exceed_the_allowance`、`test_the_wait_doubles_from_the_fifth_failure_up_to_an_hour`（`test_auth_db.py`）。登入成功只清這個來源的計數與帳號合計，別的來源累積的等待不受影響，所以本人在別處登入不會替攻擊者的位址歸零（`auth/repository.py` `clear_login_throttle`；計畫 D10、ADR account-abuse-and-lifecycle D4）。測試：`test_signing_in_clears_only_that_clients_wait`、`test_successful_sign_ins_do_not_build_up_a_wait`（`test_auth_db.py`）、`test_signing_in_clears_only_that_clients_count`（`test_auth_repository.py`）。沒有該帳號裝置 cookie 的來源另外合計，連續失敗 100 次（NIST SP 800-63B 3.2.2）起全部拒絕，直到登入成功、admin 解鎖或 24 小時沒有嘗試（`auth/service/__init__.py` `UNTRUSTED_FAILURE_LIMIT`、`_count_untrusted_attempt`）。測試：`test_untrusted_clients_are_locked_but_a_trusted_device_is_not`、`test_a_device_cookie_is_per_account`（`test_auth_db.py`） | 已緩解 |
| 從同一位址對多個帳號噴灑密碼 | `createUser`、`createSession` 的 route 掛 `RATE_LIMITED`（`auth/router.py`）：每 IP token bucket，預設每分鐘 5 次，IPv6 以 /64 為鍵（`web/ratelimit.py`）。測試：`test_sign_up_and_sign_in_are_limited_per_address`（`test_auth_db.py`）、`test_ratelimit.py` | 單一副本內已緩解；見「已接受的風險」 |
| 弱密碼或外洩過的密碼 | 15 到 128 字元（契約）、NFKC 正規化、內建常見密碼清單（`auth/service/passwords.py`、`auth/service/__init__.py`）。測試：`test_create_user_password_too_short`、`test_create_user_common_password`、`test_passwords_are_compared_after_nfkc`（`test_auth_db.py`）、`test_common_password_list`（`test_passwords.py`）；可選的 HIBP 檢查：`test_create_user_breached_password`（`test_auth_db.py`）、`test_a_breached_password_is_refused_after_nfkc`、`test_a_failed_breach_check_lets_the_password_through`（`test_hibp.py`） | 已緩解。HIBP 預設關閉，查詢失敗時放行：接受，外部服務故障不該擋住註冊 |
| 猜 session token | `secrets.token_urlsafe(32)` 產生的 256-bit token（`auth/service/__init__.py`）。測試：`test_create_user_sets_the_session_cookie` | 已緩解 |
| Session fixation | 每次登入都發新 token，並刪掉請求帶來的舊 token。測試：`test_sign_in_issues_a_new_token_and_ends_the_one_the_request_carried` | 已緩解 |
| 登出、停用或過期後重用 token | 每個請求都重讀 session 與使用者；idle 預設 1 天、絕對期限 7 天；停用會刪掉該使用者所有 session；使用者可列出自己的 session、撤銷其中一個或全部登出（`auth/service/__init__.py` `revoke_session`、`sign_out_all`）。測試：`test_session_idle_expiry`、`test_session_absolute_expiry`、`test_sign_out_ends_only_that_session`、`test_me_for_a_disabled_or_deleted_account`、`test_delete_session`、`test_delete_all_sessions`、`test_list_sessions_excludes_expired`（`test_auth_db.py`）、`test_an_admin_disables_a_user_and_ends_their_sessions`（`test_auth_db.py`，`auth/service/__init__.py` `set_disabled`） | 已緩解 |
| 用 header 代替 cookie 送 token | 只讀 cookie（`auth/dependencies.py` `AuthenticateMiddleware`）；契約 security 的檢查看這個 middleware 驗過的 principal，不看 cookie 存不存在（`web/contract.py`）。測試：`test_only_the_cookie_authenticates`、`test_an_invalid_cookie_with_an_invalid_body_is_unauthenticated_not_invalid`（`test_auth_http.py`） | 已緩解 |
| Client 偽造位址規避每 IP 限流 | 只在請求帶相符的 `X-Origin-Secret` 時採信 `X-Real-IP`，其他一律用連線來源位址；`X-Forwarded-For` 從不採信（`backend/src/app/web/origin.py`）。測試：`test_rate_limit_ignores_x_real_ip_without_the_secret`、`test_rate_limit_per_ip_behind_a_verified_origin` | 來源密鑰沒外洩時已緩解；正式路徑上 `X-Real-IP` 是否真是使用者位址列在部署文件的第一次部署檢查清單 |
| 被停用的帳號登入 | 回應與密碼錯誤相同的 `invalid_credentials`。測試：`test_a_disabled_account_signs_in_like_a_wrong_password` | 已緩解 |

## Tampering（竄改）

| 威脅 | 程式碼裡的緩解 | 狀態 |
|---|---|---|
| 跨站請求偽造（CSRF） | `web/cross_origin.py` 包住整個 app：`Sec-Fetch-Site` 不是 `same-origin`／`none` 就 403；沒有它時 `Origin` 必須等於 `APP_ORIGIN`（D6：backend 收到的 `Host` 不是瀏覽器看到的網域）；cookie 是 `SameSite=Lax`（`auth/dependencies.py` `SessionCookie`）。測試：`test_cross_origin_post_rejected`、`test_same_origin_or_non_browser_post_allowed`（`test_cross_origin.py`） | 已緩解。`Sec-Fetch-Site` 與 `Origin` 都沒帶的請求會放行；瀏覽器在會改變狀態的請求上都會帶。兩個 header 經 Vercel 與 Cloudflare 能否原樣到達 backend 未驗證，列在部署文件的第一次部署檢查清單（計畫前提段） |
| 註冊時自己指定角色 | 請求 schema 拒絕未定義的欄位；service 一律寫入 `user`。測試：`test_validation.py` 的 `additional_property` 案例、`test_create_user_sets_the_session_cookie`（角色是 `user`） | 已緩解 |
| 同一帳號名稱同時註冊 | 由唯一索引決定，恰好一個成功，其餘 409。測試：`test_concurrent_sign_ups_for_one_account_get_it_once` | 已緩解 |
| Token 走明文 http | 除非 `COOKIE_SECURE=false`，cookie 帶 `Secure` 與 `__Host-` 前綴；`MODE=prod` 下設 `COOKIE_SECURE=false` 拒絕啟動（`backend/src/app/platform/config.py`）。測試：`test_cookie_name_follows_secure`、`test_serve_defaults_are_production`、`test_invalid_values_stop_startup`（`test_config.py`）。HSTS 原本由 Caddy 送，D6 之後的負責者在步驟 7 改寫 | cookie 已緩解；HSTS 待步驟 7 |
| 在日誌裡偽造 request id | backend 一律自己產生 UUIDv7，不採信傳入的 `X-Request-ID`；文字日誌的值含空白、引號或換行時加引號（`backend/src/app/main/logs.py`）。測試：`test_request_id_from_the_client_is_replaced`、`test_text_log_values_cannot_forge_a_line` | 已緩解 |
| SQL injection | 查詢是 SQLAlchemy Core 的運算式，值一律是 bound parameter（`auth/repository.py`）；字串拼接的 SQL 會被 ruff S608 擋（`backend/ruff.toml` 的 `select = ["ALL"]`）；router 不能 import sqlalchemy（`backend/.importlinter` `sql-below-the-router`） | 已緩解 |
| 登入後被導向外站 | `callbackUrl` 用 URL 解析後必須與本站同源，`/\evil.example`、`/\t/evil.example` 這類看起來像本站路徑的值也會被擋（`frontend/src/features/auth/callback.ts`）。測試：`callback.test.ts` | 已緩解 |

## Repudiation（否認）

| 威脅 | 程式碼裡的緩解 | 狀態 |
|---|---|---|
| 無法追蹤某個請求 | 每個請求一行 access log，帶 `request_id`、`method`、`path`、`status`、`duration_ms`、`client_ip`（`backend/src/app/web/access_log.py`）。測試：`test_error_log_and_access_log_carry_the_request_id` | 已緩解 |
| 使用者或 admin 否認做過某個操作 | admin 的停用、啟用、刪除、解鎖與主機上的 `recover-admin`，在同一個交易寫進 `audit_events`（執行者、動作、對象、request id）；app 角色對它只能新增與讀取，保留 365 天。註冊、登入成功與失敗、登出、被拒絕的 admin 操作寫安全事件日誌，帶 user id 與 request id；容器日誌有輪替。測試：`test_admin_changes_are_audited`（`test_auth_db.py`，每個會改狀態的 admin operation 都要有案例）、`test_recover_admin_gives_an_admin_a_new_password_and_a_clean_slate`（`test_seed_db.py`）、`TestAuditEventsAppendOnly`、`TestAppendOnlyTables`、`test_security_events_are_logged_with_the_user`（`test_auth_db.py`，含 `authz.denied`） | 已緩解。紀錄存在同一個資料庫，能拿到 owner 或 superuser 權限的人仍可修改；需要防竄改證明時改送外部儲存 |

## Information disclosure（資訊洩漏）

| 威脅 | 程式碼裡的緩解 | 狀態 |
|---|---|---|
| 資料庫外洩導致 session 被盜用 | 只存 token 的 SHA-256，schema 用 CHECK 限制 32 byte。測試：`TestSchemaChecks` | 已緩解 |
| 資料庫外洩導致密碼外洩 | argon2id m=19 MiB、t=2、p=1、16 byte salt，argon2-cffi 比對（`auth/service/passwords.py`）。測試：`test_hash_uses_owasp_argon2id_and_verifies` | 已緩解 |
| 密碼或 token 寫進日誌 | 註冊、登入成功與失敗、驗證錯誤、格式錯誤的 body、未知的 token、登出、HIBP 查詢失敗，全部跑一遍後檢查日誌（含 debug 級）。測試：`test_credentials_never_reach_the_log`（`test_auth_db.py`，HIBP 查詢失敗時跑） | 已緩解 |
| Script 讀到 token（XSS） | cookie 是 `HttpOnly`；頁面有 nonce 型 CSP，`connect-src 'self'`、`frame-ancestors 'none'`（`frontend/src/proxy.ts`）。測試：`test_create_user_sets_the_session_cookie`、`test_session_cookie_attributes`、前端 `proxy.test.ts` | build 出來的版本已緩解；`next dev` 不設 CSP |
| 登入時從回應或時間差列舉帳號 | 不存在的帳號也跑一次 dummy hash，回同樣的 problem；節流也套用在不存在的帳號名。dummy hash 在啟動時就算好，第一次也不多一次雜湊。測試：`test_an_unknown_account_costs_a_hash_and_answers_like_a_wrong_password`、`test_sign_in_is_throttled_per_account_name_whether_or_not_it_exists` | 已緩解 |
| 註冊時列舉帳號 | 帳號已存在回 `409 account_taken`。測試：`test_create_user_duplicate` | 接受：用帳號名而不是 email，無法隱藏名稱已被使用（ADR D6）；註冊有每 IP 限流 |
| 讀到別人的使用者資料或 session | 非擁有者一律 404，與不存在的一樣（`auth/service/__init__.py` `user`、`session`，`auth/repository.py` `get_user_session`）。測試：`test_another_users_account_is_not_found_like_a_missing_one`、`test_get_session`、`test_list_sessions`、`test_another_users_session_cannot_be_revoked`（`test_auth_db.py`，撤銷也只看本人的 session：`auth/repository.py` `Live`）；非 admin 列使用者回 403：`test_a_user_cannot_administer_users`（`test_auth_db.py`） | 已緩解 |
| 讀到或改到別人的 item | 每個讀、改、刪既有 item 的查詢都帶 `owner_id = 呼叫者`，非擁有者一律 404（`items/repository.py`）。測試：`test_queries_on_existing_items_are_scoped_to_the_owner`（`test_items_repository.py`，本機）、`test_another_users_item_is_not_found_like_a_missing_one`、`test_another_users_item_cannot_be_changed_or_deleted`（`test_items_db.py`） | 已緩解 |
| 內部錯誤洩漏細節 | 非預期錯誤變成不帶細節的 500，細節只進日誌（`httpapi/problem.go`）。測試：`TestUnhandledErrorHidden` | 已緩解 |
| 密碼送到 HIBP | 只送 SHA-1 的前 5 個 hex 字元，帶 `Add-Padding`（`backend/src/app/platform/hibp.py`）。測試：`test_only_the_prefix_leaves_and_a_listed_suffix_is_breached`（`test_hibp.py`） | 已緩解。查詢失敗記進日誌的錯誤只有例外類別、訊息或 HTTP 狀態，不含請求路徑（`test_an_unreachable_server_is_no_answer`） |
| API 契約與 Swagger UI 外露 | 只在 `MODE=dev` 提供，預設 `prod`。測試：`TestDocs` | 已緩解 |
| backend ↔ PostgreSQL 傳輸未加密 | 連線必須 `sslmode=verify-full`；只有設定 `DATABASE_PLAINTEXT=same-host` 才接受不加密，範本只在 compose 裡設定（同一台主機的內部網路，資料不出主機）。改接遠端資料庫卻沒加密時，backend 與 migrate 拒絕啟動（`internal/platform/config`）。測試：`TestDatabaseURLRequiresVerifiedTLS` | 已緩解。compose 內部不加密是接受的取捨：要防同一網路上被入侵的容器，或資料庫搬到另一台主機時，改用 TLS |

## Denial of service（阻斷服務）

| 威脅 | 程式碼裡的緩解 | 狀態 |
|---|---|---|
| 大量雜湊耗盡記憶體或 CPU | 雜湊在專用的執行緒池跑，同時最多 `os.process_cpu_count()` 個；等待中的請求被取消時，還沒開始的雜湊一起取消，已開始的跑完才釋放名額（`auth/service/passwords.py`）。測試：`test_concurrent_hashes_are_bounded`、`test_a_cancelled_waiter_does_not_free_a_running_slot` | 已緩解。uvicorn 在 client 斷線時不取消請求，所以排隊中的雜湊照樣會算：接受；出現雜湊排隊造成的延遲時，加排隊上限並回 503 |
| 過大或過慢的請求 | body 上限 1 MiB；header、讀、寫逾時（`httpapi/server.go`）。測試：`TestBodyTooLarge`、`TestSlowHeaderClosed` | 已緩解 |
| 故意登入失敗把受害者鎖在門外 | 節流以「帳號＋來源」為單位，攻擊者的失敗只讓自己的來源等待；帳號層級的鎖只擋沒有該帳號裝置 cookie 的來源，本人曾登入過的瀏覽器照常登入（OWASP「Slow Down Online Guessing Attacks with Device Cookies」）；admin 可查看與解鎖（`GET`／`DELETE /api/users/{id}/sign-in-lock`，寫入稽核）。全部登出、停用、救援時忘掉該帳號的裝置。測試：`test_untrusted_clients_are_locked_but_a_trusted_device_is_not`、`test_a_device_cookie_is_per_account`、`test_signing_out_everywhere_and_disabling_forget_devices`、`test_an_admin_sees_and_lifts_the_lock`（`test_auth_db.py`）；救援時忘掉裝置在步驟 5c 移植 | 已緩解。本人在新裝置上仍可能被鎖，要等 admin 解鎖；加了 email 之後改成鎖定期間可用重設密碼解鎖（ADR api-and-auth D6 的重新評估條件） |
| 用亂數帳號名塞滿 `sign_in_throttles` | 不存在的帳號名也會建立紀錄（每次嘗試最多兩列：來源一列、帳號合計一列）；每 IP 限流限制速率，閒置 24 小時的紀錄每小時清掉（`app.main.factory` 的 lifespan 背景 task 呼叫 `auth/service/__init__.py` `cleanup`）。測試：`test_cleanup_deletes_expired_sessions_and_stale_throttles`（`test_auth_db.py`）、`test_serve_cleans_up_on_a_schedule_until_shutdown`（`test_auth_http.py`） | 單一副本內已緩解 |
| Admin 停用最後一個 admin，包括自己 | 停用時先鎖住所有啟用中的 admin，目標是唯一一個就回 409 `last_admin`（`auth/service/__init__.py` `_keep_an_admin`）；兩個 admin 同時互相停用只會成功一個。所有 admin 都登不進來時，`make recover-admin` 在主機上替既有的 admin 設新密碼、啟用並清掉節流。`recover-admin`（`backend/src/app/main/recover.py`、`auth/service/__init__.py` `recover_admin`）也刪掉它的 sessions 與信任裝置，寫 `admin.recovered`（執行者為空，代表主機上的操作者），只改既有的 admin、不建立也不升級帳號。測試：`test_the_only_enabled_admin_cannot_be_disabled`、`test_two_admins_disabling_each_other_at_once_leave_one`（`test_auth_db.py`）、`test_recover_admin_gives_an_admin_a_new_password_and_a_clean_slate`、`test_recover_admin_changes_only_an_existing_admin`（`test_seed_db.py`）、`test_recovery_changes_only_an_admin`（`test_auth_repository.py`） | 已緩解 |
| 其他 operation 不限次數 | 每 IP 限流只涵蓋 `CreateUser`、`CreateSession`；其他 operation 都要登入 | 範本接受；衍生專案依需要加進清單（`docs/rules/api-design.md` 限流） |

## Elevation of privilege（權限提升）

| 威脅 | 程式碼裡的緩解 | 狀態 |
|---|---|---|
| 一般使用者呼叫 admin 操作 | 每個 `/api` route 剛好一個存取標記（`web/access.py`）；缺標記、多標記、「公開」與契約的 `security: []` 不一致、route 與契約的 operation 對不上（含路徑參數名稱），server 啟動就失敗（`backend/src/app/main/routes.py`）。契約 security 在驗證 body 之前就拒絕沒登入的請求。測試：`test_routes.py`、`test_every_operation_needs_a_session_unless_the_contract_says_public`（`test_auth_http.py`，逐一呼叫契約裡每個 operation，沒登入要 401）。admin 操作用 `ADMIN` 標記（`web/access.py` `admin`），角色取自這次請求從資料庫讀到的值，非 admin 回 403 `insufficient_role` 並記 `authz.denied`。測試：`test_a_user_who_is_not_an_admin_is_refused_and_it_is_logged`（`test_auth_http.py`）、`test_a_user_cannot_administer_users`（`test_auth_db.py`） | 已緩解 |
| 角色變更後仍用舊角色 | 每個請求都從資料庫讀角色。目前沒有改角色或改密碼的 operation；後端規則 8 要求這類功能撤銷該使用者所有 session（`backend/AGENTS.md`） | 已緩解 |
| 匿名請求到達受保護的 handler | 契約宣告 security，validator 在參數與 body 之前檢查。認證 middleware 在契約驗證之前設定 principal。測試：`test_an_invalid_cookie_with_an_invalid_body_is_unauthenticated_not_invalid`、`test_every_operation_needs_a_session_unless_the_contract_says_public` | 已緩解 |
| backend 被入侵後改 schema | app 角色只有 DML。測試：`TestAppRoleCannotDDL` | 已緩解；所有表的資料仍可寫入，見「已接受的風險」 |
| 偷到或忘了登出的 session 被拿來刪除帳號 | 刪除自己的帳號要求 session 在 10 分鐘內登入過（`auth/service/__init__.py` `REAUTHENTICATE_WITHIN`），前端先用密碼重新登入再刪除；admin 刪除別人（`DELETE /api/users/{id}`）也一樣要求 10 分鐘內登入過；唯一啟用中的 admin 不能刪自己（409 `last_admin`，先鎖住所有啟用中的 admin）；刪除寫入稽核 `user.deleted`。測試：`test_delete_current_user`、`test_a_session_issued_ten_minutes_ago_may_still_delete`、`test_the_only_enabled_admin_cannot_delete_itself`、`test_an_admin_deletes_a_user`（`test_auth_db.py`）、`delete-account.test.tsx` | 已緩解 |
| 透過 API 建立 admin | 註冊一律寫 `user`；`ensure_admin` 只在 `seed-admin` 呼叫（`backend/src/app/main/seed.py`），既有帳號原封不動；密碼從 stdin 進容器（`make seed`），錯誤訊息只寫變數名與規則。測試：`test_seed_admin_creates_an_admin_who_can_sign_in`、`test_seed_admin_leaves_an_existing_account_unchanged`（`test_seed_db.py`）、`test_the_input_follows_create_user_request_and_is_never_echoed`（`test_seed.py`） | 已緩解 |

## 已接受的風險

| 風險 | 為什麼接受 | 何時重新評估 |
|---|---|---|
| 每 IP 限流的狀態在程式記憶體裡，每個 backend 副本各有一份額度（`web/ratelimit.py`） | 範本只跑一個 backend 副本 | 跑超過一個 backend 副本，或改用 Kamal、Swarm 部署：把限流移到資料庫或 proxy（ADR 基準的 Revocation Triggers） |
| 所有模組共用一個 app 角色與 `public` schema，app 角色能寫每張表（`migrate.go`） | 單一程序的模組化單體；分層由程式碼檢查（`backend/AGENTS.md` 規則 2、`internal/archtest`） | 某個模組拆成獨立服務，或需要自己的資料庫帳密 |
| 外洩密碼檢查查詢失敗時放行，且預設關閉 | 外部服務故障不該擋住註冊；內建清單一律檢查 | 產品要求外洩檢查是硬性規則 |
| 註冊會透露帳號名稱已被使用 | 用帳號名而不是 email，沒有 email 服務（ADR D6） | 加入 email 驗證或密碼找回 |
| 沒有改密碼、重設或找回密碼的 operation | 沒有 email 服務（ADR D6） | 同上；新 operation 必須撤銷 session（後端規則 8） |

## 何時更新這份文件

- `backend/api/openapi.yaml` 新增與認證有關的 operation，或 route 的存取標記（`web/access.py`）、掛 `RATE_LIMITED` 的 route 有增減。
- 新增角色，或改變授予角色的方式。
- Session 期限、cookie 名稱或屬性、token 格式改變。
- 新增外部身分提供者、MFA、Bearer token 或任何非瀏覽器的 client。
- 來源密鑰的機制、backend 前面的 proxy（Vercel、Cloudflare Tunnel）、backend 副本數改變。
- 上面某個 Open 項目處理完：改狀態並寫出測試。
