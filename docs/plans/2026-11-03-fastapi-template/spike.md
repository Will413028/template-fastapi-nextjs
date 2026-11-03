# 步驟 0 spike 結果（2026-11-03）

規格：`template-go-nextjs@560106d`。環境：Python 3.13.15（uv 0.12.23），fastapi 0.162.0、pydantic 2.14.2、sqlalchemy 2.1.6、openapi-core 0.23.1，`postgres:18.6`（與 Go 版 `dbtest.Image` 同 digest），Atlas community（Go 版 `backend/bin/atlas-community`）。程式在 `spike/`，重跑方式：`cd spike && uv sync`，以 Go repo 路徑為 `$G`。

## (a) code-first：FastAPI 產生的 OpenAPI 能不能讓前端產出相同型別

**結論（實測）：資料模型幾乎一樣，錯誤回應與契約細節都對不上，前端會要改。** 只拿 items 5 個操作測。

- 相同：`Item`、`ItemPage`、`CreateItemRequest` 的欄位、必填與 nullable；`updateItem` 的 `application/merge-patch+json`。
- 不同：
  1. `createItem` 的回應碼：Go 版有 `201 400 401 403 413 415 422 500`，FastAPI 只有 `201 422`，而且 422 是 FastAPI 自己的 `HTTPValidationError`，不是 RFC 9457 `Problem`。
  2. `ItemPatch.title` 變成 `string | null`；契約規定 title 不能是 null。
  3. 具名 schema `ItemTitle` 不見了，被攤平成 `string`。
  4. FastAPI 輸出 OpenAPI 3.1，Go 版是 3.0.3；`Problem.type` 的 `x-extensible-enum`（前端 `gen-api.mjs` 靠它產生錯誤 code 的型別）不會自動出現。
- 要補齊，每個 route 都得手寫 `responses=`，並自訂 schema 產生邏輯；等於把契約再寫一次（推論）。

```text
$ uv run python app_a.py > fastapi.json   # 再用 Go 版 frontend 的 openapi-typescript 7.13.0 分別產生 go.d.ts、fastapi.d.ts
== error responses createItem
go       201:400:401:403:413:415:422:500:
fastapi  201:422:
== ItemPatch
go       title?: components["schemas"]["ItemTitle"];
fastapi  title?: string | null;
```

## (b) spec-first：拿手寫的 `openapi.yaml` 驗證 FastAPI

**結論（實測）：可行。** openapi-core 0.23.1 的 Starlette middleware 直接讀 Go 版契約：合規的 request 通過；過長、空字串、多餘欄位都被擋下；response 少了 `owner_id` 也抓得到。

- 要另外處理（實測）：openapi-core 自己回的錯誤是 `{"errors": [...]}`、`application/json`，不是 `Problem`。這個回應又違反契約只允許的 `application/problem+json`，所以最後回成 415。要自訂錯誤處理，把它轉成 `Problem` 加上 `code`。
- 安裝要用 `openapi-core[starlette]`，SQLAlchemy async 要用 `sqlalchemy[asyncio]`（實測：少了會 import 失敗）。
- 推論：正式環境只驗 request，response 驗證放在測試裡（Go 版是 kin-openapi validator 驗 request），要在步驟 2 定案。

```text
$ uv run python -W ignore app_b.py $G/backend/api/openapi.yaml
ok              -> 201 {"id":"…","title":"a","description":null,"owner_id":"…",…}
title too long  -> 415 {"errors":[{"title":"Content for the following mimetype not found: application/json. Valid mimetypes: ['application/problem+json']",…
extra field     -> 415 （同上）
empty title     -> 415 （同上）
$ uv run python -W ignore app_b.py $G/backend/api/openapi.yaml --bad-response
ok              -> 400 {"errors":[{"title":"Value {'id': …} …   # 少了 owner_id，response 驗證抓到
```

## (c) Atlas 套用 Go 版 migration，SQLAlchemy 能不能對上

**結論（實測）：可以。** 6 個 migration 直接套上，SQLAlchemy 2.0 async reflect 得到 6 張表；手寫的 `Item` 模型和資料庫在欄位與 nullable 上一致。比對的 mutation 也做了：把 `description` 改成非 nullable，比對就回報差異。

```text
$ $G/backend/bin/atlas-community migrate apply --dir file://$G/backend/migrations --url postgres://…@localhost:55432/spike
  -- 6 migrations
  -- 17 sql statements
$ uv run python app_c.py
tables: ['audit_events', 'items', 'sessions', 'sign_in_throttles', 'trusted_devices', 'users']
  items.description: TEXT nullable=True default=False
  fks: ['users.id ondelete=CASCADE']
  checks: ['char_length(title) >= 1 AND char_length(title) <= 200']
model/db nullability diffs: [] db columns missing in model: []
# mutation：description 改成 Mapped[str]
model/db nullability diffs: [('description', False, True)] db columns missing in model: []
```

## 對 D1、D2 的影響

- D1（契約來源）：(a) 顯示 code-first 要大量手寫才對得上，(b) 顯示 spec-first 可行。建議維持 A：照搬 Go 版的 `openapi.yaml`，用 openapi-core 驗證。
- D2（migration 工具）：(c) 顯示 Atlas 加 SQLAlchemy 可行，模型和資料庫的比對可以寫成測試。建議維持 A：沿用 Atlas。
- 沒驗證的部分：Pydantic 模型要手寫，或從 `openapi.yaml` 產生（例如 datamodel-code-generator）；這屬於實作細節，留給步驟 2。
