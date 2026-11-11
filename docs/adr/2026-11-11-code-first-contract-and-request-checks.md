---
title: 契約改由 FastAPI 產生（code-first），請求檢查順序固定在 route class，限流每個請求都扣
date: 2026-11-11
review-date: 2027-05-11
status: active
tags: [fullstack-template, decision, fastapi, rest, openapi]
---

# 契約改由 FastAPI 產生（code-first），請求檢查順序固定在 route class，限流每個請求都扣

## Context

[FastAPI 版後端](2026-11-05-fastapi-backend-stack.md) 的 D1、D2 選了手寫 `openapi.yaml` 為唯一來源：openapi-core 的 middleware 在每個 `/api` 請求進入 operation 前驗證，Pydantic 模型由 datamodel-code-generator 從契約產生。當時不選 code-first 的理由只有一個：FastAPI 產生的契約對不上 Go 版，前端要改，兩個範本的契約也不能直接比對。這個理由已不成立：Go 版範本刪掉了 REST 層與 OpenAPI 契約（template-go-nextjs `fd4cd51`），Will 確認不需要和 Go 版契約比對、前端可以改（2026-11-08）。

把驗證層拿掉時，三個原本依附在它上面的行為要重新決定，因為它們是那個 middleware 的實作順序帶來的，不是設計出來的：錯誤的回應順序（openapi-core 先查 security）、測試要不要對照契約驗證回應、被驗證擋下的請求扣不扣 per-IP 限流額度（限流是跑在驗證之後的 dependency，所以不扣）。

**約束**：

- `external`：範本沒有部署，也沒有從它產生的專案；前後端同一個 repo、同一次部署，契約可以一次切換。
- `external`：FastAPI 先讀並解析 body，再求解 dependency（FastAPI 0.162.0 實測：匿名送壞 JSON 回 422 `json_invalid`，先於 401）。要在讀 body 之前完成的檢查不能寫成 dependency。
- `external`：FastAPI 把 `responses=` 宣告的 model 一律寫在 `application/json` 底下，沒有逐一 status 指定 media type 的寫法；Python 的 `json.loads` 與 `pydantic_core.from_json` 接受 `NaN`（Go 的 `encoding/json`、JS 的 `JSON.parse` 都拒絕）。
- `inherited`：「和 Go 版一致」、「前端依賴現有的 status 與形狀」。兩條都不再成立（見上），不當理由。
- `inherited`：錯誤代碼用 `x-extensible-enum`（ADR api-and-auth-baseline D3）。仍成立：清單會增加，新增不該算破壞性變更；與契約由誰產生無關。

## Options Considered

- **契約來源**：基準是 FastAPI code-first，契約由 route 與 Pydantic 模型產生（[FastAPI 文件](https://fastapi.tiangolo.com/tutorial/first-steps/)；官方 full-stack-fastapi-template 匯出 `openapi.json` 產生前端 client，CI 在有差異時失敗）。另一個選項是維持手寫 `openapi.yaml` 加 openapi-core 驗證。
- **錯誤順序與請求格式**：
  - 基準：存取檢查在解析 body 之前。Spring Security 是 Servlet Filter（[architecture](https://docs.spring.io/spring-security/reference/servlet/architecture.html)）；DRF 的 `APIView.initial()` 依序跑 authentication、permissions、throttles，body 到 handler 讀取時才解析（[views.py](https://github.com/encode/django-rest-framework/blob/main/rest_framework/views.py)）；Kubernetes API server 是 authentication → authorization → admission → validation（[Controlling Access](https://kubernetes.io/docs/concepts/security/controlling-access/)）。格式：RFC 9110 §15.5.16（415）、RFC 5789 §2.2（不支援的 patch 格式回 415，SHOULD 帶 `Accept-Patch`）、RFC 8259（文法沒有 NaN，交換用 UTF-8）、RFC 9110 §15.5.21 與 GitHub REST API（壞 JSON 回 400，驗證失敗回 422）。
  - A：自訂 `APIRoute`（FastAPI 文件記載的擴充點，[Custom Request and APIRoute class](https://fastapi.tiangolo.com/how-to/custom-request-and-route/)），在讀 body 前依序檢查。
  - B：接受 FastAPI 原生順序，只用 exception handler 改寫 status。
  - C：放寬：不做 415，PATCH 也收 `application/json`，接受 NaN。
- **測試中的回應驗證**：基準是在測試對照產生的契約檢查 status、media type、header、body（[Schemathesis 的 conformance checks](https://schemathesis.readthedocs.io/en/stable/reference/checks/)）。A：保留 openapi-core，改對照產生的契約。B：全部拿掉，相信 code-first。C：改用 Schemathesis。
- **被擋下的請求是否扣限流額度**：基準是限流在解析與驗證之前計數，每個請求都算（Rails `rate_limit` 是 `before_action`，[rate_limiting.rb](https://github.com/rails/rails/blob/main/actionpack/lib/action_controller/metal/rate_limiting.rb)；DRF 的 `check_throttles`；ASP.NET Core 的 `UseRateLimiter` 排在 endpoint 之前，[middleware order](https://learn.microsoft.com/en-us/aspnet/core/fundamentals/middleware/)）。另一個選項是維持「只扣通過驗證的」。

## Decision

- **D1 契約來源**：code-first。契約由 route 宣告與各模組手寫的 `schemas.py`（Pydantic）產生，`make -C backend gen` 匯出成 `backend/api/openapi.json`（OpenAPI 3.1）進版控，`gen-check` 擋下過期的匯出；前端從它產生型別。取代 ADR fastapi-backend-stack D1、D2 與 ADR api-and-auth-baseline D2 的「spec-first 的 `openapi.yaml`」一條。細節：
  - `paths` 含 `/api` 前綴（router `prefix="/api"` 的原生產出），前端的 path 字串也含 `/api`；不為了去掉前綴而後處理契約。
  - 匯出 JSON，不加 pyyaml；dev 模式另由 `/api/openapi.json` 與文件頁提供，prod 回 404。
  - problem 回應的 schema 由一段 `app.openapi` 後處理搬到 `application/problem+json`；每個 route 宣告 RFC 9457 的 422，契約裡沒有 FastAPI 的 `HTTPValidationError`。
  - Pydantic 產生的 `title` 留在契約裡：它只進前端型別的 JSDoc 註解，型別不變。
- **D2 錯誤順序與請求格式**：選 A。`app.http.route.ApiRoute` 在 FastAPI 讀 body 之前依序檢查：access marker（401 未登入、403 角色不符，admin 也在這裡）→ 限流（429，D4）→ media type（415；PATCH 只收 `application/merge-patch+json`，415 帶 `Accept-Patch`）→ 嚴格 JSON（400：語法錯、尾端多餘資料、NaN／Infinity、非 UTF-8）；之後 Pydantic 驗證失敗回 422 `errors: [{pointer, code, detail}]`。整體順序 413 > 404/405 > 401/403 > 429 > 415 > 400 > 422：
  - 413 由獨立的 body 上限 middleware 負責，排在路由之前，所以未定義的路徑送過大的 body 也回 413（Will 2026-11-11：維持舊驗證層的這個順序，不改成 404/405 先）。
  - route class 依 route 的 marker 與 body，自動把它負責的 status 寫進契約。
  - 匿名請求一律回 `code: session_invalid` 的 401，不保留舊驗證層那個沒有 `code` 的 401。
- **D3 測試中的回應驗證**：選 A。openapi-core 只留作測試依賴；`backend/tests/apitest.py` 的 `checked_client` 對照產生的契約檢查每個測試回應的 status、media type、header 與 body。problem 改由 Pydantic 的 `Problem` model 建構。Schemathesis 延後。
- **D4 限流扣額**：每個打到限流 operation 的請求都扣一個 token，含被 415、400、422 擋下的。`RATE_LIMITED` 是 `ApiRoute` 讀的 marker，位置在 access 檢查之後、內容檢查之前。額度與 bucket 不變（`AUTH_RATE_LIMIT_PER_MIN` 預設 5，註冊與登入共用）。

## Rationale

- **D1**：當初推離基準的理由消失後，手寫契約只剩成本：契約、產生的模型、route 三處要靠工具對齊，一個功能改兩個地方。代價：契約的形狀改由 Pydantic 與 FastAPI 的產出決定（3.1、`anyOf null`、`title`），升級它們可能改動契約，靠 `gen-check` 與 `api-breaking`（oasdiff）看見；要兩段 FastAPI 沒有原生寫法的程式（problem media type 的後處理、route class）；第一次切換時前端的 path 全部要改。
- **D2**：不選 B：匿名送壞 JSON 會先於 401 得到內容錯誤，415 只能從 Pydantic 的錯誤型別猜，NaN 照收。不選 C：merge-patch 的 `null` 代表清空，和一般 JSON 語意不同，模糊的語意會被每個衍生專案繼承。和舊行為相比改了兩點：403 提前到內容檢查之前（原本非 admin 送錯 body 先拿到 422），415 補上 `Accept-Patch`。代價：多一個自訂 route class 要維護，每個 `/api` route 都必須用它（`app.main` 啟動時檢查），它讀 FastAPI 沒有列入文件的內部結構（route 的 `dependant`、`body_field`），升級 FastAPI 時先跑 `test_routes.py` 與 `test_validation.py`；access marker 仍是 dependency（契約的 `security` 與交給 route 的 principal 靠它），所以每個請求跑兩次，`RATE_LIMITED` 只是 route class 讀的標記，由 `include_router` 加上的不生效（啟動檢查拒絕）；body 上限 middleware 在路由與 access 之前把 body 讀進記憶體（上限 1 MiB），匿名與未定義路徑的請求也一樣，讀到一半斷線的請求記成 500；加自動宣告的 status 時 route 會建兩次，每個 app 啟動多約 20 ms（實測）。
- **D3**：契約與回應出自同一份 model，只讓成功 body 的檢查變成重複；錯誤 body、沒宣告的 status、media type 與 header 是另外宣告的，仍會漂移，FastAPI 執行期不檢查。不選 B：只有 status 能從型別推導時才成立（ASP.NET 的 `TypedResults`），FastAPI 沒有。不選 C：它產生新的輸入，取代不了對既有測試每個回應的檢查，而且是新依賴。代價：openapi-core 留在開發依賴裡，並要為 `application/problem+json` 註冊 deserializer。
- **D4**：per-IP 限流管的是某個位址打這些 operation 的總量；猜密碼的嘗試次數另由 `sign_in_throttles` 依帳號與來源節流（ADR account-abuse-and-lifecycle D4）。舊規則也不一致：契約層的 422 不扣，service 層的 422、409 會扣。代價：註冊時連續打錯的合法使用者會用掉額度（前端不重複檢查長度，422 是正常流程），例如 4 次 422 加 1 次成功後，一分鐘內登入會拿到 429。沒有流量資料可以量這件事。

## Expected Outcome

- 改了 `schemas.py` 或 route 卻沒有重新產生契約時，`make -C backend check` 失敗；前端型別沒跟上時 `pnpm gen:api:check` 失敗。
- 匿名或角色不符的請求，不論 body 內容，一律得到 401 或 403。
- 測試裡任何沒宣告在契約的 status、media type 或 problem 形狀，讓該測試失敗。

## Invariants

- 契約只有一份來源，`backend/api/openapi.json` 是它的匯出：`make -C backend gen-check`、`frontend/scripts/gen-api.mjs`、`pnpm gen:api:check`、`make -C backend api-breaking`。
- 每個 `/api` route 是 `ApiRoute`、帶剛好一個 access marker、body 用 `REQUEST_CONFIG`：`app.main` 的 `check_routes`（啟動時）、`backend/tests/test_routes.py`。
- 錯誤順序：`backend/tests/test_validation.py`；每個回應符合契約：`backend/tests/apitest.py` 的 `checked_client`。

## Revocation Triggers

- FastAPI 提供原生的「讀 body 之前」的 dependency，或逐 media type 的嚴格模式 → 改用原生做法，刪掉 route class（D2）。
- 有非瀏覽器的 client 需要寬鬆的 Content-Type → 重新評估 D2 的選項 C。
- 衍生專案上線後合法使用者因 422 撞到 429 → 調額度或拆成每個 operation 一個 bucket，不改計數規則（D4）。Will 把 per-IP 限流改定義為「嘗試次數」→ 改成在 Pydantic 驗證之後扣。
- `checked_client` 與手寫測試沒抓到契約漂移，或 openapi-core 對 3.1 出現誤判 → 加 Schemathesis 或改用 jsonschema 直接驗證（D3）。
- 測試或啟動時間成為問題 → 重做 route class 自動宣告 status 的方式（D2）。
- 第一個正式部署接上錯誤追蹤，或 body 上限要調高 → body 上限改成邊讀邊數、不預先緩衝，斷線改記成不帶 stack 的 warning（D2）。
- 衍生專案上線後，改 401／415／400 的順序或 status 是破壞性變更：走 `api-breaking` 的放行流程（`docs/rules/api-design.md`「契約穩定性」）。

## Review Notes

## Related

<!-- >>> template-only -->
- 來源：code-first 計畫 `docs/plans/2026-11-08-fastapi-code-first.md` 的決定表（契約來源、錯誤順序、`/api` 前綴、匿名 401、回應驗證、限流扣額；Will 2026-11-08 拍板，413 的位置與 `title` 2026-11-11 拍板），量測在 `docs/plans/2026-11-08-fastapi-code-first/spike.md`，其他 FastAPI 範本的做法在同目錄的 `survey-fastapi-templates.md`。三題的業界基準簡報已併入本 ADR，原檔在 commit `fdbf158` 的同一個目錄，檔名 `brief-d2-d5-d6.md`。
<!-- <<< template-only -->
- 部分取代：[FastAPI 版後端](2026-11-05-fastapi-backend-stack.md) 的 D1、D2；[API 與認證基準](2026-10-18-api-and-auth-baseline.md) D2 的契約來源一條。
- 規則：`docs/rules/api-design.md`「契約是唯一來源」、「認證」、「限流」、「契約穩定性」。
