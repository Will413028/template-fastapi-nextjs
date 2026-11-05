---
title: FastAPI 版後端：契約優先、SQLAlchemy async＋Alembic、按功能分模組，以 template-go-nextjs@560106d 為分家點
date: 2026-11-05
review-date: 2027-05-05
status: active
supersedes: "Go 版範本的後端 ADR（不適用本 repo；仍成立的型別與 DB 角色規則移入本 ADR 的 D6、D7）"
tags: [fullstack-template, decision, fastapi, postgresql, migration]
---

# FastAPI 版後端：契約優先、SQLAlchemy async＋Alembic、按功能分模組，以 template-go-nextjs@560106d 為分家點

## Context

這個 repo 從 template-go-nextjs@560106d 分出來：當時的 HTTP 契約與 Next.js 前端照搬，backend 改用 FastAPI，規格是那個 commit 的行為。之後 Go 版改用 Protobuf＋Connect（Go 版範本的契約 ADR），兩版從此各自演進（D8）。用 `make new` 產生的專案和範本沒有連結，所以這裡的每個預設都是衍生專案的起點。

**約束**：

- `external`：Will 要一個 Python 後端的範本，前端與契約起點和 Go 版相同（2026-11-03）；Go 版換成 Connect 後，Will 決定不跟，維持 OpenAPI（2026-11-05）。
- `external`：範本沒有部署、沒有線上資料，沒有相容性窗口要顧。
- `inherited`：Go 版的 schema 規則（型別、主鍵、DB 角色；Go 版範本的後端 ADR）。仍成立：理由來自 PostgreSQL wiki 與 OWASP，和語言無關。baseline revision 等於 Go 版 `560106d` 的 6 支 migration（分家前由 catalog 比對測試證明，最後一次在 `064a24a` 的 CI；D8 之後那支測試與 Go 的 SQL 刪除，baseline 由 revision 不可修改的檢查固定），之後的 schema 各自演進。

## Options Considered

- **契約來源**：基準是 FastAPI code-first，從 Pydantic 模型產生 OpenAPI（[FastAPI 文件](https://fastapi.tiangolo.com/tutorial/first-steps/)）；A 是手寫 `backend/api/openapi.yaml`（照搬 Go 版）為唯一來源，用 openapi-core 驗證 request。
- **Pydantic 模型**：從契約產生（datamodel-code-generator）或手寫。
- **Migration**：基準是 Alembic（SQLAlchemy 的標準 migration 工具，[Alembic 文件](https://alembic.sqlalchemy.org/)）；另一個選項是沿用 Go 版的 Atlas、`schema.sql` 與 6 支 migration。
- **資料存取**：基準是 SQLAlchemy 2.0 async；另一個選項是 psycopg 3 加手寫 SQL（最接近 Go 版的 sqlc）。
- **分層**：基準是按功能分模組的模組化單體（[zhanymkanov/fastapi-best-practices](https://github.com/zhanymkanov/fastapi-best-practices)、Netflix Dispatch）；照 Go 版集中 HTTP（`app.http` 放全部 router）；六角形架構（Cosmic Python）。
- **與 Go 版的關係**（Go 版改用 Connect 之後）：跟上 Connect（proto 照搬、Python Connect server、前端照搬）；以 `560106d` 分家、維持 OpenAPI；先不決定。
- **前端 E2E**：不加（同 Go 版，靠 CI stack job 的登入流程）；或加 Playwright。

## Decision

- **D1 契約**：手寫 `backend/api/openapi.yaml` 是唯一來源，FastAPI 自己產生的 schema 關掉。`app.web.validation` 用 openapi-core 在每個 `/api` request 進入操作前驗證；測試另驗證 response（`tests/apitest.py`）。
- **D2 模型**：`make -C backend gen` 用 datamodel-code-generator 從契約產生 `app/web/contract_models.py`，`gen-check` 在 `make check` 與 CI 比對是否過期。
- **D3 Migration**：Alembic。revision 由 autogenerate 從 `models.py` 產生；兩種例外手寫：autogenerate 抓不到的 CHECK 與 default、會鎖表的 DDL 改寫成不鎖表的步驟，以及純資料 backfill。squawk 檢查每支 revision 的 offline SQL；推上 `dev` 或合併進 `main` 的 revision 只能新增（`scripts/check_migrations_immutable.py`）。baseline revision 等於 Go 版 6 支 migration 的結果，由 catalog 比對測試守住（`tests/test_schema_db.py`）。
- **D4 資料存取**：SQLAlchemy 2.0 async 加 asyncpg；交易邊界由 service 負責，repository 層可選。
- **D5 分層**：`app.main` > `app.modules.<name>`（`router` > `service` > 可選的 `repository` > `models`，模組互不 import）> `app.web` > `app.platform`，由 import-linter 的 `layers`、`independence`、`acyclic_siblings`、`forbidden` 合約守住，`ignore_imports` 為 0。跨模組經 `app.platform`，外鍵用字串 `"users.id"`、不用 ORM relationship。
- **D6 型別與主鍵**（沿用 Go 版）：主鍵 `uuid DEFAULT uuidv7()`，JSON 裡是字串；文字欄 `text`，有長度限制時加 CHECK；狀態與 role 欄 `text` 加 `CHECK (x IN (...))`；時間一律 `timestamptz`。
- **D7 DB 角色**（沿用 Go 版）：`POSTGRES_USER` 只用於初始化與維運；`app migrate` 用 `<app>_owner`（擁有 database 與 `public`，非 superuser）；`serve`、`seed-admin`、`recover-admin` 用 `<app>_app`（只有 DML）。授權由 `app migrate` 每次套用後冪等執行；密碼以 compose secret 掛檔、經 `*_FILE` 讀取。
- **D8 與 Go 版分家**（2026-11-05 修訂，取代原本的共用檔比對腳本）：`560106d` 是最後的共同點，不再比對共用檔；Go 版之後的前端修正需要時手動挑著搬。
- **D9 E2E**：不加；CI stack job 用 backend image 跑 `scripts/stack_auth_smoke.py` 的登入流程。

## Rationale

- **D1、D2**：不選 code-first 基準：spike 實測 FastAPI 產生的 schema 對不上 Go 版（`createItem` 只有 201、422，422 不是 RFC 9457 problem；`ItemPatch.title` 變成可為 null；`x-extensible-enum` 不見），前端就要改，兩個範本的契約也不能直接 diff。代價：用不到 FastAPI code-first 的寫一次就好，契約與模型靠產生工具與 `gen-check` 對齊，多一個開發依賴。
- **D3**：spike 證明沿用 Atlas 可行，Will 仍選 Alembic：Python 開發者熟悉，能從模型自動產生。代價：6 支 migration 重寫成一支 baseline，「schema 與 Go 版相同」從共用檔變成要靠測試守住；CI 的 migration 檢查（squawk、不可修改）重做一套。
- **D4**：SQLAlchemy 是 Python 生態最完整的選擇；不選 psycopg 手寫 SQL，因為對應與交易都要自己處理。代價：查詢不像 sqlc 那樣逐條可審閱，複雜查詢放 repository 層。
- **D5**：Go 版集中 HTTP 是因為 oapi-codegen 只產生一個 `ServerInterface`，D1 下的 Python 沒有這個限制；集中會讓改一個功能要動兩處。六角形架構對 CRUD 規模是多出來的樣板。代價：分層靠 import-linter 合約維護，新增模組要照 `docs/rules/add-module.md`。
- **D6、D7**：UUIDv7 不洩漏資料量、對 B-tree 友善；CHECK 讓資料完整性由資料庫保證（[PostgreSQL wiki「Don't Do This」](https://wiki.postgresql.org/wiki/Don%27t_Do_This)）。應用程式帳號只有最小權限、不擁有資料庫（OWASP Database Security Cheat Sheet）。代價：ID 16 bytes 且透露建立時間；多一個 `app migrate` 子指令與兩組密碼。
- **D8**：不跟 Connect：Go 版選 Connect 的決定性理由是和 hawkr 共用一套工具，那個理由對 Python 不成立，技術上 OpenAPI 與 Connect 相近；跟上要重寫 21 個操作的 HTTP 層與測試，Python Connect 實作的成熟度也沒查證。原本的比對腳本（只在週回顧跑）在 Go 版換契約後滿版差異，剩下真正共用的只有 LICENSE、`SECURITY.md`、`security.yml` 這類，不值得一支腳本，所以刪掉。代價：兩版前端漸行漸遠，Go 版的修正要人記得去看。
- **D9**：E2E 的 CI 時間與維護成本高，Go 版也沒有，兩版會不對稱。代價：UI 流程沒有自動化測試，靠 vitest 與 stack job 的 API 層登入流程。

## Expected Outcome

<!-- >>> template-only -->
- 用 `make new` 產生的新專案，`make -C backend check` 與 `pnpm test` 直接綠燈，repo 裡不殘留佔位名稱（CI generate job）。
<!-- <<< template-only -->
- 不符合長度、enum 的資料在 request 驗證與資料庫兩層都寫不進去。

## Invariants

- 契約只在 `backend/api/openapi.yaml`：`frontend/scripts/gen-api.mjs`、`pnpm gen:api:check`、`make -C backend gen-check`。
- models 與 revisions 一致：`backend/tests/test_schema_db.py`；baseline 不再改：`scripts/check_migrations_immutable.py`。
- 分層：`backend/.importlinter`。

## Revocation Triggers

- FastAPI 或 openapi-core 能從手寫契約直接產生 route 與驗證（不必兩套）→ 重新評估 D1、D2。
- 有 Python 專案要和 hawkr 共用 Connect 契約工具 → 重新評估 D8（改用 Connect）。
- 某模組要同步呼叫另一模組 → D5 加 `<m>/api.py` 與 `protected` 合約；業務規則超出 CRUD → 只對它改成 ports and adapters。
- 衍生專案用 managed Postgres（initdb 不執行）→ D7 的角色改由 runbook 建立。

## Review Notes

## Related

<!-- >>> template-only -->
- 來源：FastAPI 版計畫 `docs/plans/2026-11-03-fastapi-template.md` D1–D5、D7、D11（前六項 Will 2026-11-03 拍板；最後一項 Will 2026-11-05 拍板，修訂本 ADR 的 D8），spike 結果 `docs/plans/2026-11-03-fastapi-template/spike.md`。
<!-- <<< template-only -->
- 取代：Go 版範本的後端 ADR（原文在 template-go-nextjs@560106d）。
- 同批：[部署拓樸](2026-11-05-vercel-rewrite-and-cloudflare-tunnel.md)；[API 與認證基準](2026-10-18-api-and-auth-baseline.md)。
