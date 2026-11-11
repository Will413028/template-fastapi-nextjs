# API Design Rules

本專案的 API 契約是 code-first：`backend/api/openapi.json`（OpenAPI 3.1）由 route 與各模組的 `schemas.py` 產生，前端從它產生型別（見下方「契約是唯一來源」第一條）；HTTP 層在 `backend/src/app/modules/<m>/router.py`（共用的在 `backend/src/app/http/`），業務邏輯在同一模組的 service。決策來源見 [ADR api-and-auth-baseline](../adr/2026-10-18-api-and-auth-baseline.md)、[ADR FastAPI 版後端](../adr/2026-11-05-fastapi-backend-stack.md) 與 [code-first 契約的 ADR](../adr/2026-11-11-code-first-contract-and-request-checks.md)。

## 契約是唯一來源

- 新增或修改 API 改的是各模組的 `schemas.py` 與 route 宣告，再重新生成：後端 `make -C backend gen`（`backend/api/openapi.json`，**NEVER** 手改），前端 `pnpm gen:api`（`frontend/src/lib/api/schema.d.ts`）。CI 以 `make -C backend gen-check` 與 `pnpm gen:api:check` 擋下沒有重新生成的變更（ADR code-first-contract-and-request-checks D1）。
- 長度、格式、pattern、enum 只寫在 `schemas.py` 的 model（`Field(min_length=...)` 等），契約由它產生。後端由 route 的 model 與 `app.http.route.ApiRoute` 檢查每個 request（錯誤順序見「認證」），前端取用生成的型別。正式環境只驗 request；response 由測試對照產生的契約（`backend/tests/apitest.py` 的 `checked_client`；ADR code-first-contract-and-request-checks D3）。**NEVER** 在 handler、service 或 zod schema 再寫一次相同的限制（ADR api-and-auth-baseline D6）。
- 契約表達不了的規則（例如密碼在常見密碼清單內、帳號已被使用）才由 service 檢查，以 `AppError` 回報。
- request body 的 model 用 `app.http.schemas.REQUEST_CONFIG`（`extra="forbid"`、`strict=True`；啟動檢查會擋）：多送的欄位回 422 `additional_property`，型別不符（例如布林欄位送 `"true"`）回 422 `invalid_type`，不做轉換。pattern 只用明確的字元類（`[A-Za-z0-9]`，不用 `\d`、`\w`）：pydantic-core 的 regex 與 ECMA-262 在這些縮寫上不同。
- DB 的 CHECK 是第二道防線，數值必須和契約一致（見 `database-design.md`）。

```typescript
// 正確：前端只檢查必填，長度與格式交給後端的 field error（frontend/src/features/auth/validations.ts）
export const credentialsSchema = z.object({
  account: z.string().min(1, "required"),
  password: z.string().min(1, "required"),
});

// 錯誤：在前端重寫契約的限制，兩處遲早不一致
account: z.string().min(3).max(50).regex(/^[A-Za-z0-9_.-]+$/),
```

## URL 設計

### 路徑

- API 與前端同源，掛在 `/api` 底下（ADR api-and-auth-baseline D1）。每個模組的 router 用 `prefix="/api"`，契約的 `paths` 含 `/api` 前綴，沒有 `servers`（ADR code-first-contract-and-request-checks D1）；下面的路徑省略這個前綴。
- URL 是資源導向的**複數名詞 kebab-case**：`/users`、`/sessions`、`/items`、`/order-items`（ADR api-and-auth-baseline D2）。
- 資源 ID 用路徑參數 `{id}`，型別 `app.http.schemas.Id`（契約裡是 `string` + `format: uuid`，只收有連字號的寫法）。
- 「目前的」資源用固定名稱的子資源：`/users/me`、`/sessions/current`。
- 巢狀資源最多兩層：`/users/{id}/items`。
- **NEVER** 在 URL 用動詞：登入是 `POST /sessions`，不是 `POST /users/login`；登出是 `DELETE /sessions/current`。
- **NEVER** 用角色切分路徑（`/admin/users`）：用同一個資源搭配授權檢查。
- 改變資源狀態優先用 PATCH 該資源的欄位，例如停用帳號是 `PATCH /users/{id}` 帶 `is_disabled`。
- URL 不帶版本（ADR api-and-auth-baseline D2）。

### Method 與狀態碼

| 操作 | Method | URL | 成功回應 |
|------|--------|-----|---------|
| 建立 | `POST` | `/items` | `201` + `Location` + 資源本身 |
| 列表 | `GET` | `/items` | `200` + `{items, next_cursor}` |
| 取得 | `GET` | `/items/{id}` | `200` + 資源本身 |
| 部分更新 | `PATCH` | `/items/{id}` | `200` + 更新後的資源 |
| 刪除 | `DELETE` | `/items/{id}` | `204`，沒有 body |

- 建立回 201 加 `Location`，刪除回 204（RFC 9110；ADR api-and-auth-baseline D2）。
- `Location` 指向契約裡**有 GET** 的正式網址（`/api/items/{id}`），建立者用同一組 cookie GET 得到 200。不可指向別名（例如 `/users/me`）或只有寫入端的路徑（ADR api-and-auth-baseline D9）。
- 有 PATCH、PUT 或 DELETE 的 path 一定也有 GET。`test_every_writable_path_has_a_get`（`backend/tests/test_routes.py`）檢查契約；資料庫測試的 `AuthAPI.follow_location`（`backend/tests/authtest.py`）對每個 201 GET 它的 `Location`。

```python
# 正確：backend/src/app/modules/items/router.py（route 宣告 `status_code=201` 與 `headers(201, LOCATION)`）
response.headers["Location"] = f"/api/items/{item.id}"
return _item(item)
```

## Response 格式

不包外殼（ADR api-and-auth-baseline D2）：

- 單一資源直接回 JSON object：`{"id": "…", "title": "…", …}`。
- 集合回 `{<資源複數>: [...], next_cursor}`，例如 `{"items": [...], "next_cursor": "…"}`；最後一頁的 `next_cursor` 是 `null`。
- **NEVER** 回 `{data: …}`、`{detail: "deleted"}` 之類的外殼。
- ID 是 UUID 字串；時間是 RFC 3339 UTC（model 的欄位用 `AwareDatetime`）。
- 回應一律帶 `id`；有 `updated_at` 的資源同時回 `created_at` 與 `updated_at`。
- 敏感欄位（`password_hash`、session token 與其雜湊）**NEVER** 出現在任何 response schema。
- 可為 null 的欄位在 response model 宣告成沒有預設值的 `X | None`（契約裡是 required 加 `anyOf` 含 `null`），回應模型一律設值（`None` 也照送），不讓欄位消失。

## 分頁

- 集合一律用 keyset 分頁：query 參數 `limit`（1–100，預設 20）與 `cursor`，route 參數用 `app.http.schemas` 的 `Limit`、`Cursor`（ADR api-and-auth-baseline D2）。
- `cursor` 是 opaque 字串，由 `app.core.pagination.encode_cursor` 產生（最後一筆 id 的 base64url），client 原樣傳回，**NEVER** 自行組或解析。不合法的 cursor 回 422。
- 不提供總數（不跑 `COUNT(*)`）。排序以 UUIDv7 的 `id DESC`（等於建立時間新到舊），查詢多取一筆判斷是否有下一頁：`page_rows`、`trim_page`（`app.core.pagination`）。
- 需要頁碼的後台表格才另外用 offset 實作，且只限該 endpoint（ADR api-and-auth-baseline D2 Rationale）。
- 契約的 `default` 由 route 參數的預設值產生，list operation 一律寫 `limit: Limit = DEFAULT_LIMIT`，有測試（`test_the_default_limit_is_the_contracts`，`test_pagination.py`）。

## 部分更新（PATCH）

- PATCH 收 `application/merge-patch+json`（RFC 7396；ADR api-and-auth-baseline D2）：沒送的欄位不改，送 `null` 清成 NULL。
- 不可為 null 的欄位在 patch model 的型別不含 `None`，送 `null` 回 422 `null_not_allowed`。
- 可清空的欄位在 `schemas.py` 的 model 用 `pydantic.MISSING` 當預設值，區分「沒送」與「送 null」，並把「有沒有送」明確傳給 service；repository 只 SET 有送的欄位。

```python
# 正確：backend/src/app/modules/items/router.py，set_description 區分沒送與 null
patch = Patch(
    title=None if body.title is MISSING else body.title,
    set_description=body.description is not MISSING,
    description=None if body.description is MISSING else body.description,
)
```

```python
# 正確：backend/src/app/modules/items/repository.py，values 只含有送的欄位
update(Item).where(Item.id == item_id, Item.owner_id == owner_id).values(**values, updated_at=func.now())
```

## 錯誤（RFC 9457）

- 所有錯誤回 `application/problem+json`，欄位 `type`、`title`、`status`，選填 `detail`、`code`、`request_id`（ADR api-and-auth-baseline D3）。
- `type` 是固定的相對 URI `/problems/<slug>`，每種 problem 一個（`backend/src/app/http/problems.py` 的 `ProblemType`）。`request_id` 與 response header `X-Request-ID` 相同，一律由 backend 產生，不採信外來的值。`code` 是細分 `type` 的穩定機器碼，例如 `account_taken`、`insufficient_role`。
- 驗證錯誤是 422 `/problems/validation`，附 `errors: [{pointer, code, detail}]`；`pointer` 是 JSON Pointer（query 或 path 參數為 `/<name>`），`code` 是 `FieldError.code` 的值，例如 `too_short`、`password_too_common`。
- `Problem.type`、`Problem.code`、`FieldError.code` 的可用值在契約裡用 `x-extensible-enum` 列出：清單會增加，新增不算 breaking change，用戶端翻譯認得的值、其餘退回 `type` 的訊息。不要改回 `enum`（封閉清單，新增會被 `make api-breaking` 擋下）。後端由 `test_problem_values_are_in_the_contract` 與 `checked_client`（`backend/tests/apitest.py`，每個測試收到的 problem 都檢查）檢查送出的值都在清單內；前端由 `messages.test.ts` 在編譯期檢查 `frontend/messages/*.json` 的 `problems.types`、`problems.codes`、`problems.fields` 涵蓋每個值（ADR api-and-auth-baseline D3）。
- 新增 code 的順序：`backend/src/app/http/problems.py` 的清單（契約的 `x-extensible-enum` 由它產生）→ `make -C backend gen` 與 `pnpm gen:api` → 每個語系的 `problems.*` 翻譯 → 後端送出。
- 前端依 `code`、再依 `type` 選訊息（`errors.ts` 的 `problemMessage`、`fieldMessage`），**NEVER** 解析或顯示 `detail`。
- 改 slug 或 code 是 breaking change。

### Service 回報錯誤

- Service 只 raise `AppError`（`app.core.errors`：`AppError(kind, code, detail)`、欄位錯誤用 `AppError.invalid(pointer, code, detail)`），kind 有 `NOT_FOUND`、`CONFLICT`、`UNAUTHENTICATED`、`FORBIDDEN`、`VALIDATION`、`RATE_LIMITED`。
- 轉成 problem 只在 `app.http` 一處（`errors.py` 的 exception handler 與 `problems.py` 的 `KIND_PROBLEMS`）。其他 exception 一律是 500 `/problems/internal`（`RecoverMiddleware`），內容只寫進 log，**NEVER** 送給 client。
- `detail` 會送給 client：不得含 SQL、主機名稱、secret 或其他使用者的資料。

```python
# 正確：service 回報預期中的失敗
raise AppError(Kind.CONFLICT, CODE_ACCOUNT_TAKEN, "account is taken")

# 錯誤：service 決定 HTTP 狀態碼（service 也不能 import fastapi，import-linter 的 http-stays-at-the-edge 會擋）
raise HTTPException(status_code=409)
```

## 分層

- 每個模組的 route 在 `app/modules/<m>/router.py`（`APIRouter(prefix="/api")`，由 `app.main` 掛上）：解 request、呼叫 service、轉成模組 `schemas.py` 的契約模型，不寫業務邏輯，不 import sqlalchemy（`.importlinter` 的 `sql-below-the-router`）。契約的 operation 就是這些 route：`app/main.py` 在啟動時檢查每個 route（`check_routes`：在 `/api` 底下、是 `ApiRoute`、一個存取標記、body 用 `REQUEST_CONFIG`；`test_routes.py`）。
- service 不 import fastapi、starlette（`http-stays-at-the-edge`），也不 import 其他模組（`modules-independent`）；分層由 `backend/.importlinter` 檢查，見 `AGENTS.md`「Backend layers」。
- 目前使用者由 route 的依賴取得（`app.http.access` 的 `SignedIn`，或 auth 模組的 `Current`），再**顯式**當參數傳給 service；service 不讀 request 或 context。
- 模組之間共用、但不碰 HTTP 的 helper（分頁 cursor）放在 `app.core`（`pagination.py`）。

## 認證

- Session 是 opaque token，只放在 `HttpOnly; Secure; SameSite=Lax` 的 `__Host-session` cookie（不帶 Domain；`COOKIE_SECURE=false` 的本機 http 開發改名為 `session`），DB 只存 SHA-256（ADR api-and-auth-baseline D4）。
- 不接受 Bearer token，也不把 token 放進 response body 或 JS 讀得到的地方（ADR api-and-auth-baseline D4）。
- 整個 app 外層包 `app.http.cross_origin.CrossOriginMiddleware`，跨來源的狀態變更請求回 403 `cross_origin_request`：看 `Sec-Fetch-Site`，沒有時比對 `Origin` 與 `APP_ORIGIN`（ADR api-and-auth-baseline D5；比對 `APP_ORIGIN` 而不是 `Host` 是因為 ADR vercel-rewrite-and-cloudflare-tunnel D1 的 rewrite）。
- 每個 route 用 `app.http.access` 的 marker 標出誰能呼叫（`PUBLIC`、`SIGNED_IN`、`ADMIN`），契約的 `security` 由 marker 產生。`app.http.route.ApiRoute` 在 FastAPI 讀 body 之前依序檢查：access（401 未登入、403 角色不符）→ 限流（429，每個打到的請求都扣額度）→ Content-Type（415；PATCH 的 415 帶 `Accept-Patch`）→ 嚴格 JSON（400），之後才是參數與 body 的 422；在它之前是路由的 404/405，再之前是 body 上限 413（在路由之前讀 body，未定義的路徑也是）。整體順序 413 > 404/405 > 401/403 > 429 > 415 > 400 > 422（下表；ADR code-first-contract-and-request-checks D2）。「登入」指 `app.modules.auth.dependencies.AuthenticateMiddleware` 驗過 session，不是 cookie 存在。只有登入、註冊、登出這類操作用 `PUBLIC`。
- 登入一律發新 token。

| 順序 | Status | 檢查 | 由誰 |
|---|---|---|---|
| 1 | 413 | body 超過上限 | body 上限 middleware（路由之前） |
| 2 | 404、405 | 路徑或方法沒有 route | Starlette 的路由 |
| 3 | 401、403 | 未登入、角色不符 | `ApiRoute` 讀存取標記 |
| 4 | 429 | per-IP 限流（見「限流」） | `ApiRoute` 讀 `RATE_LIMITED` |
| 5 | 415 | Content-Type 不是宣告的 media type | `ApiRoute` |
| 6 | 400 | 不是嚴格的 JSON（語法、尾端多餘資料、NaN、非 UTF-8） | `ApiRoute` |
| 7 | 422 | 參數或 body 不合 model | Pydantic |

### Session 撤銷（新增功能必須遵守）

- 停用帳號時，在同一個 transaction 撤銷該使用者的所有 session（`auth/service` 的 `set_disabled` 呼叫 repository 的 `delete_user_sessions`）。
- **之後新增任何會改變使用者 role 或密碼的功能，必須在同一個 transaction 撤銷該使用者的所有 session**（ADR api-and-auth-baseline D4）。範本目前沒有這兩個功能，這條規則是給之後新增時遵守的，並且要有測試：改完後該使用者原有的 cookie 都回 401（寫法比照 `test_an_admin_disables_a_user_and_ends_their_sessions`，`test_auth_db.py`）。

## 授權

- role 只有 `admin` 和 `user`（`app.modules.auth.service` 的 `ROLE_ADMIN`、`ROLE_USER`），註冊預設 `user`，admin 只能用 `seed-admin` 建立（ADR api-and-auth-baseline D7）。
- 使用者擁有的資源，**每個查詢**都帶 owner 條件（`.where(Item.id == item_id, Item.owner_id == caller.id)`）；別人的資源回 404，不回 403，不透露它存在（`test_another_users_item_is_not_found_like_a_missing_one`，`test_items_db.py`）。
- UPDATE 與 DELETE 回報影響的列（`.returning(...)` 加 `one_or_none()`，或結果的 `rowcount`），0 列回 404，不可靜默成功。
- 每個 route 都要剛好帶一個 `app.http.access` 的存取標記，寫明誰能呼叫：`dependencies=[PUBLIC]`，或用 `SIGNED_IN`／`SignedIn` 參數、`ADMIN`／`Admin`；缺標記、多標記、route 不是 `ApiRoute`、或標記由 `include_router` 加上時 server 不會啟動（`app/main.py`、`test_routes.py`）；契約的 `security` 由標記產生（`test_openapi.py`）。admin 的 operation 在 route 執行前回 403 `insufficient_role`（`app/http/access.py` 的 `admin`）。
- 授權分兩處，依「判斷需要什麼」選一處：只看是否登入或 role 的寫在 route 的存取標記（`SIGNED_IN`、`ADMIN`）；要看呼叫者與資源的關係的（例如 `GET /api/users/{id}` 的「本人或 admin」）寫在 service 或查詢條件，非授權者回 404（`AuthService.user`、`test_another_users_account_is_not_found_like_a_missing_one`）。
- **NEVER** 先查出資源再在 handler 比對 owner：條件必須在 SQL 裡。

## 限流

- per-IP 限流以 route 套用：在 route 的 `dependencies` 加 `app.http.ratelimit.RATE_LIMITED`（目前是 `createUser`、`createSession`），次數由 `AUTH_RATE_LIMIT_PER_MIN` 設定。它是 `ApiRoute` 讀的 marker：在 access 檢查之後、內容檢查之前扣額度，所以被 415、400、422 擋下的請求也算（ADR code-first-contract-and-request-checks D4；Rails `rate_limit`、DRF throttle、ASP.NET rate limiter 都在解析前計數；猜密碼另由 `sign_in_throttles` 依帳號與來源節流）。
- 超限回 429 `/problems/rate-limited` 加 `Retry-After`（`Kind.RATE_LIMITED` 的 `retry_after`）。
- 登入另有依「帳號＋來源」的節流（`sign_in_throttles`，來源是 `limit_key` 的位址），不論帳號是否存在都記錄（ADR api-and-auth-baseline D6、ADR account-abuse-and-lifecycle D4），加上只擋陌生來源的帳號層級上限（`auth/service` 的 `UNTRUSTED_FAILURE_LIMIT`）。

## Query 參數

- 參數名用 **snake_case**：`created_after`、`sort_by`。
- 時間一律 RFC 3339 UTC：`2026-03-22T14:30:00Z`；**NEVER** 用 Unix timestamp。
- 分頁只用 `limit`、`cursor`。

## 契約穩定性

- 契約變更必須通過 `make -C backend api-breaking`（`make check` 的一部分）：oasdiff 拿 `backend/api/openapi.json` 與 `BASE`（本機預設 `origin/dev`，沒有 dev 的 clone 用 `origin/main`）的 merge-base 裡的版本比對，有 breaking change 就失敗；oasdiff 的 warning 也算（刪除 request 欄位只是 warning，但 request model 拒收多餘欄位，還在送的 client 會拿到 422）；CI 對 push 用該分支上一次的 tip、對 PR 用 base 分支，與 `migrations-immutable` 相同。
- 刻意的破壞性變更：在 `backend/api/breaking-ignore.txt` 照 oasdiff 印出的格式（method、path、訊息）加行，共用 schema 的變更每個用到它的 operation 各一行，上一行用註解寫理由；這個檔是 gate 檔，commit 要帶 `Quality-Gate:` trailer。變更進了 `main` 之後刪掉那幾行，刪除也要帶 trailer（一行包含某個變更的 method、path 與訊息時 oasdiff 就忽略它，留著會讓同樣的變更再次通過）。
- **可以**新增 operation、新增 response 欄位、新增選填的 request 欄位。
- **NEVER** 刪除、改名或改型別已合併的欄位；**NEVER** 把選填的 request 欄位改成必填。
- URL 不帶版本。出現原生 app 或第三方 API 消費者時，重新評估版本策略（ADR api-and-auth-baseline 的 Revocation Triggers）。

```python
# 正確：request model 新增選填欄位
class CreateItemRequest(BaseModel):
    title: Annotated[str, _TITLE]
    category: str | MISSING = MISSING

# 錯誤：把 title 改名為 name，前端與已部署的 client 都會壞
class CreateItemRequest(BaseModel):
    name: Annotated[str, _TITLE]
```

## 前端 API 呼叫

- 一律用 `frontend/src/lib/api/client.ts` 的 `api`（openapi-fetch，型別來自生成的 `paths`），以 `unwrap` 取資料或丟出 `ApiError`。
- 瀏覽器打相對路徑 `/api`；server-side 程式碼打 `API_URL`，只轉發自己需要的 header（不轉發 `X-Forwarded-For`）。
- API 呼叫放在 `features/<name>/api/`，搭配 TanStack Query hooks（`features/<name>/hooks/`）。**NEVER** 在 component 裡直接 `fetch()`。

```typescript
// 正確：frontend/src/features/auth/api/auth-api.ts
import { api } from "@/lib/api/client";
import { unwrap } from "@/lib/api/errors";

export const authApi = {
  register(credentials: Credentials): Promise<User> {
    return unwrap(api.POST("/api/users", { body: credentials }));
  },
};

// 錯誤：手寫 URL 與型別，繞過生成的契約
await fetch("/api/users", { method: "POST", body: JSON.stringify(credentials) });
```

## 禁止事項

- **NEVER** 手改 `backend/api/openapi.json` 或前端的 `schema.d.ts`；**NEVER** 改了 `schemas.py` 或 route 卻沒重新生成（`gen-check`、`gen:api:check` 會擋）
- **NEVER** 在 handler、service 或前端重寫契約已有的長度、格式、enum 限制
- **NEVER** 回應外殼（`{data}`、`{detail}`）或 offset + 總數的分頁（後台頁碼表格除外）
- **NEVER** 回傳指向沒有 GET 的路徑的 `Location`；**NEVER** 新增有寫入端卻沒有 GET 的 path
- **NEVER** 在 service 決定 HTTP 狀態碼或寫 response；service 只 raise `AppError`
- **NEVER** 把非 `AppError` 的錯誤內容送給 client；**NEVER** 讓前端解析 `detail`
- **NEVER** 送出契約清單沒有列的 `type` 或 `code`
- **NEVER** 在 URL 用動詞、用角色切路徑或帶版本
- **NEVER** 查詢使用者擁有的資源而不帶 owner 條件；**NEVER** 對別人的資源回 403
- **NEVER** 接受 Bearer token，或讓 JS 讀得到 session token
- **NEVER** 新增改 role 或改密碼的功能而不撤銷該使用者的所有 session
- **NEVER** 刪除、改名或改型別已合併的 API 欄位
- **NEVER** 使用 Unix timestamp，時間一律 RFC 3339 UTC
