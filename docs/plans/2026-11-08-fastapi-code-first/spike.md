# 步驟 1 spike：code-first 的未知（量測 2026-11-08）

環境：repo `6ddf1db`；FastAPI 0.162.0、Pydantic 2.14.1、Starlette 1.10.0、openapi-core 0.23.1（`backend/.venv`）；openapi-typescript 7.13.0（`frontend/node_modules`）；oasdiff 1.35.0（見 (f) 的版本說明）。原型在 `spike/`：`proto.py` 是一個獨立的 FastAPI app，形狀照範本（access marker、problem 回應、限流的公開 route、merge-patch 的 PATCH），不 import 產品程式碼；principal 用 `x-user` header 代替 AuthenticateMiddleware。下面每項的原始輸出在文末附錄。

所有指令從 repo 根目錄執行，`$S` 是任一暫存目錄（產物不進 repo）：

```bash
P=backend/.venv/bin/python; D=docs/plans/2026-11-08-fastapi-code-first/spike
$P $D/measure_order.py                       # (a) (b) D6
$P $D/measure_schema.py $S/openapi.json      # (c) (d) (f) 前半；寫出修正後的規格
$P $D/measure_openapi_core.py                # (e)
(cd frontend && node --input-type=module -e "$(cat ../$D/gen_types.mjs)" $S/openapi.json)   # (c) (f) 的 TS 輸出
$P $D/make_enum_specs.py $S/enum-specs       # (f) 後半的規格對
oasdiff breaking --fail-on ERR $S/enum-specs/<base>.json $S/enum-specs/<revision>.json
(cd frontend && pnpm test && pnpm gen:api:check)                                             # (g)
```

## 結論

| 項目 | 結果 | 停止條件 |
|---|---|---|
| (a) FastAPI 處理順序 | 量到：先解析 body，再求解依賴，最後回報參數與 body 的驗證錯誤。匿名送壞 JSON → **422 `json_invalid`**（先於 401）；匿名送合法 JSON 但 body 不合 schema → 401（依賴先丟）；`NaN` 被 JSON 解析接受（走到驗證才變 `string_type`）；非 UTF-8 → 400 `There was an error parsing the body`；沒有 Content-Type 或 `text/plain` → 422 `model_attributes_type`（沒有 415）；PATCH 送 `application/json` → 200（被接受）。前提段「先解析 body」那條由推論變成量到 | — |
| (b) custom `APIRoute` 的 401→415→400 | 量到：做得到。`OrderedRoute` 在呼叫 FastAPI 的 handler 前依序查 session（401）、宣告的 media type（415，`; charset` 不影響）、嚴格 JSON（400：壞 JSON、尾巴多字、`NaN`、頂層 `NaN`、非 UTF-8），之後才是 422；PATCH 送 `application/json` → 415，`merge-patch+json` → 200。對照（拿掉 route class，即 (a) 的那組）匿名壞 JSON 回 422，不是 401（計畫寫「400 而不是 401」，量到的是 422 `json_invalid`；重點「不是 401」成立） | 未觸發：D2 選 A 可行 |
| D6 的事實 | 量到：限流依賴照現行寫法（`dependencies=[RATE_LIMITED]`）時，422（缺欄位）**會**扣 token，壞 JSON 不扣（解析在依賴之前）；限流依賴自己宣告 body 參數（D6 的 B）時 422 不扣。用 `OrderedRoute` 時 400 與 415 都在依賴之前回，不扣 | — |
| (c) `pydantic.MISSING` 欄位 | 量到：`str \| MISSING = MISSING` 不進 `required`、型別不含 null（`{"type": "string", ...}`）；`str \| None \| MISSING` 是 `anyOf [string, null]` 且不進 `required`。openapi-typescript：`title?: string`、`description?: string \| null` | — |
| (d) `responses=` 宣告 problem | 量到：**`{"model": Problem, "content": {"application/problem+json": {}}}` 會把 schema 放在 `application/json`，`application/problem+json` 留空**（FastAPI 把 additional response 的 model 寫到 route 成功回應的 media type，`fastapi/openapi/utils.py:491`）；FastAPI 沒有逐一 status 指定 media type 的原生寫法。原型用 `problem_media_type_only()` 掛在 `app.openapi` 上把 schema 搬到 `application/problem+json`，修正後只剩一個 media type。另外：route 有參數或 body 卻沒宣告 422／`4XX`／`default` 時，FastAPI 會自動加 `HTTPValidationError`（`utils.py:507-525`）；原型每個 route 都宣告了 422，產出裡沒有 `HTTPValidationError`。PATCH 的 requestBody 只列 `application/merge-patch+json` | — |
| (e) openapi-core 驗證 3.1 回應 | 量到：可以，7 個案例全照預期。合法 Item（含 `description: null`，3.1 的 `anyOf null`）接受；缺必填欄位拒絕；未宣告的 500 拒絕（`ResponseNotFound`）；problem 用 `application/json` 送拒絕；problem 缺 `status` 拒絕；`code` 不在 x-extensible-enum 清單內接受（擴充欄位不參與驗證，清單檢查仍要留在測試 helper，即附件 B43）。前提：`application/problem+json` 要像 `app.web.contract` 現在一樣註冊 `extra_media_type_deserializers`，否則被當 bytes 而全部拒絕（第一次跑就是這樣，見附錄） | 未觸發：D5 選 A 可行 |
| (f) x-extensible-enum | 量到：`json_schema_extra` 產出 `"x-extensible-enum": [...]`；gen-api 的 transform 產出 `code?: "session_invalid" \| "insufficient_role" \| "account_taken"`。`oasdiff breaking --fail-on ERR`：x-extensible-enum 加一個值 → `No breaking changes`、exit 0；對照組改成 `enum` 加一個值 → 12 個 `response-property-enum-value-added` error、exit 1；刪一個必填回應欄位 → 2 個 error、exit 1 | 未觸發：錯誤代碼維持 x-extensible-enum |
| (g) 前端基線 | 量到：`pnpm test` 8 個檔、42 passed（等於 `quality-locks.json` 的 frontend 42），exit 0；`pnpm gen:api:check` exit 0 | — |

## 對後續步驟的影響

- **步驟 3（oasdiff 版本）**：v1.36.0 發布於 2026-11-07T16:17Z（`gh release view v1.36.0 -R oasdiff/oasdiff`），到今天只有 1 天，不符合 repo 對依賴的 7 天規則（AGENTS.md Constraints、`exclude-newer`）。前一版 v1.35.0 發布於 2026-10-28，本 spike 用它（`oasdiff_1.35.0_darwin_all.tar.gz` sha256 `964ad2c0331768c4515fc49779827094bc89a65b1d309dac28886d1c3faf305c`，與 release 的 `checksums.txt` 相符），只下載到暫存目錄。步驟 3 的版本要重新核准。
- **步驟 4a**：要多一段掛在 `app.openapi` 的後處理（problem 的 schema 搬到 `application/problem+json`），`build_openapi()` 與 dev 的 `/api/openapi.json` 共用；每個有參數或 body 的 route 都要宣告 422 problem，否則規格裡會出現 `HTTPValidationError`（可在 4a 的測試裡斷言 components 沒有它）。D4 選 B 的缺點之一是「多一段覆寫 `app.openapi` 的程式」，這段覆寫現在不論 D4 怎麼選都要有；D4 選 A 的主要理由（不必改寫 paths）不受影響。
- **步驟 4b 的寫法**：嚴格 JSON 用 FastAPI 文件記載的 custom `Request` 子類別（覆寫 `json()`，FastAPI 之後呼叫 `request.json()` 拿到快取值），不碰 Starlette 的私有屬性；需要 session 的判斷用 `route.dependant.dependencies` 裡有沒有 marker；宣告的 media type 讀 `route.body_field.field_info.media_type`（沒寫 `Body(media_type=...)` 時是 `application/json`）。

## 附錄：原始輸出

### (a) (b) D6：`measure_order.py`（exit 0）

```
== route class: APIRoute
422  anon, bad JSON                              json_invalid
401  anon, valid JSON, invalid body              no valid session
401  anon, text/plain                            no valid session
422  signed in, bad JSON                         json_invalid
422  signed in, trailing data                    json_invalid
422  signed in, NaN                              string_type
422  signed in, top-level NaN                    model_attributes_type
400  signed in, non-UTF-8                        There was an error parsing the body
422  signed in, no Content-Type                  model_attributes_type
422  signed in, text/plain                       model_attributes_type
201  signed in, json; charset=utf-8              
422  signed in, {} (missing title)               missing
200  signed in, PATCH application/json           
200  signed in, PATCH merge-patch                
200  signed in, PATCH merge-patch; charset       
422  anon, PATCH merge-patch bad JSON            json_invalid
404  trailing slash /api/items/                  Not Found
201  /api/sessions    valid                  token taken: 1
422  /api/sessions    422 (missing field)    token taken: 1
422  /api/sessions    bad JSON               token taken: 0
201  /api/sessions-b  valid                  token taken: 1
422  /api/sessions-b  422 (missing field)    token taken: 0
422  /api/sessions-b  bad JSON               token taken: 0
== route class: OrderedRoute
401  anon, bad JSON                              no valid session
401  anon, valid JSON, invalid body              no valid session
401  anon, text/plain                            no valid session
400  signed in, bad JSON                         request body is not valid JSON
400  signed in, trailing data                    request body is not valid JSON
400  signed in, NaN                              request body is not valid JSON
400  signed in, top-level NaN                    request body is not valid JSON
400  signed in, non-UTF-8                        request body is not valid JSON
415  signed in, no Content-Type                  accepted Content-Type: application/json
415  signed in, text/plain                       accepted Content-Type: application/json
201  signed in, json; charset=utf-8              
422  signed in, {} (missing title)               missing
415  signed in, PATCH application/json           accepted Content-Type: application/merge-patch+json
200  signed in, PATCH merge-patch                
200  signed in, PATCH merge-patch; charset       
401  anon, PATCH merge-patch bad JSON            no valid session
404  trailing slash /api/items/                  Not Found
201  /api/sessions    valid                  token taken: 1
422  /api/sessions    422 (missing field)    token taken: 1
400  /api/sessions    bad JSON               token taken: 0
201  /api/sessions-b  valid                  token taken: 1
422  /api/sessions-b  422 (missing field)    token taken: 0
400  /api/sessions-b  bad JSON               token taken: 0
```

### (c) (d) (f)：`measure_schema.py`（exit 0）

```
openapi: 3.1.0
paths: ['/api/items', '/api/items/{id}', '/api/sessions', '/api/sessions-b']

(c) ItemPatch: {
 "properties": {
  "title": {
   "type": "string",
   "maxLength": 200,
   "minLength": 1,
   "title": "Title"
  },
  "description": {
   "anyOf": [
    {
     "type": "string"
    },
    {
     "type": "null"
    }
   ],
   "title": "Description"
  }
 },
 "additionalProperties": false,
 "type": "object",
 "title": "ItemPatch"
}

(c)(f) Problem: {
 "properties": {
  "type": {
   "type": "string",
   "title": "Type"
  },
  "title": {
   "type": "string",
   "title": "Title"
  },
  "status": {
   "type": "integer",
   "title": "Status"
  },
  "detail": {
   "type": "string",
   "title": "Detail"
  },
  "code": {
   "type": "string",
   "title": "Code",
   "x-extensible-enum": [
    "session_invalid",
    "insufficient_role",
    "account_taken"
   ]
  }
 },
 "type": "object",
 "required": [
  "type",
  "title",
  "status"
 ],
 "title": "Problem"
}

(d) POST /api/items responses: {
 "201": {
  "description": "Successful Response",
  "content": {
   "application/json": {
    "schema": {
     "$ref": "#/components/schemas/Item"
    }
   }
  }
 },
 "401": {
  "description": "unauthenticated",
  "content": {
   "application/problem+json": {},
   "application/json": {
    "schema": {
     "$ref": "#/components/schemas/Problem"
    }
   }
  }
 },
 "415": {
  "description": "unsupported-media-type",
  "content": {
   "application/problem+json": {},
   "application/json": {
    "schema": {
     "$ref": "#/components/schemas/Problem"
    }
   }
  }
 },
 "422": {
  "description": "validation",
  "content": {
   "application/problem+json": {},
   "application/json": {
    "schema": {
     "$ref": "#/components/schemas/Problem"
    }
   }
  }
 }
}

(d) PATCH requestBody: {
 "required": true,
 "content": {
  "application/merge-patch+json": {
   "schema": {
    "$ref": "#/components/schemas/ItemPatch"
   }
  }
 }
}

(d) leftover FastAPI schemas: []

(d) with problem_media_type_only, POST /api/items 401: {
 "description": "unauthenticated",
 "content": {
  "application/problem+json": {
   "schema": {
    "$ref": "#/components/schemas/Problem"
   }
  }
 }
}
```

### (c) (f)：`gen_types.mjs` 的輸出節錄（exit 0）

```ts
        ItemPatch: {
            /** Title */
            title?: string;
            /** Description */
            description?: string | null;
        };
        /** Problem */
        Problem: {
            /** Type */
            type: string;
            /** Title */
            title: string;
            /** Status */
            status: number;
            /** Detail */
            detail?: string;
            /** Code */
            code?: "session_invalid" | "insufficient_role" | "account_taken";
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description unsupported-media-type */
--
```

### (e)：`measure_openapi_core.py`（exit 0）

```
openapi: 3.1.0
ok   201 valid Item                           accepted
ok   201 Item without title                   rejected: InvalidData: InvalidData: Value {'id': '00000000-0000-0000-0000-000000000001', 'description': None} not
ok   401 valid problem                        accepted
ok   401 problem without status               rejected: InvalidData: InvalidData: Value {'type': '/problems/unauthenticated', 'title': 'u'} not valid for schem
ok   401 problem as application/json          rejected: DataValidationError: DataValidationError: Content for the following mimetype not found: application/json. Valid
ok   500 undeclared status                    rejected: ResponseNotFound: Unknown response http status: 500
ok   401 code outside the list (extensible)   accepted
unexpected outcomes: 0
```

第一次跑（沒有註冊 `application/problem+json` 的 deserializer，exit 1）的兩行：

```
BAD  401 valid problem                        rejected: InvalidData: InvalidData: Value b'{"type": "/problems/unauthenticated", "title": "u", "status": 401, "c
BAD  401 code outside the list (extensible)   rejected: InvalidData: InvalidData: Value b'{"type": "/x", "title": "u", "status": 401, "code": "new_code"} not 
```

### (f)：oasdiff 1.35.0（`$S` 為暫存目錄）

```
$ oasdiff breaking --fail-on ERR $S/enum-specs/base.json $S/enum-specs/ext-added.json
No breaking changes to report, but the specs are different.
Run 'oasdiff diff' to see structural differences.
exit=0

$ oasdiff breaking --fail-on ERR $S/enum-specs/enum-base.json $S/enum-specs/enum-added.json
12 changes: 12 error, 0 warning, 0 info
error	[response-property-enum-value-added] at $S/enum-specs/enum-added.json
	in API POST /api/items
		added the new `new_code` enum value to the `code` response property for the response status `401`
		The server may now return a value the previous contract excluded, so a client written against it may not handle the response. If the value set is meant to grow, declare it with x-extensible-enum.

error	[response-property-enum-value-added] at $S/enum-specs/enum-added.json
...（其餘 55 行同型）
exit=1

$ oasdiff breaking --fail-on ERR $S/enum-specs/base.json $S/enum-specs/field-removed.json
2 changes: 2 error, 0 warning, 0 info
error	[response-required-property-removed] at $S/enum-specs/field-removed.json
	in API POST /api/items
		removed the required property `title` from the response with the `201` status

error	[response-required-property-removed] at $S/enum-specs/field-removed.json
	in API PATCH /api/items/{id}
...（其餘 3 行同型）
exit=1

```

### (g)：前端基線

```

 Test Files  8 passed (8)
      Tests  42 passed (42)
   Start at  23:34:23
   Duration  2.67s (environment 70%, setup 17%, tests 5%, import 4%, transform 3%)

exit=0
$ node scripts/gen-api.mjs --check
exit=0
```
