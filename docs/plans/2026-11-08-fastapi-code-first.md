# 後端改為 FastAPI code-first，套件改用業界命名

**Goal：** 契約由各模組的 Pydantic `schemas.py` 經 FastAPI 產生（OpenAPI 3.1），匯出到 `backend/api/openapi.json` 進版控並由 `gen-check` 守住；前端從它產生型別；openapi-core（D5：只留作測試依賴）、`contract.py`、`validation.py`、`routes.py`、`contract_models.py`、datamodel-codegen 移除，舊驗證層的對外行為逐項保留或依決定改變（附件 §1 每列有去向與測試）；套件為 `app.core`、`app.http`、`app.modules`、`app/main.py`、`app/cli/`、`app/migrations/`；`make -C backend api-breaking` 接進 `check`，CI 的 PR 與 push 以 `BASE` 比對（pre-push 用本機預設 `origin/main`，在 `dev`→`main` 合併前沒有可比的規格，只印說明）。驗收見步驟 8。

**上層：** 見本機個人脈絡檔（`AGENTS.local.md` 記的主清單）。

**附件：** `docs/plans/2026-11-08-fastapi-code-first/b-coverage.md`（步驟 4b：B1–B68 的去向與釘住的測試，`check_b_coverage.py` 檢查）；`inventory.md`（舊行為 B1–B68、契約特徵 F1–F36、引用點、前端耦合、測試；快照 `4dbe59f`；決定編號已對齊本檔）；`spike.md`（步驟 1）；D2、D5、D6 的業界基準簡報已併入 ADR `docs/adr/2026-11-11-code-first-contract-and-request-checks.md`（步驟 7；原檔 `git show fdbf158:docs/plans/2026-11-08-fastapi-code-first/brief-d2-d5-d6.md`）；`survey-fastapi-templates.md`（9 個 FastAPI 範本與大型應用的作法對照，2026-11-09）。

## 前提（開工時核對過，2026-11-08）

- 會改到或取代的既有部署是否在線：不在線。範本沒有正式部署（主清單的錯誤追蹤、備份演練都以「第一個正式部署」為觸發），也沒有從範本產生的衍生專案（`wiki/projects` 搜尋，審查者 2026-11-08）；前後端同一個 repo、同一次部署，契約一次切換，不需要相容性窗口。回滾就是 revert。
- 不需要和 Go 版契約比對、前端可以改（Will，2026-11-08，對話）。已確認。
- 改為 code-first 推翻 ADR `2026-11-05-fastapi-backend-stack` 的 D1、D2，以及 ADR `2026-10-18-api-and-auth-baseline` 的 D2（spec-first `openapi.yaml` 唯一來源，第 55、90、94 行）（Will，2026-11-08，對話：「併進重構計畫一起開工」）。ADR 修訂在步驟 7。
- revision 不可修改的檢查在範本本身不保護任何資料庫（範本沒有部署）；它存在是為了讓衍生專案繼承一個有效的閘門，範本持續執行它以證明它能運作。步驟 2 搬移 revision 目錄時直接改 `VERSIONS` 常數，不加新舊路徑對應（Will，2026-11-08）。
- 錯誤代碼維持 `x-extensible-enum`（ADR `2026-10-18-api-and-auth-baseline` D3、`docs/rules/api-design.md:103`「不要改回 enum」：新增代碼不算破壞性變更）。code-first 以 `json_schema_extra` 產出（附件 B61、F16–F18）。已確認（現行 ADR，未被推翻）。
- `origin/main` 只有 2026-11-03 的 Initial commit，落後 `dev` 110 個 commit，沒有 `backend/api/`（審查者實測 2026-11-08）。`api-breaking` 因此用 CI 既有的 `BASE`（PR 用 base 分支、push 用 `github.event.before`，`ci.yml:41-48`），不用 `origin/main`。已確認。
- oasdiff v1.36.0 能比對 FastAPI 產生的 OpenAPI 3.1：刪除必填回應欄位與選填欄位改必填共抓到 5 個 error，`--fail-on ERR` exit 1；有 `--err-ignore`，`--prefix-base /api` 能把不含前綴的舊規格與含 `/api` 的新規格對齊（實測：加旗標 `No changes`，不加則 21 個 endpoint 全成新增）；openapi-typescript 7.13.0 吃同一份 3.1 規格產出正常，`ItemPatch.title?: string` 不含 null（實測 2026-11-08，scratchpad 原型：不帶 Deps 的 `FastAPI()` include items、auth router 後 `app.openapi()` 產生 9 個 path）。已確認。
- FastAPI 0.162.0 先解析 body、再求解依賴、最後回報參數與 body 的驗證錯誤：匿名送壞 JSON 回 422 `json_invalid`（先於 401），送合法 JSON 但 body 不合 schema 回 401（步驟 1 實測 2026-11-08，附件 `spike.md` (a)）。已確認。
- openapi-core 0.23.1 能驗證 FastAPI 產生的 3.1 規格的回應：缺必填欄位、未宣告的 status、錯的 media type 都會拒絕；前提是 `application/problem+json` 註冊 deserializer（同現行 `app.web.contract`）（步驟 1 實測 2026-11-08，`spike.md` (e)）。已確認。
- FastAPI 把 `responses=` 裡 model 的 schema 寫到 route 成功回應的 media type（`application/json`），沒有逐一 status 指定 media type 的原生寫法（`fastapi/openapi/utils.py:491`）；route 有參數或 body 卻沒宣告 422 時自動加 `HTTPValidationError`（`utils.py:507-525`）（步驟 1 實測 2026-11-08，`spike.md` (d)）。已確認；做法寫進步驟 4a。
- oasdiff v1.36.0 發布於 2026-11-07，不滿 repo 對依賴的 7 天規則；v1.35.0 發布於 2026-10-28，步驟 1 的 (f) 用它量（`gh release list -R oasdiff/oasdiff`，2026-11-08）。已確認；Will 2026-11-08 改核准 v1.35.0。

**驗收基線：** `4dbe59f` 上 `make -C backend check` exit 0：354 passed、95 deselected（db 測試只在 CI 跑），squawk 0 issues，revision 未被修改；`scripts/quality-locks.json` 鎖 backend 354、backend_db 95。前端基線在步驟 1 補跑 `pnpm test`、`pnpm gen:api:check`。db 測試、compose stack 只在 CI 跑，CI 一次只跑一個。

## 全域約束

- commit 規則（AGENTS.md Verification）：改到 `scripts/check_commit_policy.py` 的 `GATE_FILES` 任一檔（含 `.importlinter`、`pytest.ini`、`scripts/check_migrations_immutable.py`）或往壞方向移動 lock 的 commit 帶 `Quality-Gate:` trailer；超過 1,000 行（`check_commit_policy.py` 以 `--no-renames` 計）的 commit 拆開或帶 `Large-Change:` trailer。
- 新增依賴或工具要先列版本、確認發布日期，經 Will 核准才安裝（AGENTS.md Constraints）。
- 步驟 4a–5 在工作分支 `refactor/code-first` 進行，開 PR 進 `dev` 讓 CI 跑 db 測試與 stack job；merge 進 `dev` 可逆，`dev`→`main` 不在本階段。

## 本階段依賴的不變量

| 不變量 | 依賴它的機制 |
|---|---|
| 契約只有一份來源（改完後是產生的 `backend/api/openapi.json`） | `frontend/scripts/gen-api.mjs`、`pnpm gen:api:check`、`make -C backend gen`／`gen-check`、`api-breaking` |
| 需要登入的 operation 在檢查參數與 body 之前回 401（`api-design.md:134`） | 現在：`app.web.contract._SessionSecurity`、`app.web.validation`；改完：custom `APIRoute`、`app.http.access` marker |
| 錯誤一律是 RFC 9457 problem，`code` 屬於 `x-extensible-enum` 集合，新增代碼不算破壞性變更（ADR api-and-auth-baseline D3） | `app.web.problems`、`app.web.errors`、`gen-api.mjs` 的 extensible enum 轉換、`frontend/src/lib/api/errors.ts`、`messages.test.ts` |
| 分層無反向 import、`ignore_imports` 為 0（ADR fastapi-backend-stack D5） | `backend/.importlinter` 的 `top-layers`（`exhaustive = true`）、`modules-independent`、`module-layers`、`no-cycles`、`http-stays-at-the-edge`、`sql-below-the-router` |
| healthcheck 不載入 web 框架（6.7 s 實測，`.importlinter` light-healthcheck） | `.importlinter` light-healthcheck 的 `source_modules`、`app.main.cli` 的延遲 import |
| 推上去的 revision 不可修改 | `scripts/check_migrations_immutable.py:15` 的 `VERSIONS` 路徑常數、`app/main/migrate.py:45` 的 `script_location = "app.main:migrations"`、`tests/generate_migration.py` |
| 測試數只增不減 | `scripts/quality-locks.json`、CI 的 lock 檢查（`ci.yml:64`） |

## 規模

| | 數量 | 來源 |
|---|---|---|
| 舊驗證層對外行為 | 68 | 附件 §1 |
| 契約特徵 | 36（21 個 operation） | 附件 §2、§2b |
| 改名引用點 | 85 個檔 | 步驟 2 的消費端指令在 `4dbe59f` 的輸出 |
| 測試 | 28 模組、281 個 `def test_`；刪 9–10、重寫約 33、其餘改 import | 附件 §5 |

## 範圍

- 做：套件改名；code-first 契約；舊驗證行為搬到 FastAPI 原生擴充點；前端跟上；`api-breaking` 接上 CI；ADR 與 `docs/rules/` 更新；處理主清單 Pending「gen-api 檢查每個 x-extensible-enum 都轉成 union」（步驟 5 一併做或保留原觸發，見步驟 5）。
- 不做：schema 或業務邏輯改動；新增 API 功能；改認證、限流演算法（只改套用位置）。
- 延後：schemathesis 這類以規格產生測試案例的工具（觸發：出現 `checked_client` 與手寫測試沒抓到的契約漂移；openapi-core 對 3.1 已在步驟 1 量過可用）。

## 步驟

- [x] **1. spike：量測 code-first 的未知**（被擋於：無；完成 2026-11-08，結果見附件 `spike.md`，停止條件都未觸發）
  - 範圍：在附件目錄 `spike/`（新增，不進產品程式碼）寫原型，量：(a) FastAPI 處理順序，匿名送壞 JSON 時誰先回；(b) custom `APIRoute` 能否在 body 解析前依序回 401→415→400；(c) `pydantic.MISSING` 欄位在 JSON Schema 與 openapi-typescript 的輸出；(d) `responses=` 宣告 `application/problem+json` 的產出結構；(e) openapi-core 對 `app.openapi()` 驗證回應；(f) `json_schema_extra` 產出的 `x-extensible-enum` 經 `gen-api.mjs` 是否成 union，以及在 x-extensible-enum 新增一個值時 `oasdiff breaking --fail-on ERR` 不失敗、改成 `enum` 新增值時的結果（對照）；(g) 前端基線 `pnpm test`、`pnpm gen:api:check`。
  - 消費端：無。
  - 不能動：產品程式碼。
  - 驗收：每項附指令與原始輸出寫進 `spike.md`；(b) 附「拿掉 route class 後匿名壞 JSON 回 400 而不是 401」的對照。
  - 停止條件：(b) 做不到 → D2 重新拍板；(e) 失敗 → D5 重新拍板；(f) 新增 x-extensible-enum 值被當成 ERR → 錯誤代碼的表達方式列成決定題。
- [x] **2. 套件改名（純結構，refactoring playbook）**（被擋於：無；完成 2026-11-09，commit `0b4bff4`、`d08b183`）
  - 範圍（逐檔，搬移以 `git mv`）：
    - `app/platform/*` → `app/core/*`；`app/web/*` → `app/http/*`。
    - `app/main/factory.py`、`app/main/routes.py` → `app/main.py`（`create_app`、`Deps`、`Modules`，以及暫併入的 `check_routes`、`RouteContractError`；模組層級不建立 app、不讀環境；routes 部分在步驟 4b 刪除）。
    - `app/main/cli.py` → `app/cli/__init__.py`（分派器，`pyproject.toml` script 改 `app.cli:main`）；`server.py`、`seed.py`、`recover.py`、`migrate.py`、`healthcheck.py`、`logs.py` → `app/cli/`。
    - `app/main/migrations/*` → `app/migrations/*`；`app/main/schema.py` → `app/migrations/schema.py`；`migrate.py` 的 `VERSION_SCHEMA` 移到 `app/migrations/__init__.py`；`script_location` 改 `app:migrations`。
    - 刪 `app/main/__init__.py`。
    - `.importlinter`：`top-layers` 改為 `cli` > `main | migrations` > `modules` > `http` > `core`（`main` 與 `migrations` 互不 import，`exhaustive = true` 維持）；light-healthcheck 的 `source_modules` 改 `app.cli.healthcheck`、`app.cli`，加 `as_packages = False`（只約束分派器本身，不含 `app.cli.server` 等子模組）；其他合約只換名稱。
    - `scripts/check_migrations_immutable.py:15` 的 `VERSIONS` 改 `backend/src/app/migrations/versions/`；Dockerfile、Makefile（`gen`、vulture、revision 路徑）、`tests/generate_migration.py`、docs、AGENTS.md、`docker-compose.yml:74`、`.github/workflows/` 同步。
  - 消費端：`rg -l --hidden -g '!.git' -g '!docs/plans/**' -g '!**/node_modules/**' -g '!**/.venv/**' -g '!**/__pycache__/**' -e 'app\.(web|platform)\b|app/(web|platform)/|app\.main\.|app\.main:migrations|app/main/' /Users/will/second-brain/.projects/personal/template-fastapi-nextjs`（`4dbe59f` 輸出 85 個檔）。
  - 不能動：任何行為與測試斷言；import-linter 各合約保護的性質（分層方向、模組獨立、healthcheck 輕量）。
  - 驗收：`make -C backend check` 354 passed 不變；`lint-imports` 合約數不變；消費端指令只剩 ADR 的歷史段落；mutation（複本）：在 `app.cli.healthcheck` 加 `import fastapi`，`lint-imports` 要失敗；`git diff -M100% --summary 4dbe59f HEAD -- backend/src/app` 顯示每支 revision 都是 100% 相似的 rename（搬移那次 `VERSIONS` 在舊 base 下沒有檔，檢查不會擋，以此補足）；以搬移後的 commit 為 base，在複本改一支 revision，`check_migrations_immutable.py <搬移後 commit>` 要失敗；docker build 經 CI。
  - commit：拆成「搬移」與「改引用」兩個 commit，或帶 `Large-Change:`；改 `.importlinter` 的那個帶 `Quality-Gate:`。
  - 停止條件：測試數或合約數變化。
- [x] **3. 固定 oasdiff 版本**（完成 2026-11-09；Will 2026-11-08 改核准 v1.35.0（發布 2026-10-28；原核准的 v1.36.0 發布於 2026-11-07，不滿 7 天）。darwin_all sha256 `964ad2c0331768c4515fc49779827094bc89a65b1d309dac28886d1c3faf305c`，與 release `checksums.txt` 相符；其他平台的 checksum 在本步從同一份 `checksums.txt` 登錄）
  - 範圍：oasdiff 版本與 checksum 登錄在 `scripts/tool_versions.py`（比照 hadolint），`backend/Makefile` 下載到 `bin/`；AGENTS.md 必要工具清單更新。
  - 需要人做的事：Will 核准 oasdiff（Go binary，非 PyPI；版本、發布日期與 checksum 在此步列出）。
  - 驗收：乾淨 clone 跑 `make -C backend tools` 後 `backend/bin/oasdiff-<版本> --version` 為登錄版本（檔名含版本，bump 後會重新下載）；改錯 checksum 時下載失敗。
- [x] **4a. 契約改由 FastAPI 產生**（被擋於：步驟 1、2、3；完成 2026-11-09，分支 `refactor/code-first`；差異歸類見附件 `contract-delta.md`）
  - 範圍：各模組新增 `schemas.py`（從 `contract_models.py` 搬內容並改成手寫 Pydantic 模型；錯誤代碼 `x-extensible-enum` 用 `json_schema_extra`），所有 import（兩個 router、`app/cli/seed.py` 等，附件 §3 contract_models 組）改到 `schemas.py`，**本步刪除 `contract_models.py`、datamodel-codegen 與 Makefile vulture 的 `--exclude '*/contract_models.py'`**；router 宣告 `response_model` 與 `responses=`（status 照附件 §2b）；`app/main.py` 新增不需 Deps 的 `build_openapi()`（include 全部 module router 後呼叫 `app.openapi()`）；`make -C backend gen` 改為匯出到 `backend/api/openapi.json`，`gen-check` 比對；FastAPI 的 `openapi_url` 只在 dev 開放，並加入 `ContractValidation` 的 skip 清單與 `check_routes` 的例外；`app.openapi` 掛一段後處理，把 problem 回應的 schema 從 `application/json` 搬到 `application/problem+json`（`build_openapi()` 與 dev 的 `/api/openapi.json` 共用，原型見 `spike/proto.py` 的 `problem_media_type_only`）；每個有參數或 body 的 route 宣告 422 problem，測試斷言 components 沒有 `HTTPValidationError`。舊 `openapi.yaml` 與 openapi-core 驗證本步仍在，當對照。
  - 消費端：附件 §3 的 openapi.yaml、contract_models、gen 三組（33、12、17 個檔）。
  - 不能動：附件 §2 標「保留」的契約特徵。
  - 驗收：`oasdiff diff backend/api/openapi.yaml backend/api/openapi.json --prefix-base /api`（D4）輸出的每一項差異對到附件 §2 的去向，寫進附件 `contract-delta.md`（沒對到的差異 = 失敗）；`make -C backend check` 綠燈；MODE=prod 時 `/api/openapi.json` 回 404 problem 的測試（對照：teamhide 範本關了 docs 卻漏關 `/openapi.json`，`survey-fastapi-templates.md` 問題 2）；mutation（複本）：拿掉某個 schema 的 `max_length`，`gen-check` 要失敗；把 prod 的 `openapi_url` 改成開著，上述測試要失敗。
  - 停止條件：出現附件沒列的差異且無法歸類。
- [x] **4b. 舊驗證行為搬到原生擴充點，移除 spec-first 機制**（被擋於：4a；D2、D5、D6 已決；完成 2026-11-09，commit `2b44c5e`、`2422457`；B 編號去向見附件 `b-coverage.md`）
  - 範圍：附件 §1 每一列照去向實作：custom `APIRoute`（D2：access 401／403 → 限流 429 → 415（含 `Accept-Patch`）→ 嚴格 JSON 400，並自動宣告這些 status；D6：`RATE_LIMITED` 從 dependency 改成 route class 讀的 marker）、body 上限 middleware（413，附件 B2–B7）、`RequestValidationError` 到穩定錯誤代碼的對照表、`Problem` 改由 Pydantic model 建構（D5）、`checked_client` 改對產生的規格驗證（D5）、`redirect_slashes=False`；刪 `contract.py`、`validation.py`、`routes.py`（從 `app/main.py` 移除）、`openapi.yaml`、`tests/testdata/test-openapi.yaml`、openapi-core 改為 dev 依賴（D5）；改寫 3 個 `import yaml` 的測試；`tests/vulture_whitelist.py` 照附件去向刪除（Makefile 的 vulture 參數同步）；`scripts/quality-locks.json` 改成新的測試數。
  - 消費端：附件 §3 的 openapi-core 組（13 個檔）與 §5 的測試清單。
  - 不能動：附件 §1 標「保留」的每一列的 status、`code`、header。
  - 驗收：附件 §1 每個 B 編號出現在某個測試名稱或 docstring，或在附件標明刪除理由（一次性腳本 `check_b_coverage.py` 放附件目錄）；mutation（複本）：拿掉 route class 的 401 檢查，匿名 401 的測試要失敗；把 403 檢查移回依賴（內容檢查之後），「非 admin 送錯 body 回 403」的測試要失敗；拿掉 merge-patch 檢查，415 測試要失敗；拿掉 `Accept-Patch`，對應測試要失敗；把限流移到內容檢查之後，「被 422 擋下也扣 token」的測試要失敗；拿掉 route class 自動宣告的某個 status，`checked_client` 的回應驗證要失敗；`make -C backend check`；改 `quality-locks.json` 的 commit 帶 `Quality-Gate:` 並列出刪掉的測試與對應 B 編號。CI 驗收併入步驟 5。
  - 停止條件：某列行為無法用 FastAPI 原生擴充點保留 → 回報，列成決定題。
  - 完成後的狀態：前端型別與後端不同步，必須和步驟 5 連著做。
- [x] **5. 前端跟上**（被擋於：4b；完成 2026-11-09，commit `1e0412c`）
  - 範圍：`gen-api.mjs` 讀 `backend/api/openapi.json`（extensible enum 轉換保留，並補上主清單那項「每個 x-extensible-enum 都轉成 union」的檢查，因為規格改由程式產生後手寫型別檢查的保護變弱）；`client.ts`、`auth-api.ts`、`server.ts` 與測試的 path 改成含 `/api`（D4）。
  - 消費端：`rg -n 'openapi\.yaml|x-extensible-enum|schemaPath|"/(sessions|users|items|admin)' /Users/will/second-brain/.projects/personal/template-fastapi-nextjs/frontend --glob '!**/schema.d.ts'`（附件 §4 E1–E9）。
  - 不能動：使用者看得到的行為與訊息文字。
  - 驗收：`pnpm test`、`pnpm lint`、`pnpm gen:api:check`、`pnpm build` 綠燈；開 PR 進 `dev`，CI 的 backend（含 db 測試）、frontend、stack job 綠燈；mutation（複本）：後端新增一個錯誤代碼並重新產生 `schema.d.ts`、不補翻譯，`pnpm lint` 的 tsc（`messages.test.ts` 的編譯期檢查）要失敗（原寫 `pnpm test`：vitest 不做型別檢查，實測存活，2026-11-09 更正）；把 `auth-api.ts` 的一個 path 改回不含 `/api`，型別檢查要失敗。
- [x] **6. `api-breaking` 接上 CI**（被擋於：3、5；完成 2026-11-11，commit `0d2978f`、`5823fe8`，PR #29）
  - 範圍：`make -C backend api-breaking` 用 CI 既有的 `BASE`，比對 `git show $(BASE):backend/api/openapi.json` 與工作區版本，`oasdiff breaking --fail-on ERR --err-ignore backend/api/breaking-ignore.txt`；base 沒有 `backend/api/` 目錄時印出說明並以 0 結束（目錄在、檔案不在 = 改名或路徑錯，失敗）；加進 `check`（lefthook pre-push 與 CI 都跑）；`api-design.md:165` 的 BASE 說明改成與 migrations-immutable 相同；刻意的破壞性變更以在 `breaking-ignore.txt` 加一行（附理由）放行，該檔變更走 PR review。
  - 消費端：`api-design.md:165`、`.github/workflows/ci.yml`、`backend/Makefile`、`lefthook.yml`。
  - 驗收：CI 的 log 有 `oasdiff_<版本>_linux_amd64.tar.gz: OK`（步驟 3 在 macOS 驗的下載在 Linux 補驗）；開一個 PR 進 `dev`，刪一個必填回應欄位，CI 的 `api-breaking` 失敗（2026-11-11 Will：不繞過 pre-push 推上破壞性 commit，失敗這半由本機 pre-push 的同一個 make target 證明，CI 驗放行那半）；在 `breaking-ignore.txt` 加上該項後綠燈；本機 `make -C backend api-breaking BASE=<含 openapi.json 的 commit>` 重現同樣結果。
- [x] **7. ADR 與規則文件**（被擋於：2–6；完成 2026-11-11，commit `933d66f`）
  - 範圍：修訂 ADR `2026-11-05-fastapi-backend-stack` 的 D1、D2、D5 與 Invariants，以及 ADR `2026-10-18-api-and-auth-baseline` 的 D2 與相關 Invariants（decision-log skill）；把 D2、D5、D6 寫進 ADR（decision-log skill，可與上述修訂合併），附件 `brief-d2-d5-d6.md` 的決定併入後不留在計畫目錄；改寫 `docs/rules/api-design.md`（契約段；認證段寫明錯誤順序表 413 > 404/405 > 401/403 > 429 > 415 > 400 > 422；限流段 `:153` 改成每個請求都扣）、`add-module.md`（`schemas.py`、新套件名）、AGENTS.md、`frontend/AGENTS.md`、`frontend/README.md`、`template/derived/README.md`、根目錄 `README.md`、`.env-example`。
  - 驗收：`scripts/check_doc_refs.py` 綠燈；`rg -n --hidden -g '!.git' -g '!docs/plans/**' -g '!**/node_modules/**' -g '!**/*.lock' -g '!**/pnpm-lock.yaml' 'openapi\.yaml|contract_models|openapi-core|hand-written|3\.0\.3|app\.(web|platform)\b' /Users/will/second-brain/.projects/personal/template-fastapi-nextjs` 只剩 ADR 的歷史段落（另允許 `pyproject.toml`、`uv.lock` 的 openapi-core，D5）。
- [ ] **8. 收尾**：完成定義逐項核對（Goal 每一句對到一個驗收）；design-review skill 對整個 diff 跑一次；長期檢查（`gen-check`、`api-breaking`）已在 CI，一次性腳本留附件；延後項附觸發條件搬回主清單，主清單 x-extensible-enum 那項已依步驟 5 結果關閉（2026-11-11 Will）；附件中的決定併入 ADR。驗收：CI 全綠的 run 連結、design-review 的 finding 清單與處置。

## 決定

- **D0 契約來源**（已決 2026-11-08，Will；擋住：4a 之後）
  - 結論：FastAPI code-first；理由與量測見前提段。ADR `docs/adr/2026-11-11-code-first-contract-and-request-checks.md` 的「契約來源」，並修訂 fastapi-backend-stack 的契約與模型兩條。
- **D1 套件命名**（已決 2026-11-08，Will：「這個命名併進上一則的重構計畫一起開工」，`web` 改 `http` 為該則建議）
  - 結論：`core`、`http`、`main.py`＋`cli/`＋`migrations/`；檔案對應見步驟 2。
- **D2 錯誤優先順序與請求格式**（已決 2026-11-08，Will：照簡報建議；擋住：4b；事實：`spike.md` (a)(b)；ADR `docs/adr/2026-11-11-code-first-contract-and-request-checks.md` 的「錯誤順序與請求格式」）
  - 結論：`app.web`（改名後 `app.http`）的 custom `APIRoute` 在 FastAPI 讀 body 之前依序檢查 access marker（401／403，含 admin）→ 限流（429，見 D6）→ 宣告的 media type（415；PATCH 只收 `application/merge-patch+json`，415 回應帶 `Accept-Patch`）→ 嚴格 JSON（400：語法錯、尾巴多字、NaN／Infinity、非 UTF-8，以 custom `Request.json()` 實作）；之後 Pydantic 驗證失敗回 422 `{pointer, code, detail}`。整體順序 413 > 404/405 > 401/403 > 429 > 415 > 400 > 422（2026-11-11 Will 更正：照附件 B5 維持 413 先，原寫 404/405 > 413）；413 由獨立的 body 上限 middleware 負責。route class 依 route 的 marker 與 body 自動把它負責的 status 寫進 OpenAPI。
  - 理由：Spring Security、ASP.NET Core、DRF、Kubernetes 都先驗身分與權限再解析 body；RFC 9110（415）、5789（`Accept-Patch`）、7396、8259。改變現況的兩點：403 提前到內容檢查之前（現在非 admin 送錯 body 先拿 422），415 補 `Accept-Patch`。被推翻的選項：只用 exception handler（匿名壞 JSON 先於 401、接受 NaN、415 只能猜）。
- **D3 匿名 401 的 body**（已決 2026-11-08，Will：選 A；擋住：4a）
  - A：統一用 access marker 現有的 `code: session_invalid`——只剩一條路徑；前端只看 status（`client.ts:23-28`），`session_invalid` 已在 enum 與訊息裡（建議）
  - B：保留舊的無 `code`、detail `this operation needs a signed-in session`——要多一個錯誤種類
- **D4 `/api` 前綴**（已決 2026-11-08，Will：選 A；擋住：4a、5）
  - A：paths 含 `/api`（router `prefix="/api"` 的原生產出），前端 baseUrl 改為 origin、path 字串加 `/api`——不需後處理規格（建議）
  - B：匯出時去掉前綴並加 `servers: [/api]`——前端不動，但多一段覆寫 `app.openapi` 的程式
- **D5 測試中的回應驗證**（已決 2026-11-08，Will：照簡報建議；擋住：4b；事實：`spike.md` (e)；同一份 ADR 的「測試中的回應驗證」）
  - 結論：openapi-core 降為 dev 依賴，`checked_client` 對產生的規格驗證每個測試回應（status、media type、header、body；`application/problem+json` 註冊 deserializer）；problem 改由 Pydantic `Problem` model 建構。schemathesis 維持延後。
  - 理由：規格由同一份 model 產生只讓成功 body 的驗證重複；錯誤 body、未宣告的 status、media type 仍會漂移（Schemathesis 的 conformance 檢查、ASP.NET `TypedResults` 為基準）。
- **D6 被擋下的請求是否扣限流額度**（已決 2026-11-08，Will：照簡報建議；擋住：4b；同一份 ADR 的「限流扣額」）
  - 結論：每個打到限流 operation 的請求都扣一個 token，含被 415、400、422 擋下的；限流改成 route class 讀取的 marker，位置在 access 檢查之後、內容檢查之前。額度維持 `AUTH_RATE_LIMIT_PER_MIN` 預設 5、註冊與登入共用一個 bucket。
  - 理由：Rails `rate_limit`、DRF throttle、ASP.NET `UseRateLimiter` 都在解析與驗證前計數；現行「被驗證擋下不扣」是 middleware 順序的副作用，且不一致（契約層 422 不扣、service 層 422／409 會扣）；猜密碼另由 `sign_in_throttles` 依帳號＋來源節流。重新評估的觸發：衍生專案上線後合法使用者因 422 撞到 429——調額度或拆 bucket，不改計數規則。
- **D8 匯出格式**（預設，可推翻）：JSON，`backend/api/openapi.json`，不需新增 pyyaml；dev 模式由 `/api/openapi.json` 與既有文件頁提供。

拍板順序：全部已決（D2、D5、D6 於 2026-11-08 依業界基準簡報拍板，簡報已併入上述 ADR）。

已決（2026-11-11，Will：照建議）：4b 審查提出 D2 的整體順序句（404/405 > 413）與附件 B5（413 先於 404/405）矛盾。維持 B5：body 上限 middleware 在路由之前讀完 body，也是 B6 的前提；D2 與步驟 7 的順序句已改寫，`route.py`、`api-design.md` 本來就照這個寫。

已決（2026-11-11，Will：照建議）：T1，產生的規格保留 Pydantic 的 `title`。步驟 5 量到它只進 `schema.d.ts` 的 JSDoc 註解，型別不變。

### 步驟 4b 的累積審查清單（第 1 輪：`/code-review high`＝CR、diff 視角審查者＝DR）

| # | finding | 嚴重度 | 處置與證據 |
|---|---|---|---|
| 1 | 多餘欄位送 `null` 回 `null_not_allowed`（CR1、DR1） | blocking | fixed：null 規則排除 `extra_forbidden`；`test_validation_codes` 新參數，回退 mutant killed |
| 2 | 空 body 沒 Content-Type 回 415（CR2） | should | fixed：沒有 body 不看 media type；`test_a_missing_body_is_required_whatever_its_content_type`，mutant killed |
| 3 | 錯誤順序文件寫 404/405 > 413（CR3、DR4） | should | fixed（文件照實寫）＋raised（D2 與 B5 矛盾，2026-11-11 Will 決定維持 B5，見上） |
| 4 | 拿掉 ECMA pattern 的保護後沒有替代（CR4） | should | fixed：`test_patterns_mean_the_same_to_the_server_and_its_clients` 掃產生的規格 |
| 5 | 巢狀 request model 沒檢查 `REQUEST_CONFIG`（CR5） | should | fixed：啟動檢查遞迴；`test_a_request_body_is_a_strict_model`，mutant killed |
| 6 | 非 JSON media type 也做嚴格 JSON 解析（CR6） | should | fixed：只解析 `application/json`、`+json`；`test_a_body_in_another_media_type_is_not_parsed_as_json`，mutant killed |
| 7 | include 時加的 marker／`RATE_LIMITED` 被忽略（DR3） | should | fixed：啟動檢查拒絕；`test_an_include_adds_no_marker_or_rate_limit`，mutant killed |
| 8 | 非 `ApiRoute` 的 route 限流失效（CR7） | should | rejected：真實 app 的 `check_routes` 拒絕非 `ApiRoute`（`main.py` `_api_route_problems`、`test_a_route_needs_the_api_route_class`、mutation M13 killed），`serve` 一定掛 modules |
| 9 | 文件仍寫 `openapi.yaml`（CR8） | should | fixed（步驟 5 的文件修正；剩 `frontend/osv-scanner.toml` 的 reason 字樣，該檔是 gate 檔，步驟 7 一併改） |
| 10 | `ApiRoute` 要加 responses 時 route 建兩次（CR9） | should | recorded：實測單一 route 約 1.1 ms，每個 app 多約 20 ms（啟動一次；測試每建一個 app 一次）；不靠 FastAPI 私有 API 的替代做法要重做它的 dependant 分析。觸發：測試時間或啟動時間成為問題 |
| 11 | 多 method 的 route 重複回報（CR10） | nit | fixed：每個 route 回報一次；`test_a_route_with_several_methods_is_reported_once`，mutant killed |
| 12 | path UUID 接受無連字號、大括號、`urn:uuid:`（DR2） | should | fixed：`app.http.schemas.Id`，規格不變；`test_validation_codes` 3 個新參數，mutant killed |
| 13 | threat model、api-design 各一段描述已刪的機制（DR5、DR6） | should | fixed |
| 14 | 驗收產出物未追蹤（DR7） | should | fixed：`git add -N`，與 4b 一起 commit |
| 15 | 405 的 `Allow` 只列第一個比對到的 route 的方法（DR8） | nit | recorded：Starlette 的行為，舊行為是不帶 `Allow`；測試釘住現值並寫明原因。觸發：有 client 依 `Allow` 決定可用方法 |

findings 15（blocking 1）：fixed 12、recorded 2、raised 1（#3 兼 fixed）、rejected 1。

### 步驟 6 的累積審查清單（`/code-review medium` 兩輪：R1、R2）

| # | finding | 嚴重度 | 處置 |
|---|---|---|---|
| R1-1 | ignore 檔的註解範例會被 oasdiff 當成 ignore 行 | should | fixed：先濾掉註解行再交給 oasdiff；mutant killed |
| R1-2 | 本機預設 `BASE=origin/main` 還沒有契約，本機與 pre-push 等於沒比 | should | fixed：本機預設 `origin/dev`，沒有 dev 的 clone 用 `origin/main` |
| R1-3 | 「進了 base 就刪」不符合 dev→main 流程 | should | fixed：寫成進了 `main` 才刪 |
| R1-4 | `--fail-on ERR` 放過刪除 request 欄位（oasdiff 只給 WARN） | blocking | fixed：`--fail-on WARN`、`--warn-ignore`；mutant killed |
| R1-5 | ignore 檔沒列為 gate 檔 | should | fixed：加進 `GATE_FILES` |
| R1-6 | base 沒有 `api/` 就通過，改名時會靜默關掉檢查 | should | rejected：目錄名是字面 `api`，`CONTRACT` 改名時 base 仍有 `api/`、讀不到檔而失敗（實測 `BASE=5bebe4c` exit 2）；沒有 `api/` 的 base 目前就是 `origin/main` |
| R1-7 | 「一個變更一行」誤導：共用 schema 每個 operation 各一行 | should | fixed |
| R1-8 | base 解析寫在沒有測試的 shell，與 `check_migrations_immutable.py` 重複 | should | recorded：邊界由 mutation 與手動的三種 base 驗過。觸發：recipe 再出一個 bug 或多一種邊界時，改寫成 `scripts/` 下有單元測試的腳本 |
| R1-9 | `BASE` 解析不到時只有 git 的原始錯誤 | should | fixed：分開印「不是 commit」與「沒有共同歷史」 |
| R1-10 | 新 checkout 第一次 `check` 要從 GitHub 下載 oasdiff，離線無法 push | should | recorded：步驟 3 的設計（固定版本加 checksum，`bin/` 每個 checkout 一份）。觸發：需要離線推送 |
| R2-1 | `make new` 剛產生、還沒有 remote 的專案，第一次 push 時 `origin/*` 不存在，`check` 失敗 | should | recorded：`migrations-immutable` 在步驟 6 之前就有同樣行為（原預設 `origin/main`）。觸發：衍生專案第一次推送被擋 |
| R2-2 | 錯誤訊息分不出 ref 不存在與歷史無關 | nit | fixed |
| R2-3 | 文件把 oasdiff 的比對寫反（實際是行包含 method、path 與訊息） | should | fixed |
| R2-4 | 有縮排的註解沒被濾掉 | should | fixed：`^[[:space:]]*#`；mutant killed |
| R2-5 | 刪 ignore 行也要 trailer，文件沒寫 | should | fixed |
| R2-6 | `check_migrations_immutable.py` 的用法說明仍寫預設 `origin/main` | should | fixed：說明 make 會傳 `origin/dev` |
| R2-7 | help 文字沒寫 `origin/main` 退路 | nit | fixed |
| R2-8 | `BASE ?= $(shell …)` 每次展開都重跑 git | nit | fixed：只在未定義時解析一次 |
| R2-9 | `GATE_FILES` 新項目沒有測試 | should | fixed：`test_api_breaking_exceptions_need_trailer`；mutant killed |

findings 19（blocking 1）：fixed 15、recorded 3、raised 0、rejected 1。

## 進度

狀態值：未開始／進行中／程式完成待驗收／完成。

| 步驟 | 狀態 | 已跑的驗收 | 未跑的驗收與原因 | commit |
|---|---|---|---|---|
| 1 spike | 完成 | (a)–(g) 全部，指令與原始輸出在 `spike.md` | 無 | `docs(plans): record the step 1 spike of the code-first plan` |
| 2 套件改名 | 完成 | `make -C backend check` exit 0：354 passed、95 deselected，`lint-imports` 7 kept 0 broken，squawk 0 issues；`scripts/` 59 tests、`template/` 33 tests OK，`check_doc_refs.py`、`check_vercel_config.py` exit 0；mutation（複本）4 個全 killed：`app.cli.healthcheck` 加 `import fastapi`、分派器 `app.cli` 加 `import uvicorn`（light-healthcheck BROKEN），`app.main` import `app.migrations`、`app.core` import `app.http`（top-layers BROKEN）；`git diff -M100% --summary 4dbe59f HEAD -- backend/src/app` 中 `r20261103_baseline.py`、`versions/__init__.py`、`script.py.mako` 為 100% rename；以搬移後 commit 為 base，複本改 revision，`check_migrations_immutable.py` exit 1（killed）；消費端指令只剩 ADR `fastapi-backend-stack` D1、D2、D5 的段落（步驟 7 修訂）與新名稱 `app.main.check_routes` ；CI run 42501080365（`d08b183`）全綠：backend（含 db 測試）、frontend、generate、commit policy、compose stack（image + migrate + health）；`/code-review` findings 7（blocking 0）：fixed 2（`app.cli` 的死 `__main__` 區塊、AGENTS.md 分層表對 `logs.py` 的描述）、rejected 5（revision 檢查空窗 ×2：前提段 Will 2026-11-08 決定、以 100% rename 補足；ADR 舊名：步驟 7；`http` 與標準庫同名：沒有任何設定把 `src/app` 放進 `sys.path`；routes 併入 `main.py`：步驟 2 範圍、4b 刪除） | 無 | `0b4bff4`、`d08b183` |
| 3 固定 oasdiff | 完成 | 乾淨 clone＋diff：`make -C backend tools` → `oasdiff_1.35.0_darwin_all.tar.gz: OK`、`oasdiff version 1.35.0`，重跑 Nothing to be done；改錯 darwin_all checksum → `FAILED`、exit 2、沒留下 binary；改 `OASDIFF_VERSION` 後會重新下載（不沿用舊 binary）；`tool_versions.py bump oasdiff --version 1.35.0` 重寫後 Makefile 無差異（三個 checksum 與 release `checksums.txt` 相符）；`check` 回報 `oasdiff: 1.35.0 (current)`；`make -n tools OASDIFF_OS=linux` 在 arm64 選 `linux_arm64` 與對應 checksum；mutation（複本）：刪掉 `OASDIFF_SHA256_linux_amd64`，`verify` exit 1（killed）；`scripts/` 63 tests OK、`check_doc_refs.py`、`actionlint`、`make lint-docker` exit 0；`make -C backend check` exit 0（354 passed）；`/code-review` findings 7（blocking 0）：fixed 3（版本變更不重新下載，hadolint 一併改成含版本的檔名；release 少了 archive 時 `bump` 給明確訊息；測試 fixture 改名）、recorded 1（Linux 實際下載：步驟 6 CI 跑 `api-breaking` 時驗）、rejected 3（兩份下載規則合併：binary 與 tar 檔形狀不同；改成資料表、`uname` 延遲求值：nit） | Linux 上實際下載與解壓：本機是 macOS，步驟 6 的 CI run 補驗 | 見 git log |
| 4a 契約改由 FastAPI 產生 | 完成 | `contract_delta.py`（附件，一次性）exit 0：oasdiff diff 與 changelog 的每一項都對到附件 §2（F1–F3、F13–F15、F20–F22、F26、F30、F32，另補 T1 Pydantic title、T2 component 內嵌）；腳本本身的 mutation 10 個全部 exit 1，兩邊同時加 nullable 欄位的對照組 exit 0；oasdiff 對 x-extensible-enum、header、media type 的差異會報（對照組），所以三份 enum 清單、§2b 的 status 與 header 都與 yaml 相同；`make -C backend check` exit 0：361 passed、95 deselected，`lint-imports` 7 kept，pyright 0 errors，squawk 0 issues；`check_locks.py`、`check_doc_refs.py`、`actionlint` exit 0；mutation（複本）7 個全 killed：拿掉 `Item.title` 的 `max_length` → gen-check 的比對失敗（以 Makefile 的同一段指令直接跑，避開 `uv run` 改寫 editable 安裝）、prod 開 `openapi_url` → `test_docs_only_in_dev` 失敗（第一版測試用 stub app 時這個 mutation 存活，因為舊的 `ContractValidation` 先回 404；改用真實 routes 後由 `check_routes` 擋下）、拿掉 problem media type 後處理、後處理只認 `application/json`、health 路徑提前跳過、`signed_in` 拿掉 security、`getItem` 少宣告 422；審查：`/code-review high` 10＋2 項、diff 視角審查者第 1 輪 7 項（合併成 16 列的累積清單）、第 2 輪 2 項新 finding＋推翻 1 項駁回；findings 18（blocking 0）：fixed 16、recorded 2（過渡期 yaml 與 schemas 沒有自動閘門：4a–5 同一分支一個 PR、4b 刪 yaml，過渡期規則寫進 AGENTS.md 與 api-design.md；代碼清單由登錄表產生：附件 B61，4b）、raised 0、rejected 0；CI run 42511618636（PR #24）全綠：backend（含 db 測試）、frontend、generate、commit policy、compose stack；推送前 pre-push 抓到衍生專案會帶到 `docs/plans` 路徑（generator 測試），另一個 commit 修正 | 無（T1 保留 Pydantic title：2026-11-11 Will 照建議） | `1ebeb82`、`c02318e`、`0f10347`；PR #24（draft，4a–5 完成後才合併） |
| 4b 搬移驗證行為、移除 spec-first | 完成 | `make -C backend check` exit 0：370 passed、95 deselected（`quality-locks.json` backend 361→370），`lint-imports` 7 kept，pyright 0 errors，squawk 0 issues；產生的 `openapi.json` 與 4a 逐位元相同（route class 自動宣告的 status 剛好補回 router 拿掉的）；`check_b_coverage.py` exit 0（68 列）且腳本的 3 個 mutation exit 1；`check_locks.py`、`check_doc_refs.py`、`actionlint`、`make lint-docker`、`scripts/` 63 tests、`template/` 33 tests OK；mutation（複本）15 個全 killed，含驗收列的 6 個：拿掉 401 前置檢查、403 移回依賴、PATCH 收 `application/json`、拿掉 `Accept-Patch`、限流移到內容檢查之後、拿掉自動宣告的 401（`checked_client` 抓到），另有 lax request model、`redirect_slashes`、null 規則、空 query 規則、body 上限、NaN、route class 與 strict body 的啟動檢查、415；審查：`/code-review high` 10 項＋diff 視角審查者 8 項，合併 15 列（見下方累積清單），修正後 7 個回退 mutant 全 killed、第一輪 15 個重跑仍全 killed | 無（CI run 42550941936（`a64831f`）全綠：backend（含 db 測試）、frontend、generate、commit policy、compose stack） | `2b44c5e`、`2422457`；PR #24（draft） |
| 5 前端跟上 | 完成 | `pnpm lint` exit 0、`pnpm test` 42 passed、`pnpm gen:api:check`、`pnpm build` exit 0；`gen-api.mjs` 讀 `openapi.json`，並檢查每份 x-extensible-enum 清單在輸出裡都成了 union（主清單那項）；mutation（複本）：新增錯誤代碼不補翻譯 → tsc 失敗（`pnpm test` 存活，驗收寫法已更正）、`auth-api.ts` 的 path 改回不含 `/api` → tsc 失敗、拿掉 union transform → `gen-api.mjs` exit 1；T1 量到：Pydantic 的 title 只進 JSDoc 註解，型別不變（`next_cursor: string \| null`） | 無（同一個 CI run 42550941936） | `1e0412c` |
| 6 api-breaking 接上 CI | 完成 | `make -C backend check` exit 0（370 passed），本機 `BASE` 預設 `origin/dev` 時 `api-breaking` 輸出 `No changes detected`；三種 base：`origin/dev` exit 0、`5bebe4c`（有 `api/` 沒 `openapi.json`）exit 2、`d9491d9~1`（沒 `api/`）印說明 exit 0，`BASE=orgin/dev` 印說明 exit 2；破壞性變更（`Item.title` 改選填）：探測分支推送時 pre-push 的 `backend-check` 報 4 個 `response-property-became-optional` 擋下，加上 4 行 ignore 後 pre-push 與 CI 通過（PR #30 backend run 43029687176，已關閉）；CI log 有 `oasdiff_1.35.0_linux_amd64.tar.gz: OK`（run 43028299405、43029687176，步驟 3 的 Linux 下載補驗）；mutation（複本）6 個全 killed：`Item.title` 不再必填、`--fail-on ERR`（刪 request 欄位只是 WARN，request model 拒收多餘欄位）、ignore 檔不濾註解、只濾行首 `#`、commit policy 拿掉 `breaking-ignore.txt`（新測試）；`scripts/` 64 tests、`template/` 33 tests、`check_doc_refs.py`、`actionlint` OK；PR #29 CI 全綠（含 db 測試、compose stack） | 無 | `0d2978f`、`5823fe8`；PR #29 |
| 7 ADR 與規則文件 | 完成 | 新 ADR `docs/adr/2026-11-11-code-first-contract-and-request-checks.md`（86 行：契約來源、錯誤順序與請求格式、測試中的回應驗證、限流扣額），兩份舊 ADR 以「2026-11-11 修訂」標出被取代的條目；簡報併入後從附件目錄刪除；`check_doc_refs.py` exit 0、`check_locks.py` exit 0、`make -C backend check` exit 0（370 passed、95 deselected，`api-breaking` No changes detected）；驗收的搜尋只剩三份 ADR 的段落、`pyproject.toml` 的 openapi-core，以及三處不是舊機制的命中：`backend/tests/apitest.py` 3 行註解（openapi-core 是 D5 留下的測試依賴）、`frontend/osv-scanner.toml`（`@redocly/openapi-core` 是另一個套件）、`frontend/AGENTS.md:72`（型別不手寫的規則）；`api-design.md` 另修正 9 處步驟 4–5 沒跟上的敘述（`servers`／`/api` 前綴、Go 與 YAML 範例、`nullable`、`#/components/parameters`、契約的 `security`、前端 path）；`add-module.md`、`AGENTS.md`、`frontend/README.md`、`.env-example` 已在步驟 4–6 更新，搜尋無命中，未改 | 無（文件改動，依規則不跑 `/code-review`） | `933d66f` |
| 8 收尾 | 未開始 | | | |
