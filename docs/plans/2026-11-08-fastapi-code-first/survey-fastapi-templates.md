# FastAPI 範本與大型應用的設計選擇調查

調查日期：2026-11-09。所有 repo 以 `git clone --depth 1` 取得預設分支 HEAD，只讀、只 grep，沒有執行任何專案程式碼。
標記：**[讀]** = 直接讀原始碼得到；**[推]** = 由原始碼加上 FastAPI／Starlette 行為推論，沒有實際跑過。

## 調查對象

| Repo | Stars | 最後 push | 狀態 | 本次 SHA |
|---|---|---|---|---|
| fastapi/full-stack-fastapi-template（官方） | 46,199 | 2026-11-03 | 活躍 | `aabb901` |
| s3rius/FastAPI-template（cookiecutter） | 2,836 | 2026-05-05 | 活躍 | `f7ed808` |
| benavlabs/FastAPI-boilerplate | 2,108 | 2026-11-04 | 活躍（已大改成 `infrastructure/interfaces/modules/wiring` 結構，不是舊的 `app/core`） | `2d0ca1a` |
| teamhide/fastapi-boilerplate | 1,499 | 2026-05-08 | 活躍度低 | `df4e6d4` |
| zhanymkanov/fastapi-best-practices（指南，非程式碼） | 18,299 | 2026-08-27 | 活躍 | `5e00aa6` |
| nsidnev/fastapi-realworld-example-app | 3,098 | 2022-08-21 | **已 archived**，Pydantic v1 時代，參考價值有限 | `029eb77` |
| polarsource/polar（`server/`） | 10,467 | 2026-11-08 | 活躍 | `8198449` |
| Netflix/dispatch | 6,482 | 2025-09-03 | **已 archived** | `dd2837e` |
| open-webui/open-webui（`backend/`）— 第三個大型應用 | 158,104 | 2026-11-08 | 活躍 | `b7a005e` |
| （參照）fastapi/fastapi 0.162.2 | — | — | 用來確認請求處理順序 | `8f9f47d` |

第三個大型應用的選法：候選 open-webui（158k★）、apache/airflow（47k★，但 FastAPI 只是 Airflow 3 的 API server 一部分）、PrefectHQ/prefect（24k★）、mealie（13.8k★），全部在 2026-11-08 都有 push。選 open-webui，因為 star 數最高、近期活躍、而且整個 backend 都是 FastAPI。
指令：`gh api repos/<o>/<r> --jq '[.stargazers_count,.pushed_at,.archived,.default_branch]|@tsv'`。

## 總表（repo × 問題 1–7）

| Repo | 1 分層與命名 | 2 API 契約 | 3 驗證 vs 認證順序 | 4 錯誤格式 | 5 Rate limit | 6 回應契約測試 | 7 413 |
|---|---|---|---|---|---|---|---|
| 官方 full-stack | 按層：`app/core`、`app/api/{deps,routes}`、扁平 `models.py`/`crud.py`、`app/alembic`；`main.py` 模組層級 `app`（無 factory） | code-first；匯出 openapi.json（gitignore 不進 repo）→ `@hey-api/openapi-ts` 產 client（進 repo）；CI 重產並在有差異時失敗；無 breaking 檢查；docs 永遠開 | dependency（`OAuth2PasswordBearer`）；無 415/NaN/merge-patch | `{"detail"}` 預設；無自訂 422 | 無 | 無；靠 `response_model` + 一般 pytest | 無 |
| s3rius | 按層：`<pkg>/web/{api,gql}`、`<pkg>/db/{models,dao,migrations}`、`services/`；factory `web/application.py:get_app` | code-first；無匯出、無前端；docs 永遠開 | dependency（fastapi-users） | 預設 | 無 | 無 | 無 |
| benavlabs | 按領域：`src/modules/<feature>/{routes,schemas,service,crud,models}`；共用叫 `infrastructure/`（config、database、auth、ratelimit、`http_exceptions.py`、`app_factory.py`）；`interfaces/` 組 router；`backend/migrations/` | code-first；無匯出、無前端；正式環境預設關 docs | dependency（crudauth）；知道 FastAPI 會解析 `+json` 並配合；無 415 | `{"detail": 固定訊息, "support_id"}`；422 把整個錯誤細節（含 `input`）都拿掉 | 自家 crudauth，router 層 dependency | 無 | app 無；部署範本 nginx `client_max_body_size 25M` |
| teamhide | 按領域＋六角形：`app/<domain>/{adapter,application,domain}`；共用 `core/`（在 `app/` 外），HTTP 部分叫 `core/fastapi/{middlewares,dependencies}`；`migrations/` 在根目錄 | code-first；正式環境關 `/docs`、`/redoc` 但**沒關 `/openapi.json`** | Starlette `AuthenticationMiddleware` 只標記身分，拒絕交給 `PermissionDependency` | `{"error_code","message"}`（自家例外）；422 仍是預設 | 無 | 無 | 無 |
| best-practices（指南） | 按領域：`src/<domain>/{router,schemas,models,service,dependencies,...}`，全域 `config.py`/`database.py`/`main.py`；`alembic/` 在根目錄 | 建議非公開 API 用 `openapi_url=None` 只在指定環境開 | 建議用 dependency 做驗證（例如查資料存在） | 未談 | 未談 | 未談 | 未談 |
| realworld（archived） | 按層：`app/{api,core,db,models,services,resources}`，migrations 在 `app/db/migrations`；factory `get_application()` | code-first；docs 開 | dependency（`Security(APIKeyHeader)`），401 情況回 403 | `{"errors": [...]}`（RealWorld 規格），自訂 422 | 無 | Postman/newman 跑 RealWorld 規格集（行為，不是 schema 驗證） | 無 |
| polar | 按領域：`polar/<domain>/{endpoints,service,repository,schemas,auth}`；**模型集中** `polar/models/`；共用叫 `polar/kit/`（db、http、routing、pagination…，有 import fastapi）；`middlewares.py`、`exception_handlers.py` 在頂層；factory `app.py:create_app`；`server/migrations/`、`server/scripts/` | code-first；每個 API 版本匯出 openapi.json **進 repo**（`docs/openapi/`）；前端 `openapi-typescript` + `openapi-fetch`；CI 重產 client 有差異就失敗；breaking 靠**日期版本化**，非 oasdiff；版本化的 `/{version}/openapi.json` 正式環境公開（公開 API） | 混合：middleware 解析身分（壞 token 直接回錯），dependency（`Authenticator`）要求登入／scope | `{"error": 類名, "detail": ...}`，自訂 422 但保留 `input` | `asgi-ratelimit`，**middleware**（全部請求都算） | 無 | **app 內**：ASGI middleware 看 `Content-Length`，超過回 413，POST/PUT/PATCH 沒帶回 411 |
| dispatch（archived） | 按領域：`dispatch/<domain>/{models,service,views,...}`；共用 `database/`、`models.py`、`config.py`；`cli.py`（click）；migrations 在 `dispatch/database/revisions` | code-first；redoc 與 openapi 永遠開；前端 Vue 手寫 API | router 層 `dependencies=[Depends(get_current_user)]` | 預設 `{"detail"}` + `ExceptionMiddleware` 把服務層 `ValidationError` 轉 422 | slowapi，**decorator**，只有一個路由 | schemathesis 測試存在但**整段註解掉** | 無 |
| open-webui | 按層：`routers/`、`models/`、`utils/`、`internal/db.py`、`migrations/`、`main.py`（模組層級 `app`） | code-first；只有 `ENV=dev` 開 docs 與 openapi；前端 Svelte 手寫 fetch | dependency（`HTTPBearer(auto_error=False)` + `get_verified_user`） | 預設 `{"detail"}` | 自家 `RateLimiter`（Redis），在 handler 內呼叫，只用在登入等少數路由 | 無 | 只在上傳路由，讀完內容後檢查 → 413 |

---

## 問題 1：分層與命名

**[讀]** 九個對象裡，按領域分包（feature/domain package）的有 benavlabs、teamhide、best-practices 指南、polar、dispatch；按層分包的有官方、s3rius、realworld、open-webui。大型應用中，polar 和 dispatch 按領域，open-webui 按層。

| Repo | 「core」類（設定、DB、框架無關 helper） | 「http」類（router 共用 HTTP helper） | 功能模組 | app factory | CLI | migrations |
|---|---|---|---|---|---|---|
| 官方 | `app/core/{config,db,security}.py` | `app/api/deps.py` | 無，按層 `app/api/routes/*.py` + 扁平 `models.py`/`crud.py` | `app/main.py` 模組層級 `app`，非 factory | 無（`initial_data.py`、`backend/scripts/`） | `app/alembic/` |
| s3rius | `<pkg>/settings.py`、`<pkg>/db/` | `<pkg>/web/`（`lifespan.py`、`api/router.py`） | 無，`web/api/<名稱>/{views,schema}.py` 加 `db/{dao,models}` | `web/application.py:get_app`（uvicorn `factory=True`） | `__main__.py` | `<pkg>/db/migrations` |
| benavlabs | `src/infrastructure/`（config、database、cache、logging…） | 也在 `infrastructure/`（`http_exceptions.py`、`middleware.py`、`ratelimit/`） | `src/modules/<feature>/` | `infrastructure/app_factory.py:create_application`，在 `interfaces/main.py` 呼叫 | `backend/scripts/`、另有 `cli/` 套件 | `backend/migrations/`（package 外） |
| teamhide | `core/`（在 `app/` 之外：config、db、helpers、repository） | `core/fastapi/{middlewares,dependencies}` | `app/<domain>/` | `app/server.py:create_app` | 無 | 根目錄 `migrations/` |
| best-practices | `src/config.py`、`src/database.py`、`src/models.py` | 無專門層 | `src/<domain>/` | `src/main.py` | 未談 | 根目錄 `alembic/` |
| realworld | `app/core/`（settings、events） | `app/api/{dependencies,errors}` | 無，按層 | `app/main.py:get_application` | 無 | `app/db/migrations` |
| polar | `polar/kit/`（db、jwt、pagination、http、routing…，**有 import fastapi**）、`polar/config.py`、`polar/postgres.py` | 頂層 `polar/middlewares.py`、`polar/exception_handlers.py`、`polar/routing.py` + `polar/kit/routing.py` | `polar/<domain>/{endpoints,service,repository,schemas,auth}.py`；ORM 模型集中在 `polar/models/` | `polar/app.py:create_app` | `server/scripts/`（88 支，81 支用 typer）；`polar/cli/` 是給 Polar CLI 工具用的 API 模組，不是指令套件 | `server/migrations/`（package 外） |
| dispatch | `dispatch/database/core.py`、`dispatch/config.py`、`dispatch/models.py` | 無專門層，`main.py` 內有 middleware | `dispatch/<domain>/{models,service,views}.py` | `dispatch/main.py` 模組層級，三個 FastAPI 實例（app/frontend/api） | `dispatch/cli.py`（`[project.scripts] dispatch = "dispatch.cli:entrypoint"`） | `dispatch/database/revisions/` |
| open-webui | `config.py`、`env.py`、`internal/db.py`、`utils/` | `utils/`（`auth.py`、`rate_limit.py` 混在一起） | 無，按層 `routers/`、`models/` | `main.py` 模組層級 `app` | 無 | `open_webui/migrations/` |

命名觀察：
- **[讀]** 「core」這個名字：官方、realworld、teamhide 用 `core`；polar 用 `kit`；benavlabs 用 `infrastructure`；其餘直接放頂層檔案。**沒有任何一家**把 core 類限制成「不 import fastapi」：官方 `app/core/` 沒有 fastapi 但 `security.py` 等是 HTTP 無關的；polar `kit/routing.py` 直接繼承 FastAPI `APIRoute`。
- **[讀]** 「http」類（router 共用 HTTP helper）沒有一個叫 `http`：官方叫 `api/deps.py`，realworld 叫 `api/dependencies` + `api/errors`，teamhide 叫 `core/fastapi/`，polar 放頂層 `middlewares.py`/`exception_handlers.py`/`routing.py`，benavlabs 混在 `infrastructure`。
- **[讀]** 功能模組內檔名：best-practices 與 benavlabs 用 `router.py`/`routes.py`；polar 用 `endpoints.py`；dispatch 用 `views.py`。`service.py` 在所有按領域的專案都有；`repository.py` 只有 polar 和 teamhide（benavlabs 叫 `crud.py`，s3rius 叫 `dao`）。
- **[讀]** migrations 放在 app package 內的：官方（`app/alembic`）、s3rius、realworld、dispatch、open-webui；放 package 外的：benavlabs、teamhide、polar、best-practices。

證據：
- 官方 `main.py` 模組層級 app：https://github.com/fastapi/full-stack-fastapi-template/blob/aabb90126964a164e02aff39e7c385bb0598236d/backend/app/main.py#L21-L33
- 官方 `api/main.py` 組 router：https://github.com/fastapi/full-stack-fastapi-template/blob/aabb90126964a164e02aff39e7c385bb0598236d/backend/app/api/main.py#L1-L14
- s3rius factory：https://github.com/s3rius/FastAPI-template/blob/f7ed808be8d239b5a8511b146c46ee0ec7e31e06/fastapi_template/template/%7B%7Bcookiecutter.project_name%7D%7D/%7B%7Bcookiecutter.project_name%7D%7D/web/application.py#L45-L91
- s3rius 把 `db_*` 換成 `db`：https://github.com/s3rius/FastAPI-template/blob/f7ed808be8d239b5a8511b146c46ee0ec7e31e06/fastapi_template/template/%7B%7Bcookiecutter.project_name%7D%7D/replaceable_files.toml#L1-L10
- benavlabs factory 呼叫處：https://github.com/benavlabs/FastAPI-boilerplate/blob/2d0ca1a79a09f6b4ec2c23520d6d63496536489a/backend/src/interfaces/main.py#L36-L40
- teamhide factory 與 `core/fastapi` middleware：https://github.com/teamhide/fastapi-boilerplate/blob/df4e6d4f817ec187eeaf2d1c5a877b27c06bb77b/app/server.py#L1-L92
- best-practices 結構建議：https://github.com/zhanymkanov/fastapi-best-practices/blob/5e00aa6095521f0d00e4eec2ef0afa44cd566af4/README.md#L43-L119
- realworld factory：https://github.com/nsidnev/fastapi-realworld-example-app/blob/029eb7781c60d5f563ee8990a0cbfb79b244538c/app/main.py#L13-L45
- polar factory：https://github.com/polarsource/polar/blob/81984496a4b840c911280e90abbd290ca85082d1/server/polar/app.py#L209-L273
- polar 自訂 APIRoute 組合：https://github.com/polarsource/polar/blob/81984496a4b840c911280e90abbd290ca85082d1/server/polar/routing.py#L16-L31
- polar 模組 import 自己的 service：https://github.com/polarsource/polar/blob/81984496a4b840c911280e90abbd290ca85082d1/server/polar/product/endpoints.py#L35-L39
- dispatch CLI entry：https://github.com/Netflix/dispatch/blob/dd2837e82a0bf5565b1b4b4b91ea30b7262d4061/pyproject.toml#L121-L122
- open-webui app：https://github.com/open-webui/open-webui/blob/b7a005e5a5c0c0d869f2aebf4b8f051c5fb37245/backend/open_webui/main.py#L556-L562

## 問題 2：API 契約

**[讀]** 九個全部是 code-first（由 FastAPI 產生 schema），沒有一個是手寫 OpenAPI。

| Repo | 匯出 spec | spec 進 repo | 前端產生器 | CI 檢查過期 | breaking 檢查 | 正式環境 docs |
|---|---|---|---|---|---|---|
| 官方 | `scripts/generate-client.sh` 呼叫 `app.openapi()` | 否（`frontend/.gitignore` 第 14 行忽略），產出的 client 進 repo | `@hey-api/openapi-ts` 0.97.3（axios client） | 有：CI 跑 prek（pre-commit），hook 重產 client，有改動就失敗並自動 commit | 無 | 開（`/api/v1/openapi.json` 與 `/docs` 不分環境） |
| polar | `server/scripts/generate_openapi.py` | 是，`docs/openapi/<版本>.openapi.json` | `openapi-typescript` + `openapi-fetch`（`clients/packages/client/src/v1.ts`） | 有：`openapi-generate-check.yml` 重產 client，有差異就 `exit 1` | 無 oasdiff；用**日期式 API 版本化**（`polar/kit/versioning.py`） | 主 app `openapi_url=None`，但每個版本有公開 `/{version}/openapi.json`（Polar 是公開 API） |
| benavlabs | 無 | 否 | 無前端 | 無 | 無 | 正式環境預設關，可用 guard 開 |
| teamhide | 無 | 否 | 無前端 | 無 | 無 | 關 `/docs`、`/redoc`，**`/openapi.json` 仍開**（`openapi_url` 未設 None）[讀] |
| s3rius | 無 | 否 | 無前端 | 無 | 無 | 開 |
| realworld | 無 | 否 | 無前端 | 無 | 無 | 開（可由設定改） |
| dispatch | 無 | 否 | 手寫 axios（`src/dispatch/static/dispatch/src/*/api.js`） | 無 | 無 | 開（`/api/v1/docs` redoc） |
| open-webui | 無 | 否 | 手寫 fetch（`src/lib/apis/*`） | 無 | 無 | 只有 `ENV == 'dev'` 開 |
| best-practices | — | — | — | — | — | 建議非公開 API 預設關，只在指定環境開 |

breaking-change 工具的否定證據：
`rg -i -l -e 'oasdiff|openapi-diff|openapi_diff' <repo>`（排除 lockfile、node_modules）在九個 repo 全部 0 個檔案。

證據：
- 官方產 client 腳本：https://github.com/fastapi/full-stack-fastapi-template/blob/aabb90126964a164e02aff39e7c385bb0598236d/scripts/generate-client.sh#L1-L11
- 官方 openapi-ts 設定：https://github.com/fastapi/full-stack-fastapi-template/blob/aabb90126964a164e02aff39e7c385bb0598236d/frontend/openapi-ts.config.ts#L1-L21
- 官方 spec 被 gitignore：https://github.com/fastapi/full-stack-fastapi-template/blob/aabb90126964a164e02aff39e7c385bb0598236d/frontend/.gitignore#L14
- 官方 pre-commit hook：https://github.com/fastapi/full-stack-fastapi-template/blob/aabb90126964a164e02aff39e7c385bb0598236d/.pre-commit-config.yaml#L62-L67
- 官方 CI 跑 prek 並在失敗時 exit 1：https://github.com/fastapi/full-stack-fastapi-template/blob/aabb90126964a164e02aff39e7c385bb0598236d/.github/workflows/pre-commit.yml#L60-L97
- polar client 產生指令：https://github.com/polarsource/polar/blob/81984496a4b840c911280e90abbd290ca85082d1/clients/packages/client/package.json#L16
- polar CI 重產檢查：https://github.com/polarsource/polar/blob/81984496a4b840c911280e90abbd290ca85082d1/.github/workflows/openapi-generate-check.yml#L104-L106 與 https://github.com/polarsource/polar/blob/81984496a4b840c911280e90abbd290ca85082d1/.github/workflows/openapi-generate-check.yml#L210-L214
- polar spec 進 repo 的說明：https://github.com/polarsource/polar/blob/81984496a4b840c911280e90abbd290ca85082d1/AGENTS.md#L188-L193
- polar 版本化 openapi 路由：https://github.com/polarsource/polar/blob/81984496a4b840c911280e90abbd290ca85082d1/server/polar/kit/versioning.py#L583-L590
- polar 主 app 關 openapi：https://github.com/polarsource/polar/blob/81984496a4b840c911280e90abbd290ca85082d1/server/polar/app.py#L210
- benavlabs 正式環境關 docs：https://github.com/benavlabs/FastAPI-boilerplate/blob/2d0ca1a79a09f6b4ec2c23520d6d63496536489a/backend/src/infrastructure/app_factory.py#L185-L199
- teamhide 只關 docs/redoc：https://github.com/teamhide/fastapi-boilerplate/blob/df4e6d4f817ec187eeaf2d1c5a877b27c06bb77b/app/server.py#L76-L85
- dispatch docs 設定：https://github.com/Netflix/dispatch/blob/dd2837e82a0bf5565b1b4b4b91ea30b7262d4061/src/dispatch/main.py#L75-L83
- open-webui 只在 dev 開：https://github.com/open-webui/open-webui/blob/b7a005e5a5c0c0d869f2aebf4b8f051c5fb37245/backend/open_webui/main.py#L556-L562
- best-practices 建議：https://github.com/zhanymkanov/fastapi-best-practices/blob/5e00aa6095521f0d00e4eec2ef0afa44cd566af4/README.md#L628-L643

## 問題 3：認證與驗證的順序

### 先修正題目裡的一個前提（FastAPI 本身的行為）

題目假設「認證放 dependency → FastAPI 先驗 body → 匿名＋壞 body 得 422 而不是 401」。讀 FastAPI 0.162.2 原始碼後，**這只對一半** [推，讀原始碼，未實測]：

1. `get_request_handler` 先讀 body 並做 JSON 解碼；**JSON 語法錯誤**在這一步就丟 `RequestValidationError`（`json_invalid`），任何 dependency 都還沒跑 → 匿名＋壞 JSON = **422**。
   https://github.com/fastapi/fastapi/blob/8f9f47de69982705d96e4165a9f03c6537e8c9fe/fastapi/routing.py#L450-L492
2. 接著 `solve_dependencies`：**先依序執行子 dependency**（認證就在這裡），子 dependency 丟 `HTTPException(401)` 會直接往外拋；path/query/header/cookie/body 的**欄位驗證錯誤是在這之後才收集**，最後才丟 422。
   https://github.com/fastapi/fastapi/blob/8f9f47de69982705d96e4165a9f03c6537e8c9fe/fastapi/dependencies/utils.py#L676-L762
   https://github.com/fastapi/fastapi/blob/8f9f47de69982705d96e4165a9f03c6537e8c9fe/fastapi/routing.py#L511-L522 與 https://github.com/fastapi/fastapi/blob/8f9f47de69982705d96e4165a9f03c6537e8c9fe/fastapi/routing.py#L791-L798
3. 所以：匿名＋**JSON 合法但不符 schema** = **401**（認證 dependency 先丟）。
4. route／router 層的 `dependencies=[...]` 會被插到最前面（`insert(0, ...)`），一樣先於欄位驗證。
   https://github.com/fastapi/fastapi/blob/8f9f47de69982705d96e4165a9f03c6537e8c9fe/fastapi/routing.py#L915-L919
5. 415：FastAPI 預設 `strict_content_type=True`，content-type 不是 `application/json` 或 `application/*+json` 時，body 不當 JSON 解析，保留為 bytes，之後欄位驗證失敗 → **422，不是 415**。`application/merge-patch+json` 會被當 JSON 解析（`subtype.endswith("+json")`）。
   https://github.com/fastapi/fastapi/blob/8f9f47de69982705d96e4165a9f03c6537e8c9fe/fastapi/routing.py#L457-L475

建議用一個最小實驗確認第 1、3 點（兩個請求：壞 JSON、合法 JSON 缺欄位，都不帶 token），本次沒跑，因為規則不允許安裝套件。

### 各 repo 的作法

| Repo | 認證機制 | 匿名 401 從哪來 | 415 / NaN / merge-patch |
|---|---|---|---|
| 官方 | dependency：`OAuth2PasswordBearer` + `get_current_user`；壞 token 回 **403** 不是 401 | `OAuth2PasswordBearer(auto_error=True)` | 都沒有 |
| s3rius | dependency：fastapi-users `current_user(active=True)` | fastapi-users | 都沒有 |
| benavlabs | dependency：crudauth `auth.current_user()` | crudauth | 無 415；有一段程式照 FastAPI 的規則判斷 `application/*+json`（含 merge-patch）是否會被解析成 JSON，用途是擋無法存進 DB 的字元，**不是**採用 merge-patch 當 PATCH 格式 |
| teamhide | Starlette `AuthenticationMiddleware` 解析 token、不拒絕；`PermissionDependency([IsAdmin])` 拒絕 | dependency | 無 |
| realworld | dependency：`Security(RWAPIKeyHeader)`；缺 token 沿用 `APIKeyHeader` 的狀態碼（當時的 FastAPI 是 403），token 格式錯回 **403** | dependency | 無 |
| polar | 混合：`AuthSubjectMiddleware` 在 middleware 解析身分，`OAuth2Error`（壞 token）直接在 middleware 回應；`Authenticator` dependency 對匿名丟 `Unauthorized` | 壞 token：middleware（先於驗證）；沒 token：dependency | 無 415、無 merge-patch；NaN 只在個別 Decimal 欄位 `allow_inf_nan=False` |
| dispatch | router 層 `dependencies=[Depends(get_current_user)]`，靠 middleware 先放好 `request.state.db` | dependency | 無 |
| open-webui | dependency：`HTTPBearer(auto_error=False)` + `get_current_user`/`get_verified_user` | dependency | 無 415、無 merge-patch；少數欄位 `allow_inf_nan=False` |
| best-practices | 建議用 dependency 做驗證與權限 | — | 未談 |

否定證據（415、merge-patch）：
`rg -i -l -e 'HTTP_415|status_code=415|unsupported.media' <repo>` → 九個 repo 全 0。
`rg -i -l -e 'merge-patch' <repo>` → 只有 benavlabs 有（5 個檔，都是上述 `+json` 判斷與其測試、文件）。
`rg -i -l -e 'allow_inf_nan'` → 只有 polar（3）、open-webui（3），都是逐欄位設定，沒有全域嚴格 JSON。

證據：
- 官方：https://github.com/fastapi/full-stack-fastapi-template/blob/aabb90126964a164e02aff39e7c385bb0598236d/backend/app/api/deps.py#L16-L49
- s3rius：https://github.com/s3rius/FastAPI-template/blob/f7ed808be8d239b5a8511b146c46ee0ec7e31e06/fastapi_template/template/%7B%7Bcookiecutter.project_name%7D%7D/%7B%7Bcookiecutter.project_name%7D%7D/db_sa/models/users.py#L97-L99
- benavlabs dependency：https://github.com/benavlabs/FastAPI-boilerplate/blob/2d0ca1a79a09f6b4ec2c23520d6d63496536489a/backend/src/infrastructure/auth/dependencies.py#L25
- benavlabs `+json` 判斷：https://github.com/benavlabs/FastAPI-boilerplate/blob/2d0ca1a79a09f6b4ec2c23520d6d63496536489a/backend/src/infrastructure/auth/deps.py#L156-L174
- teamhide middleware：https://github.com/teamhide/fastapi-boilerplate/blob/df4e6d4f817ec187eeaf2d1c5a877b27c06bb77b/core/fastapi/middlewares/authentication.py#L16-L50
- teamhide permission dependency：https://github.com/teamhide/fastapi-boilerplate/blob/df4e6d4f817ec187eeaf2d1c5a877b27c06bb77b/core/fastapi/dependencies/permission.py#L57-L67
- realworld：https://github.com/nsidnev/fastapi-realworld-example-app/blob/029eb7781c60d5f563ee8990a0cbfb79b244538c/app/api/dependencies/authentication.py#L21-L60
- polar middleware：https://github.com/polarsource/polar/blob/81984496a4b840c911280e90abbd290ca85082d1/server/polar/auth/middlewares.py#L248-L278
- polar Authenticator：https://github.com/polarsource/polar/blob/81984496a4b840c911280e90abbd290ca85082d1/server/polar/auth/dependencies.py#L147-L170
- polar 使用處：https://github.com/polarsource/polar/blob/81984496a4b840c911280e90abbd290ca85082d1/server/polar/product/auth.py#L9-L18
- polar NaN：https://github.com/polarsource/polar/blob/81984496a4b840c911280e90abbd290ca85082d1/server/polar/product/tiers.py#L34
- dispatch：https://github.com/Netflix/dispatch/blob/dd2837e82a0bf5565b1b4b4b91ea30b7262d4061/src/dispatch/api.py#L262-L269
- open-webui：https://github.com/open-webui/open-webui/blob/b7a005e5a5c0c0d869f2aebf4b8f051c5fb37245/backend/open_webui/utils/auth.py#L169 與 https://github.com/open-webui/open-webui/blob/b7a005e5a5c0c0d869f2aebf4b8f051c5fb37245/backend/open_webui/utils/auth.py#L573-L579

## 問題 4：錯誤格式

**[讀]** 沒有任何一家用 RFC 7807/9457 problem details。否定證據：`rg -i -l -e 'problem\+json|rfc ?7807|rfc ?9457' <repo>` 九個 repo 全 0。

| Repo | 格式 | 自訂 `RequestValidationError` handler | 剝掉 Pydantic `input` |
|---|---|---|---|
| 官方 | `{"detail": ...}` 預設 | 無 | 否（FastAPI 預設 handler 回 `exc.errors()`，含 `input`）|
| s3rius | 預設 | 無 | 否 |
| benavlabs | `{"detail": 固定訊息, "support_id"}` | 有 | **是，而且更徹底**：回應只給固定訊息，連 `loc`/`msg` 都不回；log 只記 `loc`/`type`/`msg`，註解說明 `input` 會把密碼寫進 log |
| teamhide | 自家例外 `{"error_code","message"}`；422 用預設 | 無 | 否 |
| realworld | `{"errors": [...]}` | 有 | Pydantic v1 時代，v1 本來沒有 `input` |
| polar | `{"error": 類名, "detail": ...}` | 有 | **否**，`detail` 是 `exc.errors()` 全部（bytes 轉字串） |
| dispatch | `{"detail": ...}`；`ExceptionMiddleware` 把服務層 `ValidationError` 轉 422 | 無（只有 404 handler） | 否 |
| open-webui | `{"detail": ...}` 預設 | 無 | 否 |

FastAPI 預設 422 handler：https://github.com/fastapi/fastapi/blob/8f9f47de69982705d96e4165a9f03c6537e8c9fe/fastapi/exception_handlers.py#L20-L26

證據：
- benavlabs：https://github.com/benavlabs/FastAPI-boilerplate/blob/2d0ca1a79a09f6b4ec2c23520d6d63496536489a/backend/src/modules/common/utils/error_handler.py#L72-L97
- teamhide：https://github.com/teamhide/fastapi-boilerplate/blob/df4e6d4f817ec187eeaf2d1c5a877b27c06bb77b/app/server.py#L29-L36
- realworld：https://github.com/nsidnev/fastapi-realworld-example-app/blob/029eb7781c60d5f563ee8990a0cbfb79b244538c/app/api/errors/validation_error.py#L12-L19
- polar：https://github.com/polarsource/polar/blob/81984496a4b840c911280e90abbd290ca85082d1/server/polar/exception_handlers.py#L34-L62
- dispatch：https://github.com/Netflix/dispatch/blob/dd2837e82a0bf5565b1b4b4b91ea30b7262d4061/src/dispatch/main.py#L220-L253
- 官方、s3rius、open-webui 無自訂 422：`rg -n -i 'RequestValidationError|exception_handler' <repo>`，官方 0 筆、s3rius 只有 tortoise 的 `add_exception_handlers=True`、open-webui `main.py` 只有 `RecurrenceEvaluationTimeout` handler（https://github.com/open-webui/open-webui/blob/b7a005e5a5c0c0d869f2aebf4b8f051c5fb37245/backend/open_webui/main.py#L565-L567）。

## 問題 5：Rate limiting

| Repo | 函式庫 | 位置 | 被驗證擋下的請求算不算 |
|---|---|---|---|
| 官方、s3rius、teamhide、realworld | 無 | — | — |
| benavlabs | crudauth（同作者的套件）`auth.rate_limit` | **router 層 dependency**（`include_router(..., dependencies=[Depends(api_rate_limit_dependency)])`） | [推] 依 FastAPI 順序：JSON 語法錯誤（422）**不算**（dependency 還沒跑）；JSON 合法但不符 schema **算**（dependency 先跑） |
| polar | `asgi-ratelimit`（`ratelimit.RateLimitMiddleware`，Redis） | **ASGI middleware**，三層：IP、認證端點、一般（依 auth subject 分組） | [推] 全部都算，因為 middleware 在 routing 與驗證之前 |
| dispatch | slowapi | **decorator** `@limiter.limit("1000/minute")`，只用在一個路由 | [推] 不算：decorator 包住 endpoint 函式，FastAPI 只有在驗證通過時才呼叫 endpoint |
| open-webui | 自家 `RateLimiter`（Redis 滾動視窗，fallback 記憶體） | **handler 內**手動呼叫，用在登入、OAuth token exchange | [讀] 不算：呼叫點在 handler 裡，`form_data` 已解析完 |
| best-practices | 未談 | — | — |

否定證據：`rg -i -l -e 'slowapi|fastapi_limiter|fastapi-limiter|asgi-ratelimit|RateLimiter|rate_limit' <repo>` 官方、s3rius、teamhide、realworld、best-practices 皆 0。

證據：
- benavlabs dependency：https://github.com/benavlabs/FastAPI-boilerplate/blob/2d0ca1a79a09f6b4ec2c23520d6d63496536489a/backend/src/infrastructure/ratelimit/dependency.py#L52-L77
- benavlabs 掛到 router：https://github.com/benavlabs/FastAPI-boilerplate/blob/2d0ca1a79a09f6b4ec2c23520d6d63496536489a/backend/src/interfaces/api/v1/__init__.py#L9-L14
- polar 規則：https://github.com/polarsource/polar/blob/81984496a4b840c911280e90abbd290ca85082d1/server/polar/rate_limit.py#L1-L80
- polar 掛 middleware：https://github.com/polarsource/polar/blob/81984496a4b840c911280e90abbd290ca85082d1/server/polar/app.py#L218-L227
- dispatch：https://github.com/Netflix/dispatch/blob/dd2837e82a0bf5565b1b4b4b91ea30b7262d4061/src/dispatch/rate_limiter.py#L1-L5 與 https://github.com/Netflix/dispatch/blob/dd2837e82a0bf5565b1b4b4b91ea30b7262d4061/src/dispatch/signal/views.py#L70-L71
- open-webui：https://github.com/open-webui/open-webui/blob/b7a005e5a5c0c0d869f2aebf4b8f051c5fb37245/backend/open_webui/routers/auths.py#L99 與 https://github.com/open-webui/open-webui/blob/b7a005e5a5c0c0d869f2aebf4b8f051c5fb37245/backend/open_webui/routers/auths.py#L764-L768

## 問題 6：回應的契約測試

**[讀]** 沒有任何一家在現行測試裡用 spec 驗證回應；全部靠 `response_model` 加一般斷言。
- dispatch 有一個 schemathesis 測試（`from_asgi` + `response_schema_conformance`），**整檔被註解掉**：https://github.com/Netflix/dispatch/blob/dd2837e82a0bf5565b1b4b4b91ea30b7262d4061/tests/api/test_schema.py#L1-L40
- realworld 用 Postman/newman 跑 RealWorld 官方行為測試集，檢查欄位存在等行為，不是用 OpenAPI 驗證：https://github.com/nsidnev/fastapi-realworld-example-app/blob/029eb7781c60d5f563ee8990a0cbfb79b244538c/.github/workflows/conduit.yml#L60-L67

否定證據：`rg -i -l -e 'schemathesis|openapi_core|openapi-core|openapi_spec_validator' <repo>` → 只有 dispatch 2 個檔（被註解的測試、`pyproject.toml`），其餘 0。

## 問題 7：Body 大小上限（413）

| Repo | 作法 |
|---|---|
| polar | **app 內全域**：自寫 ASGI `MaxBodySizeMiddleware`，只看 `Content-Length` 標頭，超過 10 MiB 回 413；POST/PUT/PATCH 沒帶 `Content-Length` 回 411。[推] 不計算實際串流位元組，所以 chunked 請求會被 411 擋掉而不是邊讀邊算 |
| open-webui | **只在上傳路由**：整個檔案讀進來之後比對設定值，超過回 413 |
| benavlabs | app 內無；部署範本的 nginx 設 `client_max_body_size 25M`（交給 proxy） |
| 官方、s3rius、teamhide、realworld、dispatch | 都沒有（[推] 交給 proxy 或不處理） |

否定證據：`rg -i -l -e 'HTTP_413|status_code=413|MaxBodySize|max_body_size|LengthRequired' <repo>` → 官方、s3rius、teamhide、realworld、dispatch、best-practices 皆 0；benavlabs 1（nginx 範本）；polar 3；open-webui 6（上傳／retrieval 路由與 log 設定）。另 `rg -i 'maxRequestBodyBytes|client_max_body_size'` 在官方的 compose／Traefik 設定也是 0。

證據：
- polar middleware：https://github.com/polarsource/polar/blob/81984496a4b840c911280e90abbd290ca85082d1/server/polar/middlewares.py#L236-L273
- polar 上限值：https://github.com/polarsource/polar/blob/81984496a4b840c911280e90abbd290ca85082d1/server/polar/config.py#L539
- polar 掛載：https://github.com/polarsource/polar/blob/81984496a4b840c911280e90abbd290ca85082d1/server/polar/app.py#L234
- open-webui：https://github.com/open-webui/open-webui/blob/b7a005e5a5c0c0d869f2aebf4b8f051c5fb37245/backend/open_webui/routers/files.py#L411-L417
- benavlabs nginx：https://github.com/benavlabs/FastAPI-boilerplate/blob/2d0ca1a79a09f6b4ec2c23520d6d63496536489a/cli/src/cli/features/_builtins/deploy/templates/nginx/default.conf.j2#L12

---

## 與 owner 計畫的對照（只比對 dispatch prompt 裡寫到的計畫）

| 計畫項目 | 業界最接近的作法 | 一致／不同 |
|---|---|---|
| `app.core`（設定、DB session、框架無關 helper） | 官方 `app/core`、teamhide `core/`；polar 叫 `kit`、benavlabs 叫 `infrastructure` | 名稱與官方一致；「框架無關」這個限制沒有任何一家強制 |
| `app.http`（router 共用 HTTP helper） | 官方 `api/deps.py`、realworld `api/{dependencies,errors}`、teamhide `core/fastapi/`、polar 頂層檔案 | 沒有任何一家叫 `http`；最常見的是放在 `api/` 底下 |
| `app.modules.<feature>`（router/schemas/service/repository/models） | best-practices、benavlabs（`modules/`）、polar、dispatch | 方向一致；`modules` 這個名字 benavlabs 也用；polar 把 models 集中，其他按領域的都放在模組內 |
| `app/main.py` app factory | polar `app.py:create_app`、s3rius `web/application.py:get_app`、teamhide `server.py:create_app`、官方與 best-practices 用 `main.py` 但不是 factory | factory 常見，但檔名多不叫 `main.py` |
| `app/cli/` | dispatch `cli.py`、s3rius `__main__.py`、polar `server/scripts/` | 有 CLI 的都只有單檔或腳本目錄 |
| `app/migrations/` | 在 package 內：官方 `app/alembic`、s3rius、realworld、dispatch、open-webui；在外：polar、benavlabs、teamhide | 約一半一半 |
| code-first + 匯出 spec + 前端產型別 | 官方（hey-api）、polar（openapi-typescript） | 一致 |
| spec 過期檢查 | 官方、polar 都在 CI 重產並比對 | 一致 |
| oasdiff breaking 檢查 | 九家都沒有；polar 用版本化 | 計畫比業界多一步 |
| RFC 9457 problem details | 九家都沒有 | 計畫與業界不同 |
| 剝 `input` | 只有 benavlabs 做（而且剝得更多） | 計畫屬少數派 |
| app 內 413 | 只有 polar 全域做 | 計畫屬少數派，但有大型應用先例 |

## 共識與分歧

**共識（多數一致）**
1. 全部 code-first，沒有人手寫 OpenAPI。
2. 認證以 dependency 為主（9 家中 7 家純 dependency 或以 dependency 拒絕；teamhide、polar 用 middleware 解析身分，但「要求登入」仍在 dependency）。
3. 錯誤格式沿用 FastAPI 的 `detail` 或自訂鍵名；沒有人用 problem details。
4. 沒有人處理 415、全域嚴格 JSON（NaN）或 `merge-patch+json` 作為 PATCH 格式。
5. 沒有人做 spec 驅動的回應驗證，也沒有人做 oasdiff 類的 breaking 檢查。
6. 有前端並產生 client 的兩家（官方、polar）都在 CI 檢查產物是否過期。

**分歧**
1. 分層：按領域（polar、dispatch、benavlabs、teamhide、指南）vs 按層（官方、s3rius、open-webui、realworld）。大型應用也分兩派。
2. 正式環境 docs：官方、s3rius、dispatch 一直開；benavlabs、open-webui、best-practices 正式環境關；teamhide 關一半（漏了 `/openapi.json`）；polar 的公開 API 刻意開。
3. Rate limit 位置四種都有：middleware（polar）、router dependency（benavlabs）、decorator（dispatch）、handler 內（open-webui）。被驗證擋下的請求算不算，因位置而異。
4. 413：app 內全域（polar）、只在上傳路由（open-webui）、交給 proxy（benavlabs）、不處理（其餘）。
5. spec 進不進 repo：polar 進（每版本一份）；官方不進，只把產出的 client 進 repo。

## 仍不確定的地方

1. 問題 3 的 FastAPI 順序（壞 JSON→422 先於認證；合法 JSON 缺欄位→認證 401 先於 422）是讀 0.162.2 原始碼推論，**沒有實測**。兩個不帶 token 的請求就能確認。
2. 「rate limit 算不算被驗證擋下的請求」對 benavlabs、polar、dispatch 都是推論（依 FastAPI／Starlette middleware 與 dependency 順序），沒有讀 crudauth、asgi-ratelimit、slowapi 的原始碼，也沒有實測。
3. polar 的 413 只看 `Content-Length`：「chunked 請求會被 411 擋掉」是從程式碼推論，沒測 Vercel／proxy 是否會在前面補上 `Content-Length`。
4. 只看了預設分支 HEAD；s3rius 是 cookiecutter 範本，產生後的實際檔案會依選項不同，本次只讀範本原始檔。
5. 只 grep 了列出的關鍵字；若某家用了沒想到的名稱（例如自寫的 body 限制但沒用 413 常數或這些字），可能漏掉。
6. realworld 與 dispatch 已 archived，反映的是 2022／2025 年的作法。
