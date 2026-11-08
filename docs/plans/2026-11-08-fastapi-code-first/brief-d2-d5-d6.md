# 簡報：改成 code-first 之後，錯誤優先序、測試裡的 response 驗證、限流扣額怎麼做

這份簡報涵蓋三個決策。D2 決定錯誤的回應順序，以及 request 格式要多嚴格；D5 決定測試要不要對照規格驗證 response；D6 決定被驗證擋下的 request 要不要扣 per-IP 限流額度。

證據標記：**〔量〕** 表示本次或 spike 實際跑過；**〔源〕** 表示讀了原始碼或文件，附 `檔:行` 或 URL；**〔推〕** 表示推論，沒有驗證。

## 0. 結論

| 決策 | 推薦（終態） | 和現在的差別 |
|---|---|---|
| D2 | 寫一個 `ApiRoute`（自訂的 FastAPI `APIRoute` 子類，掛在每個 `/api` router 上），在 FastAPI 讀 body **之前**依序檢查：存取權（401／403）→ 限流（429）→ Content-Type（415；PATCH 只收 `application/merge-patch+json`，415 回應帶 `Accept-Patch`）→ 嚴格 JSON（400）。之後交給 Pydantic 驗證，失敗回 422，body 是 RFC 9457 的 `errors: [{pointer, code, detail}]`。這個 route class 也負責把它產生的 401、403、415、400、422、429 自動寫進 OpenAPI | 保留 401 先於內容檢查、415、merge-patch、嚴格 JSON 與 400／422 的分法。三點修正：403 也提前到內容檢查之前（現在非 admin 送錯 body 會先拿到 422）；415 補上 `Accept-Patch` header；413（body 太大）改由一個獨立的 ASGI middleware 負責 |
| D5 | 保留 `checked_client`：用 openapi-core 對照**產生出來的** 3.1 規格，驗證每個 response 的 status、media type、header 與 body。problem body 改用 Pydantic 的 `Problem` model 建構，在產生的當下就保證形狀 | 驗證機制不變，只換驗證對象：手寫 YAML 改成產生的規格。problem 從手組的 dict 改成用 model 建構 |
| D6 | **限流額度改成每個 request 都扣**：限流在存取檢查之後、內容檢查之前執行，被 415、400、422 擋下的 request 也扣一個 token | 改變現行規則（現在被驗證擋下的不扣）。這條規則來自 Go 版的 middleware 順序，不是刻意設計出來的 |

## 1. 前提問題：要改的程式碼是否已排定重寫、搬移或刪除？

- **要刪的程式碼**：`backend/src/app/web/validation.py`（266 行）和 `backend/src/app/web/contract.py`（227 行），共 493 行〔量：`wc -l`〕。刪除是已定案的決策（brief 的前提）。這三個決策決定的是**取代它們的東西要做什麼**，沒有任何選項是在一段之後要丟掉的程式碼上加功能。所以不存在「會被丟掉的工作」。
- **會保留但要改的程式碼**：`access.py`（71 行）、`ratelimit.py`（107 行）、`tests/apitest.py`（265 行），以及 `docs/rules/api-design.md` 的「契約是唯一來源」、「認證」（第 134 行）、「限流」（第 153 行）三節〔量〕。這三節都寫著「契約驗證／validator」，不論怎麼選，這次遷移都得重寫，所以改規則文件不算額外成本。
- **從舊設計繼承下來的前提**：
  - `validation.py:4`、`ratelimit.py:1-4` 的 docstring 都以「as in the Go version」作為理由〔源〕。Go 版範本本身已經刪掉 REST 層與 OpenAPI 契約（`template-go-nextjs` commit `fd4cd51 refactor(backend): delete the REST layer and the OpenAPI contract`）〔量：`git log`〕，對照的對象已經不存在。owner 也在 2026-11-08 確認不再要求和 Go 版一致。**分類：SELF-IMPOSED，前提已不成立。**
  - 「401 先於驗證」的來源是 validator 的實作：openapi-core 先查 security，失敗就 `return`，不再看參數和 body（`openapi_core/validation/request/validators.py:112-117`）〔源〕。這是工具的行為，不是需求推導出來的。下文會說明業界基準獨立得出同樣的順序，所以這條是**依它本身的道理保留**，不是因為它本來就在。
  - 「被驗證擋下的不扣額度」純粹是 middleware 疊放順序的副作用（`ratelimit.py:3-4`：「A dependency runs after ContractValidation」）〔源〕。**SELF-IMPOSED。**

## 2. 術語

- **pre-body**：FastAPI 讀取並解析 request body 之前的階段。依賴（dependency）都在 body 解析**之後**才執行（`fastapi/routing.py:450-511`：先 `await request.body()`／`request.json()`，再呼叫 `solve_dependencies`）〔源＋量 spike (a)〕。所以凡是要在 body 解析前完成的檢查，都不能寫成 dependency。
- **custom `APIRoute`**：FastAPI 官方文件記載的擴充點。覆寫 `get_route_handler()` 之後，就能在原本的 handler 之前執行自己的程式碼，也能換掉 `Request` 物件（[FastAPI: Custom Request and APIRoute class](https://fastapi.tiangolo.com/how-to/custom-request-and-route/)）〔源〕。
- **x-extensible-enum**：可擴充的列舉清單。新增值不算 breaking change（breaking change：會讓既有 client 壞掉的改動）。依已接受的 ADR 保留。

---

## 3. D2：錯誤優先序與 request 格式嚴格度

**推薦**：採用 `ApiRoute` 的 pre-body 管線，固定順序如下：
404／405（Starlette 路由）→ 413（ASGI middleware）→ 401／403（存取權）→ 429（限流）→ 415 → 400 → 422（Pydantic）→ handler。

### 3.1 業界基準（逐一列出）

| 子行為 | 業界怎麼做（附來源） | 判斷 |
|---|---|---|
| **存取權先於內容** | Spring Security 是 Servlet Filter，在 `DispatcherServlet` 解析 body 之前執行（[Spring Security architecture](https://docs.spring.io/spring-security/reference/servlet/architecture.html)）〔源〕。ASP.NET Core 的 `UseAuthentication`／`UseAuthorization` 排在 endpoint 之前（[ASP.NET Core middleware order](https://learn.microsoft.com/en-us/aspnet/core/fundamentals/middleware/)）〔源；model binding 發生在 endpoint 內，這點是〔推〕〕。DRF 的 `APIView.initial()` 依序執行 authentication、permissions、throttles，`request.data` 要等 handler 存取時才會解析（[views.py](https://github.com/encode/django-rest-framework/blob/main/rest_framework/views.py)、[request.py](https://github.com/encode/django-rest-framework/blob/main/rest_framework/request.py)）〔源〕。Kubernetes API server 依序是 authentication（失敗回 401）→ authorization → admission → validation（[Controlling Access](https://kubernetes.io/docs/concepts/security/controlling-access/)）〔源〕 | 一致：**authn 與 authz 都在解析 body 之前**。保留，並把 403 也算進來 |
| **415** | RFC 9110 §15.5.16 為「不支援的 content 格式」定義了 415。DRF 拋 `UnsupportedMediaType`（`request.py` 的 `_parse`）〔源〕。FastAPI 原生遇到沒有 Content-Type 或 `text/plain` 時回 **422 `model_attributes_type`**〔量 spike (a)〕，在這些框架裡是唯一的例外 | 保留 415 |
| **PATCH 只收 merge-patch** | RFC 5789 §2.2：不支援的 patch 格式回 415，而且「SHOULD include an Accept-Patch response header」〔源〕。Kubernetes 的 PATCH 依 Content-Type 選擇語意（merge-patch、json-patch、strategic、apply），每一種都有自己的 media type（[API concepts](https://kubernetes.io/docs/reference/using-api/api-concepts/)）〔源；它對 `application/json` 回什麼 status，文件沒寫，未驗證〕。Azure 的指引寫「DO create and update resources using PATCH [RFC 5789] with JSON Merge Patch」（[Azure guidelines](https://github.com/microsoft/api-guidelines/blob/vNext/azure/Guidelines.md)）〔源〕。FastAPI 原生接受任何 `+json`，PATCH 送 `application/json` 會得到 200〔量〕 | 保留嚴格做法，**補上 `Accept-Patch`**（現在沒有：`validation.py:176-178` 只寫在 detail 裡）〔源〕 |
| **嚴格 JSON（NaN、尾端多餘資料、非 UTF-8）回 400** | RFC 8259 的文法裡沒有 NaN／Infinity，§8.1 規定交換時必須用 UTF-8。Go 的 `encoding/json` 和 JS 的 `JSON.parse` 都拒絕 `NaN`；Python 的 `json.loads` 與 `pydantic_core.from_json` 預設都**接受**〔量：本次 `go run`、`node -e`、`python -c`〕。FastAPI 原生把壞 JSON 當成 422 `json_invalid`，`NaN` 則通過解析〔量 spike (a)〕 | 保留。Python 在這裡是例外，修正方式是自訂 `Request.json()`（加上 `parse_constant` 拒絕） |
| **400（語法錯）和 422（語意錯）分開** | RFC 9110 §15.5.21 對 422 的定義是「syntax … is correct, but unable to process」。GitHub 對壞 JSON 回 400 "Problems parsing JSON"，對驗證失敗回 422（[GitHub troubleshooting](https://docs.github.com/en/rest/using-the-rest-api/troubleshooting-the-rest-api)）〔源〕。另一派是 Spring、ASP.NET `[ApiController]`、DRF、Google AIP-193（INVALID_ARGUMENT 對應 400），兩種錯誤都回 400〔推：依各框架慣例，本次未逐一抓文件〕 | 兩派都合規。保留分開：client 可以用 status 區分「程式送錯格式」（bug）和「使用者要改輸入」，而 FastAPI 原生用的也是 422 |
| **422 的 body 形狀 `{pointer, code, detail}`** | RFC 9457 §3 的範例正是 `"errors": [{"detail", "pointer"}]`〔源〕。GitHub 的 errors 也帶 `code`〔源〕。FastAPI 原生回 `{"detail": [...]}`（`fastapi/exception_handlers.py:20-26`），其中 Pydantic 的錯誤項目帶 `input`，也就是會回顯輸入值，可能包含密碼〔源〕 | 保留。用 `RequestValidationError` 的 handler 把 Pydantic 的錯誤 type 對應成 code（現在的 `errors.py:84-99` 已有雛形，但只分 `required` 和 `invalid_type`，需要補 `too_short`、`too_long`、`pattern`、`additional_property`、`null_not_allowed`、`out_of_range` 等） |

### 3.2 現有機制為什麼存在

ADR `2026-10-18-api-and-auth-baseline.md` 的 D2 與 D3 選了 RFC 9457、merge-patch，以及由 validator 執行的 spec-first 契約。401 先行是 openapi-core 的順序（見 §1）。`api-design.md:134` 把它寫成規則，但沒寫理由〔源〕。415 和嚴格 JSON 是 `contract.py:38-52` 的 `strict_json` 加上 openapi-core 的 media type 比對〔源〕。所以現有行為**大多是對的**（和業界基準一致），只是它們依附在要刪掉的 validator 上，理由也寫成「和 Go 版一樣」。

### 3.3 選項

| 選項 | 內容 | 失敗條件（遇到什麼情況這個選項就錯了） |
|---|---|---|
| **A. `ApiRoute` pre-body 管線（推薦）** | 一個 route class 依序做 access → ratelimit → 415 → 400，再交給 FastAPI。spike 量過可行：匿名送壞 JSON 回 401、`NaN` 回 400、PATCH 送 json 回 415〔量 spike (b)〕。同一個 class 讀 route 的 marker，自動把 401、403、415、400、422、429 加進 OpenAPI 的 `responses` | FastAPI 改掉 `get_route_handler` 的契約。這是官方文件記載的擴充點，風險低〔源〕；萬一發生，要改的只有一個 class |
| B. 接受 FastAPI 原生行為，只用 exception handler 改寫 status | 讓 `json_invalid` 改回 400；把「body 根部 `model_attributes_type` 而且 Content-Type 不是 JSON」猜成 415 | 匿名送壞 JSON 仍然先回 400 而不是 401。415 靠猜錯誤 type，Pydantic 換訊息就會壞〔推〕。`NaN` 仍然會被接受 |
| C. body 宣告成 raw `Request`，在 handler 裡自己解析 | 完全掌控 | 失去 code-first 的 requestBody schema，等於推翻已定案的遷移，不成立 |
| D. 放寬：不做 415、PATCH 也收 json、接受 NaN（GitHub 式寬鬆） | 程式最少 | merge-patch 的語意（`null` 代表刪除）和一般 JSON 不同，`application/json` 沒有 patch 語意（RFC 5789／7396）。模糊不清的語意會被每個衍生專案繼承 |

**403 的細項**：推薦 access 檢查跑完整的 marker（`PUBLIC`／`SIGNED_IN`／`ADMIN`，`access.py:36-61`），讓「存取權先於內容」成為一條沒有例外的規則（Spring、ASP.NET、DRF、K8s 都是 authz 也先於 body）。現在 admin 的 operation 會讓非 admin 先拿到 422，因為 validator 跑在 dependency 之前（`factory.py:189-190`）〔源〕。這是同一機制的另一個暴露面，順便修掉。

**推翻推薦的事實**：如果 FastAPI 提供原生的「pre-body 依賴」或「per-route media type 嚴格模式」（目前只有全域的 `strict_content_type`，見 `fastapi/routing.py:411,461-471`，而且它回的是 422 不是 415）〔源〕，就改用原生做法，刪掉 route class。

**可逆性**：route class 是單一檔案，刪掉就回到 FastAPI 原生。現在沒有任何部署，也沒有衍生專案，改 status 不用付出代價。等衍生專案上線之後，再改 401／415／400 的順序或 status 就會變成 breaking change，屆時要走 oasdiff 檢查與版本說明。

---

## 4. D5：測試裡的 response 驗證

**推薦**：保留「每個測試 response 都對照規格」，對照對象換成產生出來的 3.1 規格。problem body 改由 Pydantic `Problem` model 建構。

### 4.1 規格由同一份 model 產生之後，驗證還有沒有用？逐類看

| 類別 | Pydantic／FastAPI 在執行期會擋嗎？ | 測試驗證的價值 |
|---|---|---|
| 成功 body | 會。route 的回傳型別就是 `response_model`，FastAPI 會驗證並序列化（例：`items/router.py:39,49,57,62` 的 `-> api.Item`）〔源〕 | 大多重複。剩下的價值是抓 model 和它的 JSON Schema 不一致的情況（`MISSING` sentinel、自訂 serializer），以及直接回 `Response` 而繞過 model 的 route（例：`items/router.py:73` 的 `-> Response`）〔推〕 |
| 錯誤 body | **不會**。`problem_response` 直接用 dict 組出 `JSONResponse`（`web/problems.py:127-128`）〔源〕 | 真正的缺口。治本做法是改用 model 建構，測試驗證當第二層 |
| 沒宣告的 status | **不會**。FastAPI 不檢查 handler 或 exception handler 回的 status 有沒有寫在 `responses=` 裡〔推，未在原始碼找到相關檢查〕 | 真正的缺口。規格裡的 status 由 `responses=` 另外宣告，實際送出的 status 則由 `AppError` 的 Kind 決定，兩者可能漂移 |
| media type 與 header | 不會。FastAPI 把 `responses=` 的 model 寫到 `application/json` 底下〔量 spike (d)〕 | 真正的缺口。openapi-core 也會驗證 response header（`openapi_core/validation/response/validators.py:64-72`）〔源〕 |

結論：**「規格由同一份 model 產生」只讓成功 body 的驗證變成重複**。status、media type、problem body 都是另外宣告的，仍然可能漂移。

### 4.2 業界基準

- Schemathesis 的內建 check 包含 `status_code_conformance`（檢查沒寫在規格裡的 status）、`content_type_conformance`、`response_schema_conformance`、`response_headers_conformance`（[Schemathesis checks](https://schemathesis.readthedocs.io/en/stable/reference/checks/)）〔源〕。它正是針對「產生出來的規格」做 response 一致性測試的主流工具。
- ASP.NET Core 的 API analyzers 會在編譯期警告「Returns an undeclared status code」。.NET 10 起，這套 analyzer 改由 `TypedResults` 取代：把可能的 status 寫進回傳型別，規格由型別產生，得到 compile-time 保證（[API analyzers](https://learn.microsoft.com/en-us/aspnet/core/web-api/advanced/analyzers)）〔源〕。意思是業界的終點是**讓宣告的 status 從產生 status 的同一個機制推導出來**。
- Rails 的 committee（`assert_schema_conform`）、Java 的 Atlassian swagger-request-validator 都在測試裡驗證 response〔推：依慣例，未抓文件〕。在正式環境驗證 response 不是常態。

### 4.3 選項

| 選項 | 失敗條件 |
|---|---|
| **A. 保留 openapi-core 全量驗證，改對照產生的規格，problem 用 model 建構（推薦）** | spike 已量過可行（7 個案例全部符合預期）〔量 spike (e)〕。前提是要註冊 `application/problem+json` 的 deserializer，否則會全部被拒〔量〕。只有當 openapi-core 停止支援 3.1 時才會失敗 |
| B. 只驗 status、media type 和 problem，不驗成功 body | 拆成兩套規則反而多一個特例。省下的只有一次 validator 呼叫，不值得 |
| C. 全部拿掉（相信 code-first） | 沒宣告的 status 和 problem 形狀會漂移，沒有任何東西擋得住。只有在 status 能從型別推導（像 `TypedResults`）時才成立，FastAPI 沒有這個能力 |
| D. 改用或加上 Schemathesis | 需要新依賴，依 AGENTS.md 要先列出版本並等批准。它的價值是自動產生負面輸入，可以順便測 D2 的順序。可以之後再加，但不能取代 A：A 驗的是既有測試的每個 response |

**和 D2 的連動**：D2 推薦 `ApiRoute` 自動宣告它負責的 status（401、403、415、400、422、429），這樣框架層的 status 就由送出它的同一個機制寫進規格（相當於 `TypedResults` 的做法），D5 只需要抓 service 層的 status（404、409）。

**推翻推薦的事實**：openapi-core 驗證 3.1 時出現誤判（例如 `anyOf null` 以外的 3.1 關鍵字），改用 Schemathesis 或 jsonschema 直接驗證。**可逆性**：只影響測試，完全可逆，成本是改一個 helper。

---

## 5. D6：被驗證擋下的 request 要不要扣限流 token

**推薦**：每一個打到受限 operation 的 request 都扣一個 token。執行位置在 `ApiRoute` 的 access 檢查之後、415、400、422 之前。`RATE_LIMITED` 改成 route class 讀取的 marker，不再是 dependency。

### 5.1 業界基準

- Rails 的 `rate_limit` 註冊成 `before_action`，在 action 之前執行，先遞增計數再比對上限（[rate_limiting.rb](https://github.com/rails/rails/blob/main/actionpack/lib/action_controller/metal/rate_limiting.rb)）〔源〕。Rails 8 的 authentication generator 對 `sessions#create` 設 `rate_limit to: 10, within: 3.minutes`〔源：第三方整理，未對照 generator 原始碼〕。
- DRF 的 `check_throttles` 在 `initial()` 裡，比 `request.data` 的延遲解析更早，所以會被 400／415 擋下的 request 也會扣〔源〕。
- ASP.NET Core 的 `UseRateLimiter` 排在 `UseRouting` 之後、endpoint 之前（[middleware order](https://learn.microsoft.com/en-us/aspnet/core/fundamentals/middleware/)）〔源〕。

業界共同點：**限流在解析與驗證之前執行，每個 request 都算**。

### 5.2 現有機制為什麼存在

`ratelimit.py:3-4` 的理由是「A dependency runs after ContractValidation … as in the Go version」〔源〕，是 middleware 順序的副作用。它也不一致：同一個註冊流程裡，契約層的 422（密碼太短）不扣，service 層的 422 或 409（密碼太常見、帳號已被使用）卻會扣，因為它們在限流之後才發生（`auth/router.py:72-76`）〔源〕。

### 5.3 兩種限流的分工（關鍵論點）

- 「每個帳號加來源」的嘗試次數節流已經在 auth service 裡（`sign_in_throttles`，`api-design.md:155`）〔源〕。它的工作是限制猜密碼的**嘗試**。
- 所以 per-IP 限流的獨立價值是限制某個位址打這些 operation 的**總量**（大量註冊、單一來源灌爆）。依這個用途，被擋下的 request 也是量，應該計算。

### 5.4 選項

| 選項 | 內容 | 失敗條件 |
|---|---|---|
| **A. 全部扣，限流在內容檢查之前（推薦）** | 規則只有一句，沒有例外，位置也和每個 route 的 body 型別無關 | 合法使用者在註冊時連續打錯（422），把額度用完。現在的預設是 5 次／分鐘，而且**註冊和登入共用同一個 bucket**（`factory.py:133`，`app.state.ip_limiter` 只有一個）〔源〕。前端依 ADR D6 不重複檢查長度，所以註冊 422 是正常流程。例：4 次註冊 422 加 1 次成功，1 分鐘內再登入就會拿到 429〔推〕 |
| B. 只扣通過驗證的（現行語意） | 要在 Pydantic 驗證之後才執行，只能讓限流 dependency 宣告 body 參數（spike 量到這樣 422 就不扣）〔量 spike D6〕 | 每個受限 route 的限流都要綁它自己的 body 型別。service 層的 422 照樣會扣，不一致的問題還在 |
| C. 原生：限流當一般 dependency | 422 扣，壞 JSON 不扣〔量 spike D6〕 | 結果取決於框架的解析順序，不是一條設計出來的規則，不採用 |

**推翻推薦的事實**：如果 Will 把 per-IP 限流定義為「嘗試次數」而不是「總量」，B 才是對的。這種情況下應該改成在 `ApiRoute` 裡的 Pydantic 驗證之後扣，而不是讓 dependency 宣告 body。另外，若之後有資料顯示合法使用者因為 422 撞到 429，該調整的是**額度數值或拆成每個 operation 一個 bucket**，不是改計算規則。現在沒有使用資料（見限制表），所以 A 的失敗條件只能列為風險，無法量化。

**可逆性**：只改行為，`api-design.md` 的限流段要改一句。這次遷移本來就要改那段。沒有部署，所以沒有遷移成本。

---

## 6. 同一機制的其他暴露面（middleware 刪掉後也會失去）

- **413（body 上限 1 MiB）**：`validation.py:53-54, 98-103, 112-130`〔源〕。FastAPI 和 Starlette 沒有預設的 body 上限〔推〕。推薦用一個獨立的 ASGI middleware（類似 nginx 的 `client_max_body_size`），對所有路徑生效，排在 access 檢查之前。這屬於傳輸層的限制，先回 413 不會透露任何資訊。
- **404／405 的 problem 格式**：`errors.py:76-79` 已經處理 Starlette 的 `HTTPException`〔源〕，不需要新機制，但要有測試覆蓋 `/api` 路徑。
- **`additionalProperties: false` 回 422 `additional_property`**：改成 Pydantic 的 `extra="forbid"`，加上 §3.1 的 code 對應（`extra_forbidden` 對應 `additional_property`）。
- **`pattern` 的語意**：現在 `contract.py:55-132` 把 pattern 翻成 ECMA 語意。code-first 之後只剩 Pydantic 在執行（Rust regex），規格裡的 `pattern` 只是說明。只有在某個 client 也拿 pattern 做驗證時才會出現落差。目前前端依 ADR D6 不做這件事〔源 `api-design.md:7`〕，記為一個要觀察的點。

## 7. 限制表

| 限制 | REAL／SELF-IMPOSED | 來源 | 影響 |
|---|---|---|---|
| 和 Go 版一致 | SELF-IMPOSED，已不成立 | owner 2026-11-08；Go 範本已刪 REST 層（`fd4cd51`） | 不能當理由 |
| 前端與生成的 client 依賴現有的 status 和形狀 | SELF-IMPOSED | owner：前端可以改，沒有部署 | 不能當理由 |
| 和現有程式碼或文件一致（`api-design.md`、docstring） | SELF-IMPOSED | 這次遷移本來就要重寫 | 不能當理由 |
| 註冊和登入共用一個 bucket、額度 5 次／分鐘 | SELF-IMPOSED | `config.py:32`、`factory.py:133` | 可以調整，不左右 D6 的規則 |
| FastAPI 先解析 body 才執行 dependency | REAL（不歸我們管的依賴） | `fastapi/routing.py:450-511`、spike (a) | 逼出 route class，但不改變推薦的目標行為 |
| FastAPI 把 `responses=` 的 model 寫在 `application/json` | REAL | spike (d)、`fastapi/openapi/utils.py:491` | 需要一個 `app.openapi` 後處理（spike 已有原型），不改變推薦 |
| Python `json` 與 `pydantic_core` 接受 NaN | REAL | 本次量測 | 需要自訂 `Request.json()`，約數行 |
| openapi-core 需要註冊 `problem+json` deserializer | REAL | spike (e) | 一行設定 |
| 沒有實際流量資料（多少合法使用者會撞到 429） | REAL（缺資料） | 沒有部署 | D6 的額度數值無法用資料調整，只能列為推翻條件 |
| 新依賴需要批准（Schemathesis） | REAL（流程規則） | `AGENTS.md` Constraints | 只影響 D5 的選項 D |
| 錯誤代碼維持 x-extensible-enum | owner 確認的前提，不是推離基準的限制 | ADR D3 | 清單檢查留在測試 helper |

沒有任何 REAL 限制把推薦推離業界基準。三個決策的推薦都等於「基準，加上框架限制所需的實作調整」。

## 8. 終態與觸發

- **終態**：`app.web` 有一個 `ApiRoute`（access → ratelimit → media type → strict JSON，同時自動宣告它負責的 responses）、一個 body 上限 middleware、一個把 Pydantic 錯誤對應成 RFC 9457 的 handler、用 model 建構的 `Problem`，以及對照產生規格的 `checked_client`。`api-design.md` 用一張表寫明錯誤順序：404／405 > 413 > 401／403 > 429 > 415 > 400 > 422。
- **重新評估的觸發條件**：FastAPI 提供原生的 pre-body hook 或 per-media-type 嚴格模式；第一個衍生專案上線後出現 429 抱怨（調整額度，不改規則）；有非瀏覽器的 client 需要寬鬆的 Content-Type（重看 D2 的選項 D）。

## 9. 依據與剩下的不確定

依據：spike `docs/plans/2026-11-08-fastapi-code-first/spike.md` 的結論與附錄 (a)(b)(d)(e)，以及上文各處標註的 `file:line` 和 URL。本次量測：`go run`（Go 的 `encoding/json` 拒絕 NaN 與尾端多餘資料）、`node -e`（`JSON.parse` 拒絕 NaN）、`backend/.venv/bin/python -c`（`json.loads` 與 `pydantic_core.from_json` 都接受 NaN）。

還不確定的地方：
- ASP.NET 的 model binding 在授權之後、Spring 對 `application/json` 的 PATCH 怎麼處理、Kubernetes 對 `application/json` 的 PATCH 回什麼 status、Spring／ASP.NET／DRF／AIP 都用 400 的說法，這幾點是〔推〕或未抓到原文。
- Rails 8 generator 的數值來自第三方整理。
- 「FastAPI 不檢查沒宣告的 status」是推論，沒有找到明確的反證，也沒有找到明確的文件。
- route class 自動宣告 responses 的做法沒有實測（spike 只量了順序，沒量 OpenAPI 的自動宣告）。
