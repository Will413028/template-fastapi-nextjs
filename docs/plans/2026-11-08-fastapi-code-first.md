# 後端改為 FastAPI code-first，套件改用業界命名

**Goal：** 契約由各模組的 Pydantic `schemas.py` 經 FastAPI 產生（OpenAPI 3.1），匯出到 `backend/api/openapi.json` 進版控並由 `gen-check` 守住；前端從它產生型別；openapi-core（D5：只留作測試依賴）、`contract.py`、`validation.py`、`routes.py`、`contract_models.py`、datamodel-codegen 移除，舊驗證層的對外行為逐項保留或依決定改變（附件 §1 每列有去向與測試）；套件為 `app.core`、`app.http`、`app.modules`、`app/main.py`、`app/cli/`、`app/migrations/`；`make -C backend api-breaking` 接進 `check`，CI 的 PR 與 push 以 `BASE` 比對（pre-push 用本機預設 `origin/main`，在 `dev`→`main` 合併前沒有可比的規格，只印說明）。驗收見步驟 8。

**上層：** 見本機個人脈絡檔（`AGENTS.local.md` 記的主清單）。

**附件：** `docs/plans/2026-11-08-fastapi-code-first/inventory.md`（舊行為 B1–B68、契約特徵 F1–F36、引用點、前端耦合、測試；快照 `4dbe59f`；決定編號已對齊本檔）；`spike.md`（步驟 1）；`brief-d2-d5-d6.md`（D2、D5、D6 的業界基準簡報）；`survey-fastapi-templates.md`（9 個 FastAPI 範本與大型應用的作法對照，2026-11-09）。

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
- [ ] **4a. 契約改由 FastAPI 產生**（被擋於：步驟 1、2、3）
  - 範圍：各模組新增 `schemas.py`（從 `contract_models.py` 搬內容並改成手寫 Pydantic 模型；錯誤代碼 `x-extensible-enum` 用 `json_schema_extra`），所有 import（兩個 router、`app/cli/seed.py` 等，附件 §3 contract_models 組）改到 `schemas.py`，**本步刪除 `contract_models.py`、datamodel-codegen 與 Makefile vulture 的 `--exclude '*/contract_models.py'`**；router 宣告 `response_model` 與 `responses=`（status 照附件 §2b）；`app/main.py` 新增不需 Deps 的 `build_openapi()`（include 全部 module router 後呼叫 `app.openapi()`）；`make -C backend gen` 改為匯出到 `backend/api/openapi.json`，`gen-check` 比對；FastAPI 的 `openapi_url` 只在 dev 開放，並加入 `ContractValidation` 的 skip 清單與 `check_routes` 的例外；`app.openapi` 掛一段後處理，把 problem 回應的 schema 從 `application/json` 搬到 `application/problem+json`（`build_openapi()` 與 dev 的 `/api/openapi.json` 共用，原型見 `spike/proto.py` 的 `problem_media_type_only`）；每個有參數或 body 的 route 宣告 422 problem，測試斷言 components 沒有 `HTTPValidationError`。舊 `openapi.yaml` 與 openapi-core 驗證本步仍在，當對照。
  - 消費端：附件 §3 的 openapi.yaml、contract_models、gen 三組（33、12、17 個檔）。
  - 不能動：附件 §2 標「保留」的契約特徵。
  - 驗收：`oasdiff diff backend/api/openapi.yaml backend/api/openapi.json --prefix-base /api`（D4）輸出的每一項差異對到附件 §2 的去向，寫進附件 `contract-delta.md`（沒對到的差異 = 失敗）；`make -C backend check` 綠燈；MODE=prod 時 `/api/openapi.json` 回 404 problem 的測試（對照：teamhide 範本關了 docs 卻漏關 `/openapi.json`，`survey-fastapi-templates.md` 問題 2）；mutation（複本）：拿掉某個 schema 的 `max_length`，`gen-check` 要失敗；把 prod 的 `openapi_url` 改成開著，上述測試要失敗。
  - 停止條件：出現附件沒列的差異且無法歸類。
- [ ] **4b. 舊驗證行為搬到原生擴充點，移除 spec-first 機制**（被擋於：4a；D2、D5、D6 已決）
  - 範圍：附件 §1 每一列照去向實作：custom `APIRoute`（D2：access 401／403 → 限流 429 → 415（含 `Accept-Patch`）→ 嚴格 JSON 400，並自動宣告這些 status；D6：`RATE_LIMITED` 從 dependency 改成 route class 讀的 marker）、body 上限 middleware（413，附件 B2–B7）、`RequestValidationError` 到穩定錯誤代碼的對照表、`Problem` 改由 Pydantic model 建構（D5）、`checked_client` 改對產生的規格驗證（D5）、`redirect_slashes=False`；刪 `contract.py`、`validation.py`、`routes.py`（從 `app/main.py` 移除）、`openapi.yaml`、`tests/testdata/test-openapi.yaml`、openapi-core 改為 dev 依賴（D5）；改寫 3 個 `import yaml` 的測試；`tests/vulture_whitelist.py` 照附件去向刪除（Makefile 的 vulture 參數同步）；`scripts/quality-locks.json` 改成新的測試數。
  - 消費端：附件 §3 的 openapi-core 組（13 個檔）與 §5 的測試清單。
  - 不能動：附件 §1 標「保留」的每一列的 status、`code`、header。
  - 驗收：附件 §1 每個 B 編號出現在某個測試名稱或 docstring，或在附件標明刪除理由（一次性腳本 `check_b_coverage.py` 放附件目錄）；mutation（複本）：拿掉 route class 的 401 檢查，匿名 401 的測試要失敗；把 403 檢查移回依賴（內容檢查之後），「非 admin 送錯 body 回 403」的測試要失敗；拿掉 merge-patch 檢查，415 測試要失敗；拿掉 `Accept-Patch`，對應測試要失敗；把限流移到內容檢查之後，「被 422 擋下也扣 token」的測試要失敗；拿掉 route class 自動宣告的某個 status，`checked_client` 的回應驗證要失敗；`make -C backend check`；改 `quality-locks.json` 的 commit 帶 `Quality-Gate:` 並列出刪掉的測試與對應 B 編號。CI 驗收併入步驟 5。
  - 停止條件：某列行為無法用 FastAPI 原生擴充點保留 → 回報，列成決定題。
  - 完成後的狀態：前端型別與後端不同步，必須和步驟 5 連著做。
- [ ] **5. 前端跟上**（被擋於：4b）
  - 範圍：`gen-api.mjs` 讀 `backend/api/openapi.json`（extensible enum 轉換保留，並補上主清單那項「每個 x-extensible-enum 都轉成 union」的檢查，因為規格改由程式產生後手寫型別檢查的保護變弱）；`client.ts`、`auth-api.ts`、`server.ts` 與測試的 path 改成含 `/api`（D4）。
  - 消費端：`rg -n 'openapi\.yaml|x-extensible-enum|schemaPath|"/(sessions|users|items|admin)' /Users/will/second-brain/.projects/personal/template-fastapi-nextjs/frontend --glob '!**/schema.d.ts'`（附件 §4 E1–E9）。
  - 不能動：使用者看得到的行為與訊息文字。
  - 驗收：`pnpm test`、`pnpm lint`、`pnpm gen:api:check`、`pnpm build` 綠燈；開 PR 進 `dev`，CI 的 backend（含 db 測試）、frontend、stack job 綠燈；mutation（複本）：後端新增一個錯誤代碼並重新產生 `schema.d.ts`、不補翻譯，`pnpm test`（`messages.test.ts`）要失敗；把 `auth-api.ts` 的一個 path 改回不含 `/api`，型別檢查要失敗。
- [ ] **6. `api-breaking` 接上 CI**（被擋於：3、5）
  - 範圍：`make -C backend api-breaking` 用 CI 既有的 `BASE`，比對 `git show $(BASE):backend/api/openapi.json` 與工作區版本，`oasdiff breaking --fail-on ERR --err-ignore backend/api/breaking-ignore.txt`；base 沒有 `backend/api/` 目錄時印出說明並以 0 結束（目錄在、檔案不在 = 改名或路徑錯，失敗）；加進 `check`（lefthook pre-push 與 CI 都跑）；`api-design.md:165` 的 BASE 說明改成與 migrations-immutable 相同；刻意的破壞性變更以在 `breaking-ignore.txt` 加一行（附理由）放行，該檔變更走 PR review。
  - 消費端：`api-design.md:165`、`.github/workflows/ci.yml`、`backend/Makefile`、`lefthook.yml`。
  - 驗收：CI 的 log 有 `oasdiff_<版本>_linux_amd64.tar.gz: OK`（步驟 3 在 macOS 驗的下載在 Linux 補驗）；開一個 PR 進 `dev`，刪一個必填回應欄位，CI 的 `api-breaking` 失敗；在 `breaking-ignore.txt` 加上該項後綠燈；本機 `make -C backend api-breaking BASE=<含 openapi.json 的 commit>` 重現同樣結果。
- [ ] **7. ADR 與規則文件**（被擋於：2–6）
  - 範圍：修訂 ADR `2026-11-05-fastapi-backend-stack` 的 D1、D2、D5 與 Invariants，以及 ADR `2026-10-18-api-and-auth-baseline` 的 D2 與相關 Invariants（decision-log skill）；把 D2、D5、D6 寫進 ADR（decision-log skill，可與上述修訂合併），附件 `brief-d2-d5-d6.md` 的決定併入後不留在計畫目錄；改寫 `docs/rules/api-design.md`（契約段；認證段寫明錯誤順序表 404/405 > 413 > 401/403 > 429 > 415 > 400 > 422；限流段 `:153` 改成每個請求都扣）、`add-module.md`（`schemas.py`、新套件名）、AGENTS.md、`frontend/AGENTS.md`、`frontend/README.md`、`template/derived/README.md`、根目錄 `README.md`、`.env-example`。
  - 驗收：`scripts/check_doc_refs.py` 綠燈；`rg -n --hidden -g '!.git' -g '!docs/plans/**' -g '!**/node_modules/**' -g '!**/*.lock' -g '!**/pnpm-lock.yaml' 'openapi\.yaml|contract_models|openapi-core|hand-written|3\.0\.3|app\.(web|platform)\b' /Users/will/second-brain/.projects/personal/template-fastapi-nextjs` 只剩 ADR 的歷史段落（另允許 `pyproject.toml`、`uv.lock` 的 openapi-core，D5）。
- [ ] **8. 收尾**：完成定義逐項核對（Goal 每一句對到一個驗收）；design-review skill 對整個 diff 跑一次；長期檢查（`gen-check`、`api-breaking`）已在 CI，一次性腳本留附件；延後項附觸發條件搬回主清單，主清單 x-extensible-enum 那項依步驟 5 結果勾掉或保留；附件中的決定併入 ADR。驗收：CI 全綠的 run 連結、design-review 的 finding 清單與處置。

## 決定

- **D0 契約來源**（已決 2026-11-08，Will；擋住：4a 之後）
  - 結論：FastAPI code-first；理由與量測見前提段。ADR：步驟 7 修訂 fastapi-backend-stack D1、D2。
- **D1 套件命名**（已決 2026-11-08，Will：「這個命名併進上一則的重構計畫一起開工」，`web` 改 `http` 為該則建議）
  - 結論：`core`、`http`、`main.py`＋`cli/`＋`migrations/`；檔案對應見步驟 2。
- **D2 錯誤優先順序與請求格式**（已決 2026-11-08，Will：照簡報建議；擋住：4b；簡報：附件 `brief-d2-d5-d6.md` §3，事實：`spike.md` (a)(b)）
  - 結論：`app.web`（改名後 `app.http`）的 custom `APIRoute` 在 FastAPI 讀 body 之前依序檢查 access marker（401／403，含 admin）→ 限流（429，見 D6）→ 宣告的 media type（415；PATCH 只收 `application/merge-patch+json`，415 回應帶 `Accept-Patch`）→ 嚴格 JSON（400：語法錯、尾巴多字、NaN／Infinity、非 UTF-8，以 custom `Request.json()` 實作）；之後 Pydantic 驗證失敗回 422 `{pointer, code, detail}`。整體順序 404/405 > 413 > 401/403 > 429 > 415 > 400 > 422；413 由獨立的 body 上限 middleware 負責。route class 依 route 的 marker 與 body 自動把它負責的 status 寫進 OpenAPI。
  - 理由：Spring Security、ASP.NET Core、DRF、Kubernetes 都先驗身分與權限再解析 body；RFC 9110（415）、5789（`Accept-Patch`）、7396、8259。改變現況的兩點：403 提前到內容檢查之前（現在非 admin 送錯 body 先拿 422），415 補 `Accept-Patch`。被推翻的選項：只用 exception handler（匿名壞 JSON 先於 401、接受 NaN、415 只能猜）。
- **D3 匿名 401 的 body**（已決 2026-11-08，Will：選 A；擋住：4a）
  - A：統一用 access marker 現有的 `code: session_invalid`——只剩一條路徑；前端只看 status（`client.ts:23-28`），`session_invalid` 已在 enum 與訊息裡（建議）
  - B：保留舊的無 `code`、detail `this operation needs a signed-in session`——要多一個錯誤種類
- **D4 `/api` 前綴**（已決 2026-11-08，Will：選 A；擋住：4a、5）
  - A：paths 含 `/api`（router `prefix="/api"` 的原生產出），前端 baseUrl 改為 origin、path 字串加 `/api`——不需後處理規格（建議）
  - B：匯出時去掉前綴並加 `servers: [/api]`——前端不動，但多一段覆寫 `app.openapi` 的程式
- **D5 測試中的回應驗證**（已決 2026-11-08，Will：照簡報建議；擋住：4b；簡報 §4，事實：`spike.md` (e)）
  - 結論：openapi-core 降為 dev 依賴，`checked_client` 對產生的規格驗證每個測試回應（status、media type、header、body；`application/problem+json` 註冊 deserializer）；problem 改由 Pydantic `Problem` model 建構。schemathesis 維持延後。
  - 理由：規格由同一份 model 產生只讓成功 body 的驗證重複；錯誤 body、未宣告的 status、media type 仍會漂移（Schemathesis 的 conformance 檢查、ASP.NET `TypedResults` 為基準）。
- **D6 被擋下的請求是否扣限流額度**（已決 2026-11-08，Will：照簡報建議；擋住：4b；簡報 §5）
  - 結論：每個打到限流 operation 的請求都扣一個 token，含被 415、400、422 擋下的；限流改成 route class 讀取的 marker，位置在 access 檢查之後、內容檢查之前。額度維持 `AUTH_RATE_LIMIT_PER_MIN` 預設 5、註冊與登入共用一個 bucket。
  - 理由：Rails `rate_limit`、DRF throttle、ASP.NET `UseRateLimiter` 都在解析與驗證前計數；現行「被驗證擋下不扣」是 middleware 順序的副作用，且不一致（契約層 422 不扣、service 層 422／409 會扣）；猜密碼另由 `sign_in_throttles` 依帳號＋來源節流。重新評估的觸發：衍生專案上線後合法使用者因 422 撞到 429——調額度或拆 bucket，不改計數規則。
- **D8 匯出格式**（預設，可推翻）：JSON，`backend/api/openapi.json`，不需新增 pyyaml；dev 模式由 `/api/openapi.json` 與既有文件頁提供。

拍板順序：全部已決（D2、D5、D6 於 2026-11-08 依附件 `brief-d2-d5-d6.md` 的業界基準簡報拍板）。

## 進度

狀態值：未開始／進行中／程式完成待驗收／完成。

| 步驟 | 狀態 | 已跑的驗收 | 未跑的驗收與原因 | commit |
|---|---|---|---|---|
| 1 spike | 完成 | (a)–(g) 全部，指令與原始輸出在 `spike.md` | 無 | `docs(plans): record the step 1 spike of the code-first plan` |
| 2 套件改名 | 完成 | `make -C backend check` exit 0：354 passed、95 deselected，`lint-imports` 7 kept 0 broken，squawk 0 issues；`scripts/` 59 tests、`template/` 33 tests OK，`check_doc_refs.py`、`check_vercel_config.py` exit 0；mutation（複本）4 個全 killed：`app.cli.healthcheck` 加 `import fastapi`、分派器 `app.cli` 加 `import uvicorn`（light-healthcheck BROKEN），`app.main` import `app.migrations`、`app.core` import `app.http`（top-layers BROKEN）；`git diff -M100% --summary 4dbe59f HEAD -- backend/src/app` 中 `r20261103_baseline.py`、`versions/__init__.py`、`script.py.mako` 為 100% rename；以搬移後 commit 為 base，複本改 revision，`check_migrations_immutable.py` exit 1（killed）；消費端指令只剩 ADR `fastapi-backend-stack` D1、D2、D5 的段落（步驟 7 修訂）與新名稱 `app.main.check_routes` ；CI run 42501080365（`d08b183`）全綠：backend（含 db 測試）、frontend、generate、commit policy、compose stack（image + migrate + health）；`/code-review` findings 7（blocking 0）：fixed 2（`app.cli` 的死 `__main__` 區塊、AGENTS.md 分層表對 `logs.py` 的描述）、rejected 5（revision 檢查空窗 ×2：前提段 Will 2026-11-08 決定、以 100% rename 補足；ADR 舊名：步驟 7；`http` 與標準庫同名：沒有任何設定把 `src/app` 放進 `sys.path`；routes 併入 `main.py`：步驟 2 範圍、4b 刪除） | 無 | `0b4bff4`、`d08b183` |
| 3 固定 oasdiff | 完成 | 乾淨 clone＋diff：`make -C backend tools` → `oasdiff_1.35.0_darwin_all.tar.gz: OK`、`oasdiff version 1.35.0`，重跑 Nothing to be done；改錯 darwin_all checksum → `FAILED`、exit 2、沒留下 binary；改 `OASDIFF_VERSION` 後會重新下載（不沿用舊 binary）；`tool_versions.py bump oasdiff --version 1.35.0` 重寫後 Makefile 無差異（三個 checksum 與 release `checksums.txt` 相符）；`check` 回報 `oasdiff: 1.35.0 (current)`；`make -n tools OASDIFF_OS=linux` 在 arm64 選 `linux_arm64` 與對應 checksum；mutation（複本）：刪掉 `OASDIFF_SHA256_linux_amd64`，`verify` exit 1（killed）；`scripts/` 63 tests OK、`check_doc_refs.py`、`actionlint`、`make lint-docker` exit 0；`make -C backend check` exit 0（354 passed）；`/code-review` findings 7（blocking 0）：fixed 3（版本變更不重新下載，hadolint 一併改成含版本的檔名；release 少了 archive 時 `bump` 給明確訊息；測試 fixture 改名）、recorded 1（Linux 實際下載：步驟 6 CI 跑 `api-breaking` 時驗）、rejected 3（兩份下載規則合併：binary 與 tar 檔形狀不同；改成資料表、`uname` 延遲求值：nit） | Linux 上實際下載與解壓：本機是 macOS，步驟 6 的 CI run 補驗 | 見 git log |
| 4a 契約改由 FastAPI 產生 | 未開始 | | | |
| 4b 搬移驗證行為、移除 spec-first | 未開始 | | | |
| 5 前端跟上 | 未開始 | | | |
| 6 api-breaking 接上 CI | 未開始 | | | |
| 7 ADR 與規則文件 | 未開始 | | | |
| 8 收尾 | 未開始 | | | |
