# 步驟 4a：手寫 `openapi.yaml` 與產生的 `openapi.json` 的差異

- 比對：`backend/bin/oasdiff-1.35.0 diff|changelog backend/api/openapi.yaml backend/api/openapi.json --prefix-base /api`（D4：新規格的 paths 含 `/api`）。
- 歸類：`contract_delta.py`（本目錄，一次性，4b 刪 yaml 時一起刪）把 `oasdiff diff -f json` 的每一個葉節點與 `oasdiff changelog --level INFO -f json` 的每一筆對到附件 `inventory.md` §2 的 F 列或 T 列（T1、T2 於本步補進 §2）；每條規則同時限定路徑與值（例如刪掉的 schema 只能是 `ItemTitle`、新增的 title 只能是從空變成有），changelog 每筆也限定它的對象；攤平後的 `ValidationProblem` 內容另外檢查（oasdiff 把它的 property 當成新增，不比內容）；比對前先把產生規格裡的 `anyOf [X, {type: null}]` 改寫回 3.0 的 X 加 `nullable: true`（`as_nullable`），否則 oasdiff 不比 anyOf 分支的內容。對不到就 exit 1。description 只差換行與結尾換行（YAML block scalar 對 Python 字串）算沒變，其餘 description 變動算對不到。
- 指令（repo root）：`python3 -I docs/plans/2026-11-08-fastapi-code-first/contract_delta.py backend/bin/oasdiff-1.35.0`，exit 0，`every difference is mapped`。

## 結果（2026-11-09，`refactor/code-first`）

```
diff leaves by destination:
    20  (description: line breaks only)
     1  F1 openapi 3.0.3 -> 3.1.0
     6  F13 F14 FastAPI copies the parameter's description into its schema
     9  F14 F21 F22 anyOf [type, null] compared as type + nullable: true (as_nullable)
     3  F14 the cursor query parameter becomes nullable (cursor: str | None)
    14  F15 the id path parameter moves from the path item to each operation
     1  F2 servers /api removed, paths carry /api (D4)
     1  F20 ItemTitle inlined
     2  F26 SignInLock.failures format int64 dropped
    40  F3 global security -> per operation, from the access markers
   176  F30 example -> examples (JSON Schema 2020-12)
   255  F32 ValidationProblem allOf [Problem, {errors}] -> one flat object (contents: check_flat_validation_problem)
   837  T1 Pydantic writes a title on every schema and property (documentation only)
    17  T2 reusable parameters, headers and responses inlined per operation (F8, F10-F15)
changelog entries by id:
     1  api-global-security-removed -> F3
     1  api-schema-removed -> F20
     1  api-version-not-bumped -> F3 F14 F15 F26 (oasdiff counts them as breaking; the version stays 0.1.0)
    10  new-request-path-parameter -> F15
     3  request-parameter-became-nullable -> F14
    16  response-body-all-of-removed -> F32
    16  response-body-type-specialized -> F32
    48  response-optional-property-added -> F32
     1  response-property-type-changed -> F26
    64  response-required-property-added -> F32
every difference is mapped
```

## 各類的說明

| 類 | 內容 | 去向 |
|---|---|---|
| F1 | 3.0.3 → 3.1.0 | 附件 F1：FastAPI 產生 3.1；openapi-typescript 吃 3.1 已在步驟 1 (c) 量過 |
| F2 | 刪 `servers: [/api]`，paths 帶 `/api` | D4 選 A |
| F3 | 全域 `security` 拿掉，改成 18 個非 public operation 各自宣告 `sessionCookie`；`createUser`、`createSession`、`deleteCurrentSession` 沒有 security | 附件 F3、B49：`SIGNED_IN`、`ADMIN` 內含 `Security(APIKeyCookie(..., auto_error=False))`，只產生文件；`test_openapi.py::test_security_follows_the_access_markers` |
| F13、F14 | `limit`、`cursor` 的 description 也寫進參數的 schema；`cursor` 變成 `anyOf [string, null]`（前端型別會是 `cursor?: string \| null`） | 附件 F14 的去向就是 `cursor: str \| None = None`；請求帶 `cursor=null` 字串時仍是不合法的 cursor（422），行為不變 |
| F15 | `{id}` 參數從 path item 層搬到每個 operation（oasdiff 報 10 個 `new-request-path-parameter`，名稱、位置、`format: uuid` 都相同） | 附件 F15 |
| F20 | `ItemTitle` 不再是具名 schema，長度限制展開在 `title` 欄位 | 附件 F20（刻意放棄名稱） |
| F21、F22 | `nullable: true` → `anyOf [type, null]`；必填與否不變；改寫回 3.0 寫法後與 yaml 完全相同（9 處，含 3 個 `cursor`） | 附件 F21、F22 |
| F26 | `SignInLock.failures` 的 `format: int64` 拿掉 | 附件 F26（刻意放棄） |
| F30 | `example` → `examples` 陣列（Problem.type、FieldError.pointer、FieldError.code） | 附件 F30 |
| F32 | `ValidationProblem` 從 `allOf [Problem, {errors}]` 變成一個平的 object（7 個 property，required `type`、`title`、`status`、`errors`）；16 個宣告 422 的 operation 各報一次 | 附件 F32 |
| T1 | Pydantic 在每個 schema 與 property 加 `title`（例如 `"title": "Next Cursor"`） | 附件 §2 T1：只是文件，不影響驗證或型別；不另寫後處理去掉（FastAPI 應用的常態）；待 Will 確認 |
| T2 | `components/parameters`、`components/headers`、`components/responses` 不再存在，內容逐 operation 內嵌；oasdiff 解開 `$ref` 後內容相同，所以 operation 層沒有對應差異 | 附件 §2 T2 |

## oasdiff 會不會漏報（對照組）

在 `openapi.json` 的複本上各改一處再和原檔比，確認上表「沒出現」的類別真的是相同而不是 oasdiff 看不到（2026-11-09）：

- 從 `Problem.code` 的 `x-extensible-enum` 刪掉 `last_admin` → 每個 problem 回應都報 `Extensions changed / Modified extension: x-extensible-enum`。
- 把 `createItem` 201 的 `Location` header description 改掉 → `Headers changed / Modified header: location`。
- 把 `createItem` 415 的 media type 從 `application/problem+json` 改成 `application/json` → `Modified media type ... Name changed`。

所以 x-extensible-enum 三份清單（12 個 type、8 個 code、12 個 FieldError code）、每個 operation 的 status 與 header（附件 §2b）、problem 的 media type 都和 yaml 相同。

## 歸類腳本本身會不會放過真的差異（mutation）

在 `openapi.json` 的複本上各做一種改動，以第二個參數交給 `contract_delta.py`，10 個全部 exit 1（2026-11-09）：`info.title` 改掉、`ValidationProblem.errors.items` 改成 `{"type": "string"}`、`Item.description` 不再可為 null、`CreateItemRequest.description` 的字串分支加 `maxLength: 5`、`ItemPage.next_cursor` 的字串分支加 `pattern`（前五個是 4a 第 1、2 輪審查找到第一版規則會放過的）、`User` 多一個必填欄位、`Item` 少了 `owner_id`、`Item.title` 的 `maxLength` 改成 300、`Problem.code` 的清單少一個值、`createItem` 少了 415。

對照組：yaml（第三個參數）與 json 同時在 `Item` 加一個必填、可為 null 的 `notes`，exit 0（過渡期兩邊一起改的正當變更不會被擋）。
