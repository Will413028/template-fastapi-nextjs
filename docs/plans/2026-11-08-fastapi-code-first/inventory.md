# Spec-first → FastAPI code-first：行為與耦合盤點

> 決定的編號與選項字母以計畫 `../2026-11-08-fastapi-code-first.md` 的「決定」段為準；本檔各列的 (a)(b)(c) 是盤點當時的草案，不代表計畫的 A/B。

- Snapshot：`dev` @ `4dbe59f`（`git -C <repo> rev-parse --short HEAD`），2026-11-08。
- 範圍：舊實作就是規格。本文件列出每一個「外部看得到」的行為、契約特徵、引用點與測試，讓搬遷時沒有東西無聲消失。唯讀盤點，未改任何程式碼、未跑任何測試。
- 證據等級：**measured** = 讀過原始碼或跑過下列指令；**inferred** = 依讀到的 FastAPI 0.162.0 / Starlette 1.10.0 / Pydantic 2.14.1 原始碼推論、尚未以測試驗證；**unverified** = 依既有知識，未讀原始碼也未跑。表格中未標註者皆為 measured。
- 套件版本（measured，`ls backend/.venv/lib/python3.14/site-packages`）：fastapi 0.162.0、starlette 1.10.0、pydantic 2.14.1、openapi-core 0.23.1、jsonschema 4.26.0、datamodel-code-generator 0.86.0；frontend openapi-typescript 7.13.0、openapi-fetch 0.17.0。

使用的指令（repo root 為 cwd）：

```bash
git -C <repo> rev-parse --show-toplevel; git -C <repo> rev-parse --short HEAD
# 讀檔：validation.py contract.py routes.py docs.py errors.py problems.py access.py ratelimit.py factory.py server.py
#       items/router.py auth/router.py contract_models.py apitest.py testdata/test-openapi.yaml openapi.yaml(全檔)
#       test_validation.py test_routes.py test_contract_models.py test_problems.py test_middleware.py
#       frontend/scripts/gen-api.mjs frontend/src/lib/api/{client,errors}.ts messages.test.ts auth-api.ts server.ts
# FastAPI 行為依據：.venv/.../fastapi/routing.py:440-540（body 解析）、dependencies/utils.py:643-760（依賴求解順序）、
#                   openapi/utils.py:509-526（自動 422）
G='--hidden -g !.git -g !**/node_modules/** -g !**/.venv/** -g !**/__pycache__/** -g !docs/plans/** -g !**/coverage.xml -g !**/test-report.xml'
rg -c ${=G} -e '<pattern>'                         # 第 3 節每組
rg -c '^(async )?def test_' backend/tests/*.py      # 第 5 節
.venv/bin/python -I -c '<yaml.safe_load 列出每個 operation 的 security/params/media/codes/headers>'   # 第 2 節 2b
.venv/bin/python -I -c '<tomllib 讀 uv.lock 找 pyyaml/jsonschema 的依賴者>'                           # 第 5 節備註
```

FastAPI 原始碼讀到的三個關鍵事實（後面多處引用）：

- **K1**（routing.py:450-491）：body 在依賴求解**之前**讀取並解析。無 Content-Type 且 `strict_content_type`（預設 True）→ body 保留為 bytes；`application/json` 或任何 `application/*+json` → `request.json()`；`JSONDecodeError` → `RequestValidationError(type="json_invalid", loc=("body", pos))`；其他例外（含 `UnicodeDecodeError`、`ClientDisconnect`）→ `HTTPException(400, "There was an error parsing the body")`。FastAPI 沒有 415。
- **K2**（dependencies/utils.py:676-760）：先逐一求解並**呼叫**子依賴（access marker、`RATE_LIMITED`），之後才驗證 path/query/header/cookie，再驗證 body。所以 marker 的 401 仍先於 422，但 `RATE_LIMITED` 會在 422 之前扣 token。
- **K3**（openapi/utils.py:509-526）：有參數或 body 的 operation，若 responses 沒有 `422`、`4XX` 或 `default`，FastAPI 自動加 `422` → `HTTPValidationError` schema。

---

## 1. 行為盤點（validation.py、contract.py、routes.py、docs.py 與相依的 errors.py、problems.py、access.py）

目的地欄的用語：keep as-is / rewrite as `<middleware | exception handler | Pydantic constraint | dependency | custom route class | test helper | startup check>` / delete + 理由。新套件名照計畫：`app.http`（原 `app.web`）、`app.core`（原 `app.platform`）、`app/main.py` + `app/cli/` + `app/migrations/`（原 `app.main`）。

| # | 行為 | 來源 file:line | 釘住的測試 (file::test) | 目的地 |
|---|---|---|---|---|
| B1 | 只驗證 `scope.type == http` 且路徑以 `/api/` 開頭者；health 與 dev docs 路徑跳過 | validation.py:95-97 | test_validation.py::test_health_is_outside_validation、::test_docs_only_in_dev | delete：不再有全域契約驗證中介層。body 上限中介層（B2）保留同樣的 `/api/` 範圍判斷 |
| B2 | Content-Length 宣告 > `MAX_BODY_BYTES`（1 MiB）→ 413 `payload-too-large`，不讀 body | validation.py:54, 100-103, 115-117 | test_validation.py::test_body_over_the_limit | rewrite as middleware：`app.http` 純 ASGI `BodyLimitMiddleware`，沿用 `MAX_BODY_BYTES` 與 `_read_body`/`_replay` |
| B3 | 無 Content-Length（chunked）時邊讀邊算，超過即 413 | validation.py:118-130 | test_validation.py::test_body_over_the_limit_without_content_length | rewrite as middleware（同 B2） |
| B4 | 413 detail 為 `request body is over 1048576 bytes` | validation.py:101 | 無（只驗 type） | keep as-is（搬進 B2 中介層） |
| B5 | 413 先於 404/405/401/415/422：每個 `/api` request（含未定義路由、GET）都先讀完 body | validation.py:98-105 | 無 | rewrite as middleware：B2 中介層若先讀完 body 再交給 app，順序不變；若改成包裝 `receive` 延遲判斷，未定義路由會先 404。建議維持先讀（也是 B6 的前提） |
| B6 | client 中途斷線 → `ClientDisconnect` 往外丟 → RecoverMiddleware 回 500 `internal`（不是 4xx） | validation.py:112-114, 122-123；errors.py:56-62 | test_validation.py::test_body_read_failure_is_not_a_validation_error | rewrite as middleware：B2 中介層必須先讀 body 讓 `ClientDisconnect` 在中介層丟出。否則依 K1，FastAPI 會變成 `HTTPException(400)` → 400 problem（inferred） |
| B7 | 已讀的 body 重播給 operation，之後的 `receive` 轉回連線（disconnect） | validation.py:109, 133-144 | test_validation.py::test_valid_request_reaches_the_operation（echo body） | keep as-is（搬進 B2 中介層） |
| B8 | 契約沒有的 `/api` 路徑 → 404 `not-found` | validation.py:168-169 | test_validation.py::test_undefined_route；test_middleware.py::test_problem_carries_the_request_id[/api/nope] | rewrite as exception handler：改由 Starlette routing 丟 404 `HTTPException`，errors.py:76-79 已轉成 problem。注意 Starlette 預設 `redirect_slashes=True`：`/api/items/` 目前是 404，code-first 會變成 307 轉址（inferred）。目的地：`FastAPI(redirect_slashes=False)`（副作用：`/health/` 也從轉址變 404） |
| B9 | 路徑存在但方法不存在 → 405 `method-not-allowed`，**沒有** `Allow` header | validation.py:166-167 | test_validation.py::test_undefined_route（DELETE /api/widgets） | rewrite as exception handler：Starlette 405 → errors.py:79 保留 headers，所以 code-first 會**多帶** `Allow`（符合 RFC 9110，屬新增不屬遺失）。HEAD 對 GET 路由：FastAPI `APIRoute` 不自動加 HEAD（inferred），維持 405 |
| B10 | `/widgets`（不在 `/api` server 下）→ 404 | validation.py:95（跳過）+ Starlette routing | test_validation.py::test_undefined_route | keep as-is |
| B11 | 宣告 security 的 operation 沒有 principal → 401 `unauthenticated`，detail `this operation needs a signed-in session`，**無 `code`** | validation.py:170-171；contract.py:150-165 | test_validation.py::test_secured_operation_without_a_session_is_unauthenticated（斷言 detail）；test_auth_http.py::test_every_operation_needs_a_session_unless_the_contract_says_public | rewrite as dependency：`SIGNED_IN`/`ADMIN` marker（access.py:40-46）已會丟 `session_invalid()` → 401 帶 `code: session_invalid`、detail `no valid session`（principal.py:34-37）。**DECISION NEEDED (D3)**：接受 401 body 改成帶 `session_invalid` code，還是 marker 另丟一個無 code、保留原 detail 的錯誤？前端不受影響：client.ts:23-28 只看 status，`session_invalid` 已在 enum 與 messages 中 |
| B12 | security 錯誤單獨回傳、先於參數與 body 錯誤（匿名呼叫者看不到該送什麼） | validation.py:151-156 | test_auth_http.py::test_an_invalid_cookie_with_an_invalid_body_is_unauthenticated_not_invalid；test_validation.py::test_secured_operation_without_a_session_is_unauthenticated | rewrite as dependency：依 K2，marker 先於 schema 驗證，401 仍先於 422（inferred）。但依 K1，壞 JSON 與錯的 media type 在依賴之前處理，所以匿名送壞 JSON 會先拿到 400/422。順序怎麼保留見 D2（B22） |
| B13 | 滿足 security 的是 auth 中介層判定有效的 session，不是「有 cookie」；過期或偽造的 cookie 等於匿名 | contract.py:158-165；auth/dependencies.py:5-6 | test_auth_http.py::test_an_invalid_cookie_with_an_invalid_body_is_unauthenticated_not_invalid、::test_only_the_cookie_authenticates | keep as-is（marker 讀 `current_principal()`；AuthenticateMiddleware 仍在 routing 之前） |
| B14 | Content-Type 不在 operation 接受的清單內 → 415 `unsupported-media-type`，detail `accepted Content-Type: <排序後的清單>`，絕不回顯 request 的 header | validation.py:172-178 | test_validation.py::test_unsupported_media_type[text/x-canary]；test_items_http.py::test_a_patch_is_a_merge_patch | rewrite as custom route class：FastAPI 沒有 415（K1：非 JSON 的型別保留為 bytes → 422）。`app.http` 的 `APIRoute` 子類別在 `get_route_handler` 依該路由宣告的 media type 檢查。是否採用見 D2 |
| B15 | 有 body 的 operation 沒帶 Content-Type → 視為 `application/octet-stream` → 415 | contract.py:224-227 | test_validation.py::test_unsupported_media_type[None] | rewrite as custom route class（同 B14；FastAPI 預設會變 422） |
| B16 | PATCH 只接受 `application/merge-patch+json`；送 `application/json` → 415。POST 只接受 `application/json` | openapi.yaml:154, 501；contract.py:41 | test_items_http.py::test_a_patch_is_a_merge_patch | rewrite as custom route class：FastAPI 在每個 body 路由都接受 `application/json` 與任何 `+json`（routing.py:469-471）。路由用 `Body(media_type="application/merge-patch+json")` 宣告，route class 讀它來檢查 |
| B17 | media type 參數（`; charset=utf-8`）不影響比對 | openapi-core 比對行為 | test_validation.py::test_media_type_parameters_are_accepted | rewrite as custom route class（只比對 type/subtype） |
| B18 | `application/merge-patch+json` 的 body 以 JSON 解析 | contract.py:38-41, 182 | test_validation.py::test_merge_patch_body_is_parsed_as_json | delete 自訂 deserializer：FastAPI 原生把 `+json` 當 JSON（K1）。測試保留 |
| B19 | 語法錯、尾巴多字、多個 JSON 值 → 400 `bad-request`，detail `request body is not valid JSON` | validation.py:179-180；contract.py:49-52 | test_validation.py::test_malformed_json_is_a_bad_request（前 3 個參數） | rewrite as custom route class（嚴格解析）或 exception handler（`json_invalid` → 400）。目前 errors.py:84-98 會把 `json_invalid` 變成 422 `invalid_type`，pointer 是 `/<位移>`，錯 |
| B20 | `NaN`、`Infinity` 被拒為 400（RFC 8259 沒有這些值） | contract.py:44-52 | test_validation.py::test_malformed_json_is_a_bad_request[NaN/Infinity] | rewrite as custom route class：Starlette `Request.json()` 用 `json.loads`，會接受 NaN（inferred），所以必須自己用 `strict_json` 解析後交給 FastAPI，exception handler 做不到 |
| B21 | 非 UTF-8 body → 400，detail 同 B19 | contract.py:50-52 | test_validation.py::test_malformed_json_is_a_bad_request[non-UTF-8] | rewrite as custom route class：依 K1，FastAPI 會變 `HTTPException(400)`，而 errors.py:79 不帶 detail，detail 斷言會失敗 |
| B22 | 優先順序：404/405 > 401 > 415 > 400 > 422（「media type 或 JSON 錯誤優先於其他錯誤」） | validation.py:151-156, 165-181 | 隱含於 B12、B14、B19 的測試 | **DECISION NEEDED (D2)**：(a) custom route class 在 FastAPI 解析 body 前先查 marker（401）→ media type（415）→ 嚴格 JSON（400），完全保留順序（建議）；(b) 只用 exception handler，接受匿名送壞 JSON 時 400 先於 401，也沒有 415/NaN 拒絕；(c) 中介層自己重做路由比對 |
| B23 | 422 `validation` 列出**每一個**錯誤 `{pointer, code, detail}`，以 (pointer, code) 去重 | validation.py:147-162 | test_validation.py::test_validation_lists_every_error | rewrite as exception handler：`RequestValidationError` → `validation_response`，取代並擴充 errors.py:84-98 |
| B24 | 參數錯誤與 body 錯誤同一個 422，參數在前 | validation.py:157-161 | test_validation.py::test_parameter_and_body_errors_are_listed_together | rewrite as exception handler：FastAPI 先收 path/query/header/cookie 再收 body（K2），順序相同（inferred） |
| B25 | body 整個缺少（空 body）→ pointer `""`、code `required` | validation.py:185-186 | test_validation.py::test_validation_codes[POST "" → "" required] | rewrite as exception handler：FastAPI 空 body → `missing`，loc `("body",)` → `pointer(loc[1:]) == ""`（inferred） |
| B26 | 缺必填屬性 → 每個屬性各一筆 pointer `/name`、`required` | validation.py:227-234 | test_validation.py::test_validation_codes[{} → /name]；test_items_http.py::test_create_item_title_rules[/title required] | rewrite as exception handler（Pydantic `missing` → `required`） |
| B27 | 多餘屬性 → 每個未宣告屬性一筆、排序、`additional_property` | validation.py:235-241 | test_validation.py::test_validation_codes[/extra]、::test_validation_lists_every_error | rewrite as Pydantic constraint（`extra="forbid"`）+ exception handler（`extra_forbidden` → `additional_property`）。排序：Pydantic 依輸入順序（unverified），測試用 `sorted` 不受影響 |
| B28 | 不可為 null 的欄位送 null → `null_not_allowed`（nullable 與 type 兩筆去重） | validation.py:160-161, 242-243 | test_validation.py::test_validation_codes[/note]；test_items_http.py::test_a_patch_cannot_null_the_title；test_contract_models.py::test_null_is_rejected_where_the_contract_does_not_allow_it | rewrite as exception handler：Pydantic 型別錯誤且 `input is None` → `null_not_allowed` |
| B29 | `SCHEMA_CODES`：keyword → code（minLength/minItems→`too_short`，maxLength/maxItems→`too_long`，`pattern`，`enum`，`format`，min/max/exclusive*→`out_of_range`，type→`invalid_type`） | validation.py:56-74, 244 | test_validation.py::test_validation_codes（13 個 body 參數）；test_problems.py::test_problem_values_are_in_the_contract | rewrite as exception handler：改成 Pydantic error type → code 對照，例如 `string_too_short`/`too_short`→`too_short`、`string_too_long`/`too_long`→`too_long`、`string_pattern_mismatch`→`pattern`、`literal_error`/`enum`→`enum`、`uuid_parsing`/`uuid_type`/`datetime_*`→`format`、`greater_than*`/`less_than*`→`out_of_range`、`*_parsing`/`*_type`→`invalid_type`、`extra_forbidden`→`additional_property`、`missing`→`required`。對照表的值仍需落在 FieldError enum 內（B61） |
| B30 | 沒有對照的 keyword → `invalid`（例如 multipleOf） | validation.py:244 | test_validation.py::test_validation_codes[/count invalid] | rewrite as exception handler（沒有對照的 Pydantic type，例如 `multiple_of`，→ `invalid`） |
| B31 | JSON Pointer 跳脫 `~0`/`~1`，指到巢狀鍵 | validation.py:219；problems.py:84-86 | test_validation.py::test_validation_codes[/parts/a~1b] | keep as-is：`pointer()` 不變，handler 用 `loc[1:]` |
| B32 | detail 不回顯輸入值，依 keyword 套模板 | validation.py:247-266 | test_validation.py::test_validation_detail_does_not_echo_the_input | rewrite as exception handler：從 Pydantic error 的 `ctx` 組模板，絕不使用 `msg`/`input`（`msg` 可能帶輸入值） |
| B33 | 空 query 值（`?limit=`）→ `invalid`、`value must not be empty` | validation.py:202-206 | test_validation.py::test_validation_codes[limit=] | rewrite as exception handler：Pydantic 會給 `int_parsing`；loc 是 query 且 `input == ""` 時改成 `invalid` |
| B34 | 空 cursor（`?cursor=`）→ `/cursor` `invalid` | validation.py:202-206 | test_validation.py::test_validation_codes[cursor=]；test_items_http.py::test_list_items_refuses_an_invalid_page[cursor=] | keep as-is：FastAPI 把 `""` 交給 service，`pagination.decode_cursor` 的 fullmatch 會拒絕（platform/pagination.py:29-32）（inferred；test_items_http 會測到） |
| B35 | query 無法轉型（`limit=abc`、`0x10`）→ `invalid_type` | validation.py:207-208 | test_validation.py::test_validation_codes[limit=abc]；test_items_http.py::test_list_items_refuses_an_invalid_page[limit=0x10] | rewrite as exception handler（`int_parsing` → `invalid_type`） |
| B36 | path 參數不是 uuid → `/id` `format` | openapi.yaml:554-560 + openapi-core format | test_validation.py::test_validation_codes[/api/widgets/not-a-uuid]；test_items_http.py::test_an_item_id_is_a_uuid | rewrite as exception handler：`uuid_parsing` → `format`（目前 errors.py:96 會給 `invalid_type`） |
| B37 | 缺必填參數 → `required` | validation.py:198-199；errors.py:92-93 | test_validation.py::test_binding_failure_on_a_missing_value_is_required | rewrite as exception handler |
| B38 | `limit` 1..100、預設 20 → 超出為 `out_of_range` | openapi.yaml:561-569；routers 的 docstring「契約已檢查 limit 範圍」 | test_validation.py::test_validation_codes[limit=0]；test_items_http.py::test_list_items_refuses_an_invalid_page[limit=101] | rewrite as Pydantic constraint：`Query(ge=1, le=100)`，預設值用 `DEFAULT_LIMIT`；同時更新 items/router.py:50、auth/router.py:110, 152 的 docstring |
| B39 | 未知的 openapi-core 錯誤 → `UnexpectedValidationError` → 500（新種錯誤會被發現） | validation.py:77-78, 191-192, 209-210, 216-218 | 無 | delete：Pydantic 錯誤都走 B29/B30 對照，未知 type 退回 `invalid`。改用一個單元測試列出 schemas 實際會產生的 error type，維持「新種錯誤會被發現」 |
| B40 | pattern 採 ECMA-262 語意：`$` 只配字尾、`\d`/`\w`/`\b` 只算 ASCII、`\s` 含 Unicode 空白、`.` 不配行終止字元 | contract.py:55-112, 128-131, 143-147 | test_validation.py::test_pattern_dollar_matches_only_the_end（3 參數）、::test_a_matching_pattern_passes_validation、::test_patterns_have_their_ecma_meaning（6 參數） | rewrite as Pydantic constraint：`Field(pattern=...)`。pydantic-core 預設 Rust regex，`$` 只配字尾（unverified）。但 Rust 的 `\d` 涵蓋 Unicode，與 ECMA 不同；目前唯一的 pattern `^[A-Za-z0-9_.-]+$` 用明確字元類，不受影響。保留 HTTP 測試 test_pattern_dollar_matches_only_the_end；delete ecma_regex 單元測試（翻譯器不存在了）；在 api-design.md 規定 pattern 只用明確字元類 |
| B41 | 契約讀檔失敗、不是合法 OpenAPI、或有不支援的 pattern → 啟動時失敗；serve 印 `serve: load the contract ...` 後退出 | contract.py:168-194；server.py:29, 39-40 | test_validation.py::test_a_pattern_without_its_ecma_meaning_fails_at_load；test_cli.py::test_serve_without_the_contract_fails_cleanly、::test_serve_with_an_invalid_contract_fails_cleanly | delete：執行期不再讀契約檔（spec 由程式產生）。Dockerfile:21 `COPY api api` 一起移除 |
| B42 | 以固定 origin `http://backend` + servers `/api` 比對路徑，不看 Host header | contract.py:34-36, 206 | 無直接測試 | delete：沒有 openapi-core 了 |
| B43 | 測試中：problem 的 `type`、`code`、`errors[].code` 必須落在契約的 x-extensible-enum 內 | apitest.py:58-88, 186-190 | test_problems.py::test_values_outside_the_contract_are_reported；所有 `checked_client` 測試 | rewrite as test helper：從 `app.openapi()` 或匯出檔讀 enum（或直接讀 Python 常數，見 B61） |
| B44 | 測試中：每個 contract operation 的回應都要符合宣告（status 有列、media type、body schema） | apitest.py:179-205, 207-228 | 所有 `checked_client(...)` 測試，包括 test_auth_db.py、test_items_db.py 透過 authtest.py:270 | **DECISION NEEDED (D5)**：(a) openapi-core 降為只在測試用的依賴，對 `app.openapi()` 驗證（openapi-core 支援 3.1，unverified）；(b) 不再驗回應，只靠 FastAPI 的 `response_model`（只管成功回應的 body，不管未宣告的 status 與 problem body）；(c) 改用 schemathesis 等新依賴（要另外核准） |
| B45 | `checked_client` 對非 operation（health、docs、未定義路由）跳過回應驗證 | apitest.py:199-202 | 隱含 | keep as-is（rewrite 時保留） |
| B46 | `assert_problem` 斷言 `content-type == application/problem+json`、type、status、title | apitest.py:91-99 | 大部分 HTTP 測試 | keep as-is |
| B47 | 每個 `/api` 路由都必須是契約的 operation（method + path + path 參數名），反之亦然；不符時 app 不建立，serve 印 `routes and contract disagree` 退出 | routes.py:42-80；factory.py:157；server.py:72-74 | test_routes.py::test_routes_that_match_the_contract_pass、::test_the_app_is_not_built_when_its_routes_and_contract_disagree、::test_an_operation_without_a_route、::test_a_route_that_is_not_an_operation、::test_a_path_parameter_with_another_name；test_cli.py::test_serve_with_routes_that_disagree_with_the_contract_does_not_start | delete：這項檢查的必要條件是「路由與規格各自手寫、可能不一致」；code-first 的規格由路由產生，這個條件不再成立。前端那一側的不一致改由「匯出檔過期檢查」（取代 gen-check）抓 |
| B48 | 每個 `/api` 路由剛好一個 access marker | routes.py:72-75, 98-99, 105-109；access.py:70-71 | test_routes.py::test_a_route_needs_exactly_one_access_marker | keep as-is → rewrite as startup check，搬到 `app/main.py` 或 `app.http.routes` |
| B49 | `PUBLIC` 恰好是契約寫 `security: []` 的 operation | routes.py:76-77；contract_operations routes.py:42-55 | test_routes.py::test_public_routes_are_the_operations_the_contract_makes_public | rewrite as dependency：`SIGNED_IN`/`ADMIN` 內含 `Security(APIKeyCookie(name="__Host-session", scheme_name="sessionCookie", auto_error=False))`，只用來產生文件，真正的檢查仍是 `current_principal()`；`PUBLIC` 不含。測試改成檢查 `app.openapi()`：有 PUBLIC 的 operation 沒有 security，其餘都有 |
| B50 | `/api` 外只允許 health（與 dev docs）；mount 或純 Starlette route 一律拒絕 | routes.py:88-97 | test_routes.py::test_nothing_is_routed_outside_the_api_but_the_health_checks | keep as-is → rewrite as startup check。FastAPI 自己的 `/openapi.json`、`/docs`、`/redoc` 必須維持關閉，或只在 dev 掛到 `/api` 底下 |
| B51 | 以 `iter_route_contexts` 走訪 include 進來的 router（FastAPI 0.137+） | routes.py:12-13, 88 | test_routes.py::test_the_real_app_routes_every_operation（`ME in routed`） | keep as-is（B48 用）；test rewrite 成「真實 app 的 marker 檢查走訪到非空集合」 |
| B52 | 每個可寫的路徑（PATCH/PUT/DELETE）都有 GET | test 內的規格性質 | test_routes.py::test_every_writable_path_has_a_get | rewrite as test helper：改讀 `app.openapi()["paths"]` |
| B53 | MODE=dev：`/api/openapi.yaml` 回傳契約檔原始 bytes，`application/yaml` | factory.py:163-168；contract.py:175-176, 191 | test_validation.py::test_docs_only_in_dev、::test_docs_serve_the_contract_file | rewrite：dev 時 `FastAPI(openapi_url="/api/openapi.json")`（prod 為 None）；test 改成斷言內容等於提交的匯出檔。路徑與格式見 D8 |
| B54 | MODE=dev：`/api/docs` 是 Swagger UI，swagger-ui-dist 5.33.0 從 jsDelivr 載入並帶 SRI | docs.py:1-24；factory.py:170-172 | test_validation.py::test_docs_only_in_dev（`integrity="sha384-`） | keep as-is：FastAPI 內建的 `get_swagger_ui_html` 沒有 SRI（unverified），所以保留自訂 HTML，只把 `url` 改成新的 spec 路徑；`docs_url=None` |
| B55 | MODE=prod：兩個 docs 路徑都是 404 problem | factory.py:152 | test_validation.py::test_docs_only_in_dev（第二段） | keep as-is |
| B56 | docs 路徑不經驗證、不進路由檢查 | factory.py:174, 190；routes.py:93 | 隱含 | keep as-is（B48/B50 的略過清單） |
| B57 | 有對應 problem 的 `HTTPException` → 同 status 的 problem，保留 headers（`Allow`、`WWW-Authenticate`） | errors.py:29, 65-81 | test_problems.py::test_other_http_errors_keep_their_status_and_headers、::test_unknown_path_outside_the_api_is_a_problem | keep as-is（之後也會接手 `/api` 的 404/405） |
| B58 | `RequestValidationError` → validation problem：`missing`→`required`，其他一律 `invalid_type`，丟掉 loc 的第一段 | errors.py:84-98 | test_validation.py::test_binding_failure_after_validation_is_a_validation_problem、::test_binding_failure_on_a_missing_value_is_required | rewrite as exception handler：變成主要的 422 handler，吸收 B23-B37 的對照。原本假設「路由比契約嚴格」的那段 docstring 刪掉 |
| B59 | 未預期的例外 → 記 log（含 stack）、回 500 `internal`、不帶 detail；回應已開始時直接切斷 | errors.py:32-62 | test_problems.py::test_unhandled_error_is_logged_not_sent、::test_unhandled_error_log_has_the_stack、::test_failure_after_the_response_started_aborts_it、::test_app_error_after_the_response_started_aborts_it；test_validation.py::test_failure_in_an_operation_is_a_problem | keep as-is |
| B60 | `AppError` → 對應的 problem；VALIDATION 變單筆 errors 且沒有頂層 code | problems.py:108-115 | test_problems.py::test_app_error_becomes_its_problem、::test_app_error_validation_lists_the_field | keep as-is |
| B61 | 送出的每個 type 與 code 都必須列在契約的 x-extensible-enum | problems.py:3-4；apitest.py:58-70 | test_problems.py::test_problem_values_are_in_the_contract | rewrite as Pydantic constraint：`Problem`/`FieldError` schema 用 `json_schema_extra={"x-extensible-enum": [...]}`，清單從 `PROBLEM_TYPES`、一個 code 登錄表、FieldError code 對照表產生；測試改成比對這些 Python 常數與 `app.openapi()` |
| B62 | 429 的 `Retry-After` 取上限整數；等待 ≤ 0 時不送 | problems.py:111-114 | test_problems.py::test_rate_limited_sends_retry_after_rounded_up、::test_rate_limited_without_a_positive_wait_has_no_retry_after | keep as-is |
| B63 | `signed_in` 的 docstring 說「契約檢查已先擋掉匿名」；marker 只是聲明 | access.py:1-6, 40-46 | test_auth_http.py::test_every_operation_needs_a_session_unless_the_contract_says_public | rewrite as dependency：marker 成為真正的檢查（行為見 B11），docstring 一起改 |
| B64 | `admin` marker：非 admin → 403 `insufficient_role`，並記 `authz.denied` log | access.py:49-61 | test_auth_http.py::test_a_user_who_is_not_an_admin_is_refused_and_it_is_logged | keep as-is |
| B65 | 被契約拒絕的 request 不扣 rate-limit token（與 Go 版一致） | ratelimit.py:3-4；dependency 位置 ratelimit.py:94-104 | test_ratelimit.py::test_a_request_the_contract_rejects_uses_no_token | **DECISION NEEDED (D6)**：依 K2，`RATE_LIMITED` 依賴在 schema 驗證之前執行，422 會扣 token（壞 JSON 依 K1 不會）。(a) 接受（濫用者的無效請求也算數，可說更安全）並改測試；(b) 改在 handler 第一行呼叫 limiter；(c) 讓限流依賴也宣告 body 參數，驗證失敗時 FastAPI 就不呼叫它（dependencies/utils.py:706-708） |
| B66 | AuthenticateMiddleware 在驗證之前，讓 security 看得到 session；在 origin/CSRF 檢查之後，被拒的 request 不查資料庫 | factory.py:177-199 | test_auth_http.py::test_a_request_refused_at_the_edge_never_looks_up_its_session | keep as-is（步驟 8 設計審查補充：原本「讓契約驗證看得到 session」的理由已隨驗證層消失；今天成立的理由是 `PUBLIC` 的登入、登出也要讀 token，以及過期的 cookie 要在任何回應上清掉）：拿掉 `ContractValidation` 那一行，加上 B2 中介層（建議放在原本 ContractValidation 的位置，也就是最內層） |
| B67 | `seed-admin` 用契約產生的 `CreateUserRequest` 驗帳號與密碼 | main/seed.py:5-6, 25 | test_seed.py（2）、test_seed_db.py（6） | keep as-is，只改 import：改用 `app.modules.auth.schemas.CreateUserRequest`（`app.cli` 在最上層，可以 import modules） |
| B68 | `Deps.contract`、`create_app` 用契約、serve 從 `api/openapi.yaml` 讀契約 | factory.py:69-70, 111, 157, 190；server.py:19, 29, 39-57 | 所有 `deps(contract())` / `stub_contract()` 呼叫 | delete：拿掉欄位與讀檔。test helper 改用一個 Pydantic 版的 stub router（取代 testdata/test-openapi.yaml 的 Widget） |

## 2. openapi.yaml 中 code-first 必須重現或刻意放棄的契約特徵

「前端依賴？」欄位只列 `frontend/src` 中**手寫**的程式；`schema.d.ts` 是產生出來的，不算依賴。

| # | 特徵 | 位置（yaml 行） | 前端依賴？(file:line) | 目的地 |
|---|---|---|---|---|
| F1 | `openapi: 3.0.3`（註解還寫著 oapi-codegen 的 3.1 支援） | 1-5 | gen-api.mjs:15-24（openapi-typescript 讀檔） | FastAPI 產生 3.1.0；openapi-typescript 7.x 支援 3.1（unverified）。註解刪除 |
| F2 | `servers: [{url: /api}]`，paths 不含 `/api` | 12-13 | client.ts:16-17（baseUrl 自己加 `/api`）、client.ts:26（`schemaPath === "/sessions"`）、auth-api.ts:12, 17, 21, 25, 32, 33、server.ts:21、client.test.ts:38, 50, 67, 83, 93, 97, 109 | **DECISION NEEDED (D4)**：FastAPI 的 router 是 `prefix="/api"`，所以產生的 paths 會是 `/api/sessions`。(a) 匯出時去掉前綴並加 `servers: [/api]`（覆寫 `app.openapi` 或用 `root_path`），前端不動；(b) 前端 baseUrl 改成 origin，所有 path 字串與 client.ts:26 改成 `/api/...`；(c) 把 API 掛成 `/api` 子 app |
| F3 | 全域 `security: [sessionCookie]`；`createUser`、`createSession`、`deleteCurrentSession` 寫 `security: []` | 14-15, 23, 259, 355 | 無 | rewrite as dependency：由 marker 產生（B49） |
| F4 | securityScheme `sessionCookie`：apiKey、in cookie、name `__Host-session`，description 說明 COOKIE_SECURE=false 時改名 `session` | 545-552 | 無 | `APIKeyCookie(name="__Host-session", scheme_name="sessionCookie", description=..., auto_error=False)`。文件寫死名稱，與現在相同 |
| F5 | 21 個 camelCase operationId（createUser … deleteItem） | 19, 59, 83, 98, 123, 145, 183, 209, 230, 249, 295, 318, 336, 351, 370, 390, 414, 448, 472, 492, 528 | 手寫程式無；schema.d.ts 的 `operations[...]` 鍵。其他使用者：test_queries.py:10-17 與 test_queries_db.py:22-27（`listItems`/`listSessions`/`listUsers`）、docs/security/threat-model-auth.md:29-37、docs/privacy/data-inventory.md:22, 67 | 每個路由明確寫 `operation_id=`（FastAPI 預設會產生 `create_item_api_items_post`）；或設 `generate_unique_id_function` 把 endpoint 名稱轉 camelCase |
| F6 | tags `auth`、`items` | 每個 operation | 無 | `APIRouter(tags=[...])` |
| F7 | 每個 operation 的 summary/description | 每個 operation | 無 | `summary=` 與 docstring（FastAPI 用 docstring 當 description） |
| F8 | 每個 operation 宣告的 status code（見 2b） | 30-543 | client.ts:25（401 時轉址，`POST /sessions` 例外）；errors.ts:48（`response.ok`） | 路由上寫 `responses={...}`；每個有參數或 body 的 operation 都要宣告 422，否則依 K3 會多出 `HTTPValidationError`/`ValidationError` 兩個 schema 跑進 schema.d.ts |
| F9 | 所有錯誤回應都是 `application/problem+json` → `Problem` 或 `ValidationProblem`（components/responses 9 個） | 861-924 | errors.ts:3-4（`components["schemas"]["Problem"/"FieldError"]`）；auth-form.test.tsx:28、delete-account.test.tsx:76、client.test.ts:13（mock 的 content-type） | `responses={4xx: {"model": Problem, "content": {"application/problem+json": {}}}}` 之類的寫法；FastAPI 怎麼把 `model` 放進 `application/problem+json` 底下尚未驗證（unverified）。第一步先做一個 operation 的原型，diff 產生的 schema.d.ts |
| F10 | 201 的 `Location` header（createUser、createSession、createItem） | 33-35, 269-271, 426-428；component 577-580 | 無 | `responses={201: {"headers": {"Location": ...}}}`。也可以刻意放棄（只是文件；行為由 router 設定，有 test 測） |
| F11 | `Set-Cookie` header：SetSessionCookie、ClearSessionCookie | 36-37, 108-110, 272-273, 325-327, 359-361, 399-401；component 581-588 | 無 | 同 F10（只是文件） |
| F12 | 429 的 `Retry-After`（integer，minimum 0） | 910-914；component 589-593 | 無 | 同 F10；行為見 B62 |
| F13 | `Limit` query 參數：integer 1..100，default 20 | 561-569 | 無；test_pagination.py:60-62 斷言 default == `DEFAULT_LIMIT` | `Query(ge=1, le=100)`，預設 `DEFAULT_LIMIT`；測試改讀 `app.openapi()` |
| F14 | `Cursor` query 參數：string，可省略 | 570-575 | 無 | `cursor: str \| None = None` 加 description |
| F15 | `Id` path 參數：string、format uuid、名稱 `id` | 554-560 | 無 | `id: uuid.UUID`（ruff.toml:22-23 說明為什麼用 `id`） |
| F16 | x-extensible-enum `Problem.type`（12 個 `/problems/*`） | 776-793 | errors.ts:9-10；messages.test.ts:11, 23-24；gen-api.mjs:18-24（轉成 union） | `json_schema_extra`，從 `PROBLEM_TYPES` 產生（B61）。gen-api.mjs 的 transform 不用改 |
| F17 | x-extensible-enum `Problem.code`（8 個） | 803-814 | errors.ts:11；messages.test.ts:12, 25；errors.test.ts:8, 39 | `json_schema_extra`，從一個 code 登錄表產生。目前 code 散在 access.py:33、principal.py、各 service 裡 |
| F18 | x-extensible-enum `FieldError.code`（12 個） | 840-857 | errors.ts:12；messages.test.ts:13, 26 | `json_schema_extra`，從 B29 對照表的值加上業務 code（`password_too_common`）產生 |
| F19 | `User.role` enum `[admin, user]` | 606-607 | 無（手寫程式沒讀 role） | `Literal["admin", "user"]` |
| F20 | 具名 schema `ItemTitle`（1..200），被 CreateItemRequest/ItemPatch `$ref` | 747-770 | 無（只有 schema.d.ts:333, 337, 348） | 刻意放棄名稱：`Annotated[str, Field(min_length=1, max_length=200)]` 直接展開在欄位裡（datamodel-codegen 現在本來就 collapse，見 pyproject.toml:58-59） |
| F21 | nullable 且必填：`UserPage`/`SessionPage`/`ItemPage.next_cursor`、`Item.description` | 621-624, 658-661, 743-746, 723-725 | schema.d.ts 型別 `string \| null`；手寫程式沒讀 next_cursor | `str \| None` 不給預設值 → 3.1 的 `anyOf [string, null]` 且列在 required（inferred）。output schema 預設會把有預設值的欄位列為 required（`separate_input_output_schemas=True`） |
| F22 | nullable 且可省略：`CreateItemRequest.description`、`ItemPatch.description` | 754-756, 763-765 | 無 | `str \| None \| MISSING = MISSING`（沿用 contract_models.py:183, 194）；`MISSING` 在 JSON Schema 中的呈現尚未驗證（unverified） |
| F23 | 可省略但不可 null：`UserPatch.is_disabled`、`ItemPatch.title`、`Problem.detail/code/request_id` | 711-713, 761-762, 800-817 | errors.ts:68（`problem.code` 可能不存在） | `X \| MISSING = MISSING`；test_contract_models.py 的 3 個測試搬到 schemas 測試 |
| F24 | request schema 都是 `additionalProperties: false`（CreateUserRequest、CreateSessionRequest、UserPatch、CreateItemRequest、ItemPatch） | 664, 684, 710, 748, 758 | 無 | `ConfigDict(extra="forbid")` |
| F25 | `format: uuid` / `date-time`（id、owner_id、*_at） | 599-612, 630-646, 718-734 | 只有 schema.d.ts 的註解 | `uuid.UUID`、`AwareDatetime` |
| F26 | `SignInLock.failures` 是 `format: int64`、minimum 0 | 703-707 | 無 | `Field(ge=0)`；int64 刻意放棄（Pydantic 不產生，只影響註解） |
| F27 | `Problem.type` 是 `format: uri-reference` | 778 | 無 | `json_schema_extra={"format": "uri-reference"}`，或刻意放棄 |
| F28 | `CreateUserRequest.account` 的 pattern `^[A-Za-z0-9_.-]+$` | 671 | 無（表單只檢查有沒有填） | `Field(pattern=...)`；語意見 B40 |
| F29 | 長度：account 3..50 / 1..50，password 15..128 / 1..128，ItemTitle 1..200 | 669-676, 689-695, 768-769 | 無 | `Field(min_length, max_length)`；docs/rules/database-design.md:67 要求 DB CHECK 與它一致（改成與 schemas.py 一致）；test_passwords.py:154-157 斷言 `PASSWORD_MIN_LENGTH` |
| F30 | `examples`：Problem.type、FieldError.pointer、FieldError.code | 793, 838, 857 | 只有 schema.d.ts 的 `@example` | `Field(examples=[...])`（3.1 是 `examples` 陣列） |
| F31 | schema 與 property 的 description（例如密碼的 NFKC 規則、Session 的 idle 說明） | 遍布 594-860 | 只有 schema.d.ts 的註解 | `Field(description=...)`，或 `use_attribute_docstrings` |
| F32 | `ValidationProblem` = `allOf [Problem, {errors}]` | 818-828 | 無（errors.ts:16-19 自己定義 `errors?`） | `class ValidationProblem(Problem)`，產生扁平的 properties；刻意放棄 allOf |
| F33 | `Session.user` 巢狀 `$ref User` | 633-634 | auth-api.ts:12-13（`session.user`） | 巢狀 model |
| F34 | info：title、version、description（problem 規則） | 6-11 | 無 | `FastAPI(title=, version=, description=)` |
| F35 | public 的 `createSession` 有 401（帳密錯誤） | 280-281 | client.ts:26（這個路徑的 401 不轉址） | 寫進 `responses`（F8） |
| F36 | 405、503 不屬於任何 operation；health 不在契約內 | — | 無 | keep：health 維持 `include_in_schema=False` |
| T1 | （步驟 4a 補列）產生的規格在每個 schema 與 property 多了 Pydantic 的 `title`（例如 `"title": "Next Cursor"`）；yaml 沒有 | — | 無（推論 openapi-typescript 的型別不受 `title` 影響；步驟 5 產生 `schema.d.ts` 時核對） | 保留：只是文件，不影響驗證或型別；不寫後處理去掉（FastAPI 應用的常態）。已決（2026-11-11，Will）；步驟 5 量到只進 JSDoc 註解，型別不變 |
| T2 | （步驟 4a 補列）yaml 的 `components/parameters`、`headers`、`responses` 在產生的規格裡逐 operation 內嵌 | 553-593、861-924 | 無 | 刻意放棄：FastAPI 沒有可重用的 parameter／response component；內容相同由 oasdiff 解開 `$ref` 比對（`contract-delta.md`） |

### 2b. 每個 operation 宣告的 status code 與 header（F8 的清單；measured，從 yaml 讀出）

| operationId | 路由 | security | 參數 | request media | responses | headers |
|---|---|---|---|---|---|---|
| `createUser` | POST /users | public | - | application/json | 201 400 403 409 413 415 422 429 500 | 201: Location, Set-Cookie |
| `listUsers` | GET /users | session | Limit, Cursor | - | 200 401 403 422 500 | - |
| `getCurrentUser` | GET /users/me | session | - | - | 200 401 500 | - |
| `deleteCurrentUser` | DELETE /users/me | session | - | - | 204 401 403 409 500 | 204: Set-Cookie |
| `getUser` | GET /users/{id} | session | Id | - | 200 401 404 422 500 | - |
| `patchUser` | PATCH /users/{id} | session | Id | application/merge-patch+json | 200 400 401 403 404 409 413 415 422 500 | - |
| `deleteUser` | DELETE /users/{id} | session | Id | - | 204 401 403 404 409 422 500 | - |
| `getSignInLock` | GET /users/{id}/sign-in-lock | session | Id | - | 200 401 403 404 422 500 | - |
| `deleteSignInLock` | DELETE /users/{id}/sign-in-lock | session | Id | - | 204 401 403 404 422 500 | - |
| `createSession` | POST /sessions | public | - | application/json | 201 400 401 403 413 415 422 429 500 | 201: Location, Set-Cookie |
| `listSessions` | GET /sessions | session | Limit, Cursor | - | 200 401 422 500 | - |
| `deleteAllSessions` | DELETE /sessions | session | - | - | 204 401 403 500 | 204: Set-Cookie |
| `getCurrentSession` | GET /sessions/current | session | - | - | 200 401 500 | - |
| `deleteCurrentSession` | DELETE /sessions/current | public | - | - | 204 403 500 | 204: Set-Cookie |
| `getSession` | GET /sessions/{id} | session | Id | - | 200 401 404 422 500 | - |
| `deleteSession` | DELETE /sessions/{id} | session | Id | - | 204 401 403 404 422 500 | 204: Set-Cookie |
| `createItem` | POST /items | session | - | application/json | 201 400 401 403 413 415 422 500 | 201: Location |
| `listItems` | GET /items | session | Limit, Cursor | - | 200 401 422 500 | - |
| `getItem` | GET /items/{id} | session | Id | - | 200 401 404 422 500 | - |
| `updateItem` | PATCH /items/{id} | session | Id | application/merge-patch+json | 200 400 401 403 404 413 415 422 500 | - |
| `deleteItem` | DELETE /items/{id} | session | Id | - | 204 401 403 404 422 500 | - |

觀察：403 只出現在會改狀態的方法（CSRF，cross_origin.py）；429 只在 createUser/createSession；`getCurrentUser`/`getCurrentSession` 沒有 422（沒有參數）。如果 D5 選擇不驗回應，這張表就沒有任何測試在守了。

## 3. 引用點（consumers）

指令（repo root；`G` 見文件開頭：排除 .git、node_modules、.venv、__pycache__、docs/plans、coverage.xml、test-report.xml）：

```bash
rg -c ${=G} -e '<pattern>'    # 每個檔案的行數；檔案數 = rg -l ... | wc -l
```

| 組 | pattern | 檔案數 | 符合行數 |
|---|---|---|---|
| openapi.yaml | `openapi\.yaml` | 33 | 58 |
| contract_models | `contract_models` | 12 | 24 |
| gen / gen-check | `gen-check\|\bgen:api\|make gen\b\|-C backend gen\|\bgen\b:` | 17 | 39 |
| datamodel-codegen | `datamodel-codegen\|datamodel_codegen` | 5 | 7 |
| openapi-core | `openapi[-_]core` | 13 | 45 |
| app.platform | `app\.platform` | 43 | 95 |
| app.web | `app\.web` | 39 | 97 |
| app.main | `app\.main` | 43 | 76 |
| 斜線路徑 | `app/(main\|web\|platform)\b` | 22 | （見下方） |

各組檔案（括號內為行數）：

- **openapi.yaml**：.env-example(1)、.gitattributes(1)、.github/workflows/ci.yml(1)、AGENTS.md(5)、README.md(1)、template/derived/README.md(1)、backend/Makefile(1)、backend/pyproject.toml(4)、backend/src/app/main/factory.py(4)、backend/src/app/main/server.py(1)、backend/src/app/modules/auth/router.py(1)、backend/src/app/modules/items/router.py(1)、backend/src/app/web/contract_models.py(1)、backend/src/app/web/contract.py(2)、backend/src/app/web/docs.py(1)、backend/src/app/web/validation.py(1)、backend/tests/apitest.py(4)、backend/tests/test_cli.py(2)、backend/tests/test_contract_models.py(1)、backend/tests/test_validation.py(4)、backend/tests/testdata/test-openapi.yaml(1)、docs/adr/2026-10-18-api-and-auth-baseline.md(3)、docs/adr/2026-11-05-fastapi-backend-stack.md(3)、docs/deploy-vps.md(1)、docs/rules/add-module.md(1)、docs/rules/api-design.md(2)、docs/rules/database-design.md(1)、docs/security/threat-model-auth.md(2)、frontend/AGENTS.md(2)、frontend/README.md(1)、frontend/osv-scanner.toml(1，說明文字)、frontend/scripts/gen-api.mjs(1)、frontend/src/lib/api/__tests__/messages.test.ts(1，註解)。
- **contract_models**：.gitattributes(1，`linguist-generated`)、AGENTS.md(2)、backend/Makefile(8)、backend/pyproject.toml(3，含 coverage omit)、backend/ruff.toml(1，per-file ignore)、backend/src/app/main/seed.py(2)、backend/src/app/modules/auth/router.py(1)、backend/src/app/modules/items/router.py(1)、backend/tests/test_contract_models.py(1)、docs/adr/2026-11-05-fastapi-backend-stack.md(1)、docs/rules/add-module.md(1)、docs/rules/api-design.md(2)。
- **gen / gen-check**：.github/workflows/ci.yml(3：backend gen-check 說明 :38、frontend `pnpm gen:api:check` :252-253)、AGENTS.md(4)、backend/api/openapi.yaml(1，註解)、backend/Makefile(6：`gen`、`gen-check`、`check` 依賴 :119)、backend/pyproject.toml(5)、backend/ruff.toml(1)、backend/src/app/main/seed.py(1)、backend/src/app/web/contract_models.py(2)、backend/tests/test_contract_models.py(2)、docs/adr/2026-11-05-fastapi-backend-stack.md(3)、docs/rules/add-module.md(1)、docs/rules/api-design.md(3)、frontend/AGENTS.md(2)、frontend/package.json(2：`gen:api`、`gen:api:check`)、frontend/README.md(1)、frontend/scripts/gen-api.mjs(1)、lefthook.yml(1：pre-push frontend 指令 :44)。
- **datamodel-codegen**：backend/Makefile(2)、backend/pyproject.toml(2：dev 依賴 :24-25、`[tool.datamodel-codegen]` :43-70)、backend/src/app/web/contract_models.py(1)、backend/tests/test_contract_models.py(1)、docs/rules/api-design.md(1)。
- **openapi-core**：backend/Makefile(1，vulture 註解)、backend/pyproject.toml(2：:11-13 runtime 依賴，還有 jsonschema)、backend/src/app/web/contract.py(14)、backend/src/app/web/validation.py(10)、backend/tests/apitest.py(2)、backend/tests/test_validation.py(1)、backend/tests/testdata/test-openapi.yaml(1)、backend/tests/vulture_whitelist.py(1)、backend/uv.lock(5)、docs/adr/2026-11-05-fastapi-backend-stack.md(3)、docs/rules/api-design.md(1)。**誤判**：frontend/osv-scanner.toml(1) 與 frontend/pnpm-lock.yaml(3) 是 openapi-typescript 帶進來的 `@redocly/openapi-core`，與後端無關。
- **app.platform**：AGENTS.md(6)、backend/.importlinter(5)、backend/.squawk.toml(1)、backend/ruff.toml(3，`TID251`)、backend/src/app/main/{cli(1), factory(2), healthcheck(1), logs(1), migrate(2), schema(1), seed(4), server(3)}.py、backend/src/app/modules/auth/{dependencies(4), models(1), repository(1), router(3), service/__init__(9)}.py、backend/src/app/modules/items/{models(1), router(1), service(4)}.py、backend/src/app/platform/{config(1), db(1), pagination(1), principal(2)}.py、backend/src/app/web/{access(2), contract(1), errors(1), problems(1), ratelimit(1)}.py、backend/tests/{authfake(2), authtest(3), dbtest(2), generate_migration(1), test_config(1), test_hibp(3), test_logging(1), test_migrate_db(2), test_pagination(2), test_problems(1), test_schema_db(2)}.py、docs/adr/2026-11-05-fastapi-backend-stack.md(1)、docs/rules/api-design.md(4)、docs/rules/database-design.md(5)。
- **app.web**：AGENTS.md(3)、backend/.importlinter(4)、backend/pyproject.toml(1)、docker-compose.yml(1，註解 :74)、backend/src/app/main/{factory(15), logs(2), routes(4), seed(2), server(5)}.py、backend/src/app/modules/auth/{dependencies(2), router(7)}.py、backend/src/app/modules/items/router.py(3)、backend/src/app/platform/{config(1), errors(1), principal(2)}.py（docstring）、backend/src/app/web/{access_log(1), access(1), contract(1), cross_origin(1), errors(1), origin(1), problems(1), ratelimit(3), validation(3)}.py、backend/tests/{apitest(5), authfake(1), authtest(2), test_auth_http(2), test_contract_models(1), test_health(1), test_logging(1), test_origin(1), test_problems(2), test_ratelimit(2), test_routes(1), test_validation(2), vulture_whitelist(1)}.py、docs/adr/2026-11-05-fastapi-backend-stack.md(2)、docs/rules/api-design.md(7)。
- **app.main**：AGENTS.md(3)、backend/.importlinter(5，含 light-healthcheck 的 `app.main.healthcheck`、`app.main.cli` :83-84)、backend/Makefile(1)、backend/pyproject.toml(1：`app = "app.main.cli:main"` :20)、backend/src/app/main/{cli(5), factory(4), migrate(1), recover(2), seed(1), server(3)}.py、backend/src/app/main/migrations/{__init__(1), env(4)}.py、backend/src/app/modules/auth/{dependencies(2), models(1), service/__init__(2)}.py、backend/src/app/modules/items/dependencies.py(1)、backend/src/app/web/{__init__(1), access(4), docs(1), ratelimit(1), request_id(1)}.py、backend/tests/{apitest(1), authfake(1), authtest(1), dbtest(2), generate_migration(1), revision_sql(1), test_auth_db(1), test_auth_http(2), test_cli(3), test_health(1), test_logging(1), test_migrate_db(2), test_routes(3), test_schema_db(2), test_schema(2), test_seed_db(1), test_seed(1), test_validation(1)}.py、docs/adr/2026-11-05-fastapi-backend-stack.md(1)、docs/rules/add-module.md(1)、docs/rules/api-design.md(1)、docs/security/threat-model-auth.md(1)。**字串形式、改名時特別容易漏**：backend/src/app/main/migrate.py:45 `script_location = "app.main:migrations"`（搬到 `app/migrations/` 時必改）。
- **斜線路徑 `app/(main|web|platform)`**（rename 時要改）：.gitattributes:6、.github/workflows/ci.yml:64（`src/app/main/migrations/versions`）、AGENTS.md(:9, :14, :96)、Makefile:46、backend/Makefile(:19, :27, :32, :63, :90-91)、backend/pyproject.toml(:24, :41, :43)、backend/ruff.toml:27、backend/tests/generate_migration.py、backend/tests/test_auth_http.py、backend/tests/test_schema.py、scripts/check_migrations_immutable.py:15、scripts/test_check_migrations_immutable.py:12、scripts/check_vercel_config.py、docs/adr/2026-11-05-fastapi-backend-stack.md、docs/adr/2026-11-05-vercel-rewrite-and-cloudflare-tunnel.md、docs/deploy-vps.md、docs/frontend-known-issues.md、docs/privacy/data-inventory.md、docs/rules/{add-module, api-design, database-design}.md、docs/security/threat-model-auth.md。

不在上面各組、但同樣受影響：

- `backend/Dockerfile:21` `COPY api api`（執行期讀契約；B41）。
- `.env-example:15`、`docs/deploy-vps.md:68`（`/api/openapi.yaml` 與 `/api/docs` 路徑文字；B53）。
- `scripts/quality-locks.json`（`"backend": 354` 收集到的測試數，只能往上）：刪測試會讓 `scripts/check_locks.py` 失敗，必須在同一個 commit 更新 lock 並帶 `Quality-Gate:` trailer（AGENTS.md Verification）。
- `scripts/check_doc_refs.py`：檢查 docs 路徑與 `ADR <slug> D<n>` 標籤都存在。改掉或刪掉的 docs/ADR 參照會讓它失敗。
- `.gitattributes`：提交的 OpenAPI 匯出檔要標 `linguist-generated`（`check_commit_policy.py` 計算大改動時會排除它）。
- `backend/.importlinter`：層級表（top-layers、http-stays-at-the-edge、light-healthcheck）全部以舊套件名寫成，rename 時要一起改，並帶 `Quality-Gate:` trailer（它是 checker 設定）。

## 4. 前端耦合

現在怎麼吃規格（measured）：

- `frontend/scripts/gen-api.mjs:15` 固定讀 `../../backend/api/openapi.yaml`；`:18-24` 用 openapi-typescript 的 `transform`，把帶 `x-extensible-enum` 的 schema 轉成字串字面值 union；`:27-32` 的 `--check` 是 CI（ci.yml:252-253）與 lefthook pre-push（lefthook.yml:44）的過期檢查。
- `src/lib/api/client.ts:3` `paths` 型別給 openapi-fetch；`:16-17` baseUrl 自己加 `/api`；`:26` 以 `schemaPath === "/sessions"`（不含 `/api` 的契約路徑字串）排除登入的 401。
- `src/lib/api/errors.ts:3-4` 取 `components["schemas"]["FieldError"]`、`["Problem"]`；`:9-12` 由 enum union 推出 `ProblemTypeSlug`（依賴 `/problems/` 前綴）、`ProblemCode`、`FieldErrorCode`；`:16-19` 自己定義 `errors?`（不依賴 ValidationProblem）；`:91` 依 `pointer` 的第一段對應表單欄位。
- `src/lib/api/__tests__/messages.test.ts:4, 9-35`：編譯期檢查每個 enum 值都有翻譯；`:23-26` 斷言 union 不是 `string` 也不是 `never`（防止 gen-api 不再轉 union）。
- `src/features/auth/api/auth-api.ts:5-6` 用 schema 名稱 `User`、`CreateSessionRequest`（`register` 也拿 `CreateSessionRequest` 型別當 `POST /users` 的 body，因為形狀與 CreateUserRequest 相同）；`:12-33` 路徑字串 `/sessions`、`/users`、`/sessions/current`、`/users/me`。
- `src/features/auth/server.ts:21` `api.GET("/users/me")`。
- 不使用 operationId；不讀 header 型別；沒有用到 pattern 或長度限制（表單只檢查有沒有填）。

規格改成 FastAPI 產生的 3.1 時會變的地方：

| # | 改變 | 影響 | 處理 |
|---|---|---|---|
| E1 | 規格來源從手寫 YAML 變成提交的匯出檔 | gen-api.mjs:15 的路徑；frontend/AGENTS.md:14, 73、README 文字 | 改路徑（格式見 D8）；後端加「匯出檔過期」檢查取代 gen-check |
| E2 | paths 多了 `/api` 前綴 | client.ts:16-17, 26；auth-api.ts；server.ts:21；client.test.ts | D4 |
| E3 | schema 名稱：FastAPI 用 class 名稱；同一個 model 若同時用在 input 與 output 且有預設值，會拆成 `X-Input`/`X-Output`（`separate_input_output_schemas`）；兩個模組有同名 class 會變成很長的帶模組名稱 | errors.ts:3-4（`Problem`、`FieldError`）；auth-api.ts:5-6（`User`、`CreateSessionRequest`） | 所有 class 名稱維持跟契約一樣且全域唯一；`Problem`/`FieldError` 必須真的被 responses 參照才會出現在 components（F9） |
| E4 | `x-extensible-enum` 必須由 `json_schema_extra` 放進產生的 schema | errors.ts:9-12；messages.test.ts（不然 union 退回 `string`，`:23-26` 會擋下） | F16-F18 |
| E5 | FastAPI 自動加的 422 `HTTPValidationError`/`ValidationError` schema（K3） | 會出現在 schema.d.ts 與 openapi-fetch 的 error union | 每個 operation 宣告 422 → `ValidationProblem` |
| E6 | nullable 從 `nullable: true` 變成 `anyOf [..., {type: null}]` | openapi-typescript 應該一樣產生 `string \| null`（unverified） | 第一次產生後 diff schema.d.ts |
| E7 | `example` 變成 `examples` 陣列；`ItemTitle` 不再有名稱；`allOf` 變成扁平；int64/uri-reference 的 format 註解消失 | 只影響 schema.d.ts 的註解與型別別名；手寫程式沒依賴 | 刻意放棄（F20、F26、F27、F30、F32） |
| E8 | operationId 若沒明確設定會變成 `create_item_api_items_post` | schema.d.ts 的 `operations` 鍵；手寫程式沒依賴，但後端測試與 docs 有（F5） | F5 |
| E9 | request body 的 media type：PATCH 必須產生 `application/merge-patch+json` | openapi-fetch 依 `requestBody.content` 型別；目前前端沒有呼叫 PATCH | `Body(media_type="application/merge-patch+json")` |

## 5. 測試

計數指令：`rg -c '^(async )?def test_' backend/tests/*.py`（只算函式定義，不算 parametrize 展開；`scripts/quality-locks.json` 鎖的 354/95 是收集到的測試數）。

| 模組 | def 數 | 分類 | 細節 |
|---|---|---|---|
| test_validation.py | 24 | rewrite（2 個 delete） | delete：`test_patterns_have_their_ecma_meaning`、`test_a_pattern_without_its_ecma_meaning_fails_at_load`（B40/B41）。其餘 22 個改用 Pydantic 版 stub router 測同樣的行為（B2-B38）；`test_docs_*` 兩個改用 `/api/openapi.json`（B53） |
| test_routes.py | 10 | rewrite（5 個 delete） | delete：`test_routes_that_match_the_contract_pass`、`test_the_app_is_not_built_when_its_routes_and_contract_disagree`、`test_an_operation_without_a_route`、`test_a_route_that_is_not_an_operation`、`test_a_path_parameter_with_another_name`（B47）。rewrite：`test_the_real_app_routes_every_operation`（B51）、`test_a_route_needs_exactly_one_access_marker`（B48）、`test_public_routes_are_the_operations_the_contract_makes_public`（B49）、`test_nothing_is_routed_outside_the_api_but_the_health_checks`（B50）、`test_every_writable_path_has_a_get`（B52） |
| test_contract_models.py | 3 | rewrite | 搬去測 `app.modules.*.schemas`（F22/F23；MISSING 的語意） |
| test_problems.py | 12 | helper + 1 rewrite | `test_problem_values_are_in_the_contract` 改成用新的 code 對照表與 `app.openapi()`（B61）；其餘只改 `bare_app`/`stub_app` 與 import |
| test_cli.py | 11 | 3 delete/rewrite + import 改名 | delete：`test_serve_without_the_contract_fails_cleanly`、`test_serve_with_an_invalid_contract_fails_cleanly`（B41）；`test_serve_with_routes_that_disagree_with_the_contract_does_not_start` 改成 marker 檢查失敗（B48）或刪除。其餘把 `app.main` 改成 `app.cli`/`app.main` |
| test_ratelimit.py | 9 | helper + 1 待決 | `test_a_request_the_contract_rejects_uses_no_token` 依 D6；其餘只改 helper 與 `app.web` import |
| test_auth_http.py | 14 | helper + 1 rewrite | `test_every_operation_needs_a_session_unless_the_contract_says_public` 的列舉改讀 `app.openapi()`，detail/code 斷言依 D3；`test_an_invalid_cookie_with_an_invalid_body_is_unauthenticated_not_invalid` 依 D2；其餘只改 helper |
| test_items_http.py | 6 | helper | 期望的 code 不變，靠 B29-B36 的對照達成；`test_a_patch_is_a_merge_patch` 依 D2（415） |
| test_middleware.py | 4 | helper | `stub_app` |
| test_origin.py | 9 | helper + import 改名 | `stub_app`；`app.web.origin` |
| test_cross_origin.py | 3 | helper | `bare_app` |
| test_logging.py | 12 | helper + import 改名 | `bare_app`/`stub_app`；`app.main.logs`、`app.platform.config`、`app.web.origin` |
| test_health.py | 8 | helper + import 改名 | `deps(contract())` → 不含契約的 deps |
| test_pagination.py | 5 | 1 rewrite + import 改名 | `test_the_default_limit_is_the_contracts` 改讀 `app.openapi()`（不再用 yaml）；`app.platform` → `app.core` |
| test_passwords.py | 9 | 1 rewrite | `test_the_minimum_is_the_contracts` 改讀 schema/`app.openapi()` |
| test_queries.py | 1 | rewrite | 從 `app.openapi()` 收集 paginated list 的 operationId（依賴 F5） |
| test_auth_db.py | 64 | 只有 helper / import 改名 | 透過 authtest.py 的 `checked_client(..., against=contract())`；`app.main.logs` |
| test_items_db.py | 13 | 只有 helper | 透過 authtest |
| test_queries_db.py | 1 | 只有 helper | `LISTS` 的鍵是 operationId（F5） |
| test_seed_db.py | 6 | import 改名 | `app.main.seed` |
| test_seed.py | 2 | import 改名 | `app.main.seed`（B67） |
| test_schema.py | 8 | import 改名 | `app.main.migrate`、`app.main.schema` |
| test_schema_db.py | 2 | import 改名 | 同上 + `app.platform.*` |
| test_migrate_db.py | 7 | import 改名 | `app.main`、`app.main.migrate`、`app.platform.*` |
| test_config.py | 21 | import 改名 | `app.platform.config` |
| test_hibp.py | 8 | import 改名 | `app.platform.errors`、`app.platform.hibp` |
| test_auth_repository.py | 6 | 不受影響 | 只 import `app.modules.auth` |
| test_items_repository.py | 3 | 不受影響 | 只 import `app.modules.items` |

小計：delete 9-10（test_validation 2、test_routes 5、test_cli 2 或 3）；rewrite 約 33（test_validation 22、test_contract_models 3、test_routes 5、test_problems 1、test_auth_http 1、test_pagination 1、test_passwords 1、test_queries 1，再加上 test_cli 那 1 個若不刪）；其餘只改 helper 或 import。28 個模組的 def 總數 281。

輔助檔：

- `tests/apitest.py`：rewrite as test helper（拿掉 `Contract`/`ContractRequest`/`ContractResponse`/`stub_contract`/`CONTRACT_PATH`/`TEST_CONTRACT_PATH`；`contract_values` 改讀 `app.openapi()`；`checked_client` 依 D5）。
- `tests/testdata/test-openapi.yaml`：delete，換成測試用的 Pydantic `Widget` model 與 stub router，每個欄位各測一種 code（含 `multipleOf` → `invalid`、`parts` 的 `a/b` 鍵）。
- `tests/authfake.py`、`tests/authtest.py`：helper（`deps(contract())`、`checked_client(..., against=contract())`）+ import 改名。
- `tests/dbtest.py`、`tests/generate_migration.py`、`tests/revision_sql.py`：import 改名（`app.main.migrate`、`app.platform.*`）。
- `tests/vulture_whitelist.py`：delete（只是為 openapi-core 的 protocol 屬性存在）；`backend/Makefile:63` 的 `--exclude '*/contract_models.py'` 一起拿掉。
- 依賴：pyyaml 只是透過 `datamodel-code-generator` 與 `jsonschema-path`（openapi-core 帶進來）間接安裝的（measured，uv.lock）。兩者都移除後，test_pagination.py:7、test_passwords.py:13、test_queries.py:3 的 `import yaml` 會壞。照上表改讀 `app.openapi()` 就不需要 yaml。`jsonschema` 是直接依賴（pyproject.toml:11-12），只為 validation.py 存在，可以一起移除（若 D5 選 a，openapi-core 會把它帶回 dev 依賴）。

前端測試：`client.test.ts`（路徑字串，依 D4）；`messages.test.ts`、`errors.test.ts` 在 F16-F18 保留的前提下不用改。

## 6. DECISION NEEDED 總表

| ID | 問題 | 相關列 |
|---|---|---|
| D3 | 匿名呼叫需要登入的 operation：接受 401 改帶 `code: session_invalid`、detail `no valid session`（marker 現在的行為），還是保留無 code、detail `this operation needs a signed-in session`？ | B11、B63 |
| D2 | 保留 404/405 > 401 > 415 > 400 > 422 的順序與 415/嚴格 JSON（NaN、非 UTF-8），用 custom route class（建議），還是接受 FastAPI 原生行為（沒有 415；匿名送壞 JSON 時 400/422 先於 401），或用中介層自己重做路由比對？ | B12、B14-B17、B19-B22 |
| D5 | 測試中的回應驗證：openapi-core 降為只在測試用的依賴，對 `app.openapi()` 驗證；或不再驗回應；或引入新依賴（要核准）？ | B44、2b |
| D6 | 422 的 request 是否扣 rate-limit token：接受 FastAPI 依賴順序並改測試，還是在 handler 內呼叫 limiter，或讓限流依賴宣告 body 參數？ | B65 |
| D4 | 規格 paths 的 `/api`：匯出時去掉前綴並加 `servers: [/api]`（前端不動），還是前端改用含 `/api` 的路徑，或把 API 掛成 `/api` 子 app？ | F2、E2 |
| D8 | 提交的匯出檔格式與位置（例如 `backend/api/openapi.json`；YAML 需要 pyyaml 直接依賴），以及 dev 是否以 `/api/openapi.json` 提供同一份？ | B53、E1 |

拍板結果（2026-11-08，Will）：D3 選統一用 `session_invalid`；D4 選 paths 含 `/api`；D8 用 JSON；D2、D5、D6 照業界基準簡報（已併入 ADR `docs/adr/2026-11-11-code-first-contract-and-request-checks.md`）——D2 custom route class（401/403 → 429 → 415 含 `Accept-Patch` → 400 → 422，403 也提前），D5 openapi-core 降為 dev 依賴並對產生的規格驗證、`Problem` 由 model 建構，D6 每個請求都扣 token（B65 的測試改成「被擋下也扣」）。上表各列的 DECISION NEEDED 以此為準，細節見計畫的決定表。

## 7. 未驗證項目

- 沒有跑任何測試或產生指令。K1-K3 是讀 FastAPI 0.162.0 原始碼得到的（inferred），每個依賴它們的目的地（B6、B12、B14-B16、B19-B21、B24、B25、B65）都要在第一步用測試量測。
- pydantic-core Rust regex 的 `$` 只配字尾、`\d` 涵蓋 Unicode（B40）：unverified。
- `MISSING` sentinel 在 FastAPI 產生的 JSON Schema 中如何呈現（F22/F23）：unverified。
- FastAPI 的 `responses={... "model": ..., "content": {"application/problem+json": ...}}` 實際產生的結構（F9）：unverified。
- openapi-typescript 7.13 對 3.1 `anyOf null` 的輸出（E6）、openapi-core 0.23 對 3.1 的支援（D3a）、FastAPI 內建 Swagger HTML 沒有 SRI（B54）：unverified。
- Pydantic 回報 `extra_forbidden` 的順序（B27）：unverified；現有測試對多筆錯誤用 `sorted`，不受影響。
