# Database Design Rules

本專案使用 PostgreSQL 18、SQLAlchemy 2.0 async 加 asyncpg，schema 由 Alembic 管理（`docs/plans/2026-11-03-fastapi-template.md` 的 D2、D3）。命名、型別、FK、角色等 schema 規則沿用 Go 版（[ADR go-backend](../adr/2026-10-18-go-backend.md)），兩版的 schema 相同，由測試守住（`backend/tests/test_schema_db.py`）。

> 「查詢」相關的規則（下面提到 sqlc、`queries/*.sql`、`pgx` 的段落，以及 Transaction 一節）還是 Go 版的內容，FastAPI 版的寫法在計畫步驟 3 寫第一批查詢時改寫。

## Schema 與查詢的位置

- 每個模組擁有自己的表，宣告在 `backend/src/app/modules/<m>/models.py`（共用 `app.platform.db.Base`）：模型描述表的**目標狀態**，revision 必須和它一致（見 Migration）。
- `backend/src/app/main/schema.py` 的 `MODEL_MODULES` 列出所有有表的模組；新模組的 `models.py` 沒列進去時 `tests/test_schema.py` 會失敗。
- 跨模組的 FK 用字串（`ForeignKey("users.id")`），不建 ORM relationship（AGENTS.md「Backend layers」）。
- 所有表都在 `public` schema，DDL 不寫 schema 前綴。
- 查詢只寫在 `internal/modules/<m>/queries/*.sql`，由該模組的 `sqlc.yaml` 生成到 `internal/modules/<m>/internal/store`（`make -C backend sqlc`）。生成碼不手改；`make -C backend sqlc-diff` 擋下過期的生成碼。
- ~~**NEVER** 在 Go 裡組 SQL 字串或用 ORM；每個查詢都經 sqlc 生成（ADR go-backend D2）。~~（Go 版規則，已失效：FastAPI 版用 SQLAlchemy，D3；步驟 3 改寫）
- sqlc 的 uuid 一律 override 成 `github.com/google/uuid.UUID`，`timestamptz` 成 `time.Time`，nullable 欄位生成指標（`emit_pointers_for_null_types: true`）。

## 命名規範

### Table 與 Column

- 表名用**複數 snake_case**：`users`、`sessions`、`sign_in_throttles`、`order_items`。
- 欄位名用 **snake_case**：`created_at`、`owner_id`、`is_disabled`。
- Boolean 欄位用 `is_` / `has_` 前綴：`is_disabled`、`has_paid`。
- 時間欄位用 `_at` 後綴：`created_at`、`last_used_at`、`expires_at`。

### Foreign Key

- 欄位名是 `<被參照的表單數>_id`（`user_id → users.id`），或表達角色的 `<角色>_id`（`owner_id → users.id`）。
- 必須明確寫 `NOT NULL` 或允許 NULL。
- 指向 `users` 的 FK 必須寫明刪除行為：使用者擁有的資料 `ON DELETE CASCADE`（`sessions.user_id`、`items.owner_id`），帳號刪除時一起刪掉；比帳號活得久的參照用 `ON DELETE SET NULL`。沒寫（`NO ACTION`）或 `RESTRICT` 會讓刪除帳號失敗，`tests/test_schema.py` 的 `test_foreign_keys_to_users_allow_deleting_the_account` 擋下。
- 其他 FK 預設不 cascade：刪除仍被參照的 parent 會失敗，而不是靜默刪掉 child；只有純附屬資料才 `ON DELETE CASCADE`。

### Index

- 名稱照 PostgreSQL 自動命名的格式，在模型的 `Index(...)` 明寫：一般索引 `<table>_<columns>_idx`，unique 索引 `<table>_<column>_key`。
- 為 keyset 分頁建複合索引，欄位與查詢的 `WHERE` 和 `ORDER BY` 一致。

```python
# 正確：app/modules/items/models.py 與 app/modules/auth/models.py 的 __table_args__
# keyset pagination: WHERE owner_id = $1 AND id < $cursor ORDER BY id DESC
Index("items_owner_id_id_idx", "owner_id", text("id DESC"))

Index("sessions_id_key", "id", unique=True)
```

## 模組邊界

- 跨模組只允許 FK 指向 `users`（ADR go-backend Invariants）。
- 模組的查詢**NEVER** `SELECT` 或 `JOIN` 其他模組的表。需要別的模組的 schema 才能解析 FK 時，`sqlc.yaml` 的 `schema` 可以列出它（items 列出 `../auth/schema.sql`），並設 `omit_unused_structs: true`，但查詢仍不讀那些表。
- 需要別的模組的資料時，經由該模組的 service 取得。

## 型別選擇

照 [PostgreSQL wiki「Don't Do This」](https://wiki.postgresql.org/wiki/Don%27t_Do_This)（ADR go-backend D3）：

| 場景 | 使用 | 避免 |
|------|------|------|
| 主鍵 | `uuid PRIMARY KEY DEFAULT uuidv7()` | `serial`、`int` 自增 |
| 文字（有長度限制） | `text` + `CHECK (char_length(x) BETWEEN m AND n)` | `varchar(n)`、`char(n)` |
| 文字（無限制） | `text` | `varchar` |
| 狀態、role、列舉 | `text` + `CHECK (x IN (...))` | PG `ENUM` 型別、沒有 CHECK 的 `text` |
| 時間 | `timestamptz` | `timestamp`（不帶時區） |
| 整數計數 | `bigint` | `int` |
| 金額 | `numeric(precision, scale)` | `float`、`real`、`money` |
| 布林 | `boolean` | `smallint`、`'Y'/'N'` |
| 二進位（雜湊等） | `bytea` + `CHECK (octet_length(x) = n)` | hex 字串存 `text` |

- 主鍵由 DB 的 `uuidv7()`（PostgreSQL 18 內建）產生，JSON 裡是字串。UUIDv7 依時間排序，可以直接當 keyset 分頁的排序鍵（ADR go-backend D3）。
- 長度與 enum 的 CHECK 數值必須和 `backend/api/openapi.yaml` 一致：契約是第一道防線，CHECK 是第二道，任何不合長度或 enum 的資料在 DB 層就寫不進去（ADR go-backend Expected Outcome）。
- 新增狀態值時，在同一個 PR 改契約的 `enum`、模型的 `CheckConstraint`，並產生 revision（autogenerate 抓不到 CHECK 的變更，要手動補進 revision；`tests/test_schema_db.py` 的 catalog 比對會擋下漏補）。

```python
# 正確：app/modules/auth/models.py
class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "account = lower(account) AND char_length(account) BETWEEN 3 AND 50", name="users_account_check"
        ),
        CheckConstraint("role IN ('admin', 'user')", name="users_role_check"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, server_default=UUIDV7)
    account: Mapped[str] = mapped_column(TEXT, unique=True)
    password_hash: Mapped[str] = mapped_column(TEXT)
    role: Mapped[str] = mapped_column(TEXT, server_default=text("'user'"))
    is_disabled: Mapped[bool] = mapped_column(BOOLEAN, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, server_default=NOW)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, server_default=NOW)
```

### JSONB 與 Array

- JSONB 只用於彈性結構的資料（偏好設定、外部系統的原始 payload）；使用前必須能說明為何不用 table + FK。**NEVER** 在 JSONB 塞可以正規化的關聯資料。
- 需要查詢 JSONB 或 array 的內容時，建 GIN 索引。
- 簡單的多值屬性（標籤）可以用 `text[]`，不用 JSON 存字串列表。

## 時間戳記

- 對外的資源表（API 回傳 `updated_at` 的）都有 `created_at`、`updated_at timestamptz NOT NULL DEFAULT now()`。
- **這些表的每個 UPDATE 查詢都明寫 `updated_at = now()`**，不靠 trigger，也不由 Go 傳值；並有測試覆蓋（`TestUpdatedAtChanges`）（ADR go-backend D3）。
- 例外：內部的狀態表可以由 service 傳入時間，讓測試控制時鐘（`sign_in_throttles.updated_at` 與 `sessions.last_used_at` 由 `@now` 傳入，限流與 idle 期限的測試因此不用等真實時間）。
- 連線的 `TimeZone` 固定為 UTC。

```sql
-- 正確：internal/modules/auth/queries/users.sql
UPDATE users
SET is_disabled = @is_disabled, updated_at = now()
WHERE id = @id
RETURNING ...;
```

## Migration

Alembic（ADR go-backend D2 在 FastAPI 版改選 Alembic，見 `docs/plans/2026-11-03-fastapi-template.md` 的 D2）。表定義在各模組的 `models.py`，revision 在 `backend/src/app/main/migrations/versions/`。

### 產生

- 改 `models.py` 後產生 revision：root `make generate-migration MSG=<name>`，或 `make -C backend migration MSG=<name>`（需要 Docker：它用 db 測試同一個 PostgreSQL 容器，先套用既有 revision，再和模型比對）。產生的檔案要讀過再 commit：autogenerate 抓不到 CHECK 約束與部分 default 的變更，這些要手動補進 revision。
- 套用：root `make migration`（compose 的 `migrate` service 執行 `app migrate`）；`make up`、`make dev` 也會在 backend 啟動前先跑它。
- 兩道測試守住模型與 revision 一致（只在 CI 跑，`make -C backend test-db`）：`alembic check`，以及「從模型 `create_all` 建的資料庫」和「套用 revision 建的資料庫」的 catalog 比對（表、欄位、約束、索引、表註解；`tests/test_schema_db.py`）。revision 漏了任何東西，CI 就紅。
- revision 只往前（沒有 `downgrade()`，`tests/test_schema.py` 檢查）：回退是舊 image 配新 schema，見下面的相容規則。
- 可以手改產生的 revision，但只限兩種，改完一樣要通過上面的比對：
  - 純資料的 backfill（對既有表走 expand 步驟時），用 `op.execute`。
  - autogenerate 產生的 DDL 會在掃表時持有阻擋寫入的鎖時，改寫成不鎖表的步驟。例：FK 或 CHECK 先 `ADD CONSTRAINT … NOT VALID`，下一支 revision 才 `VALIDATE CONSTRAINT`。

### 不可修改

- 已推上 `dev` 或合併進 `main` 的 revision 只能新增，**NEVER** 修改、刪除或改名；要改就新增一支。`make -C backend migrations-immutable`（`make check` 的一部分）以 `BASE`（本機預設 `origin/main`）的 merge-base 為基準逐檔比對（`scripts/check_migrations_immutable.py`）；CI 對 push 用該分支上一次的 tip、對 PR 用 base 分支，所以推上 `dev` 的 revision 也不能再改。
- 還沒推上去的 revision 可以刪掉重新產生。

### 與上一版相容（expand／contract）

- 每支 revision 都要讓**上一版的 backend** 照常運作：部署是先跑 `app migrate` 再換 backend，這段時間跑的是舊程式；revision 只往前套用，回退也是舊 image 配新 schema。
- 舊程式還在用的結構不能在同一版刪除或改形：刪或改名欄位與表、換主鍵或 `ON CONFLICT` 依賴的唯一約束、把欄位改成舊程式寫不進去的型別或約束。做法是拆成兩版（Martin Fowler「Parallel Change」、GitLab「Avoiding downtime in migrations」）：
  - **expand**：新增新結構，新程式只用它，舊結構原封不動（例：Go 版以 `sign_in_throttles` 取代 `login_throttles`，舊表在下一個 PR 才刪除）。
  - **contract**：新程式上線之後的下一版，才刪掉舊結構。範本的「一版」就是合併進 `main` 的一個 PR。
- squawk 擋下這類變更（`ban-drop-column`、`ban-drop-table`、`renaming-*` 等）：`make -C backend squawk`（`make check` 的一部分，不需要資料庫）用 Alembic 的 offline 模式把每支 revision 輸出成各自的 SQL 檔（`tests/revision_sql.py`），再交給 squawk（設定在 `backend/.squawk.toml`）。只有兩種情況可以在 revision 裡用 `op.execute("-- squawk-ignore <rule>")` 寫在那個 statement 前面，並在上一行註解寫理由：contract 步驟（寫出舊程式在哪一版停止使用它），或同一個 statement 立刻補回等價的約束。ignore 的數量鎖在 `scripts/quality-locks.json`。

### 鎖與安全（squawk）

- 對既有的表建或刪索引一律 `CONCURRENTLY`：`op.create_index(..., postgresql_concurrently=True)`，包在 `with op.get_context().autocommit_block():` 裡（`CONCURRENTLY` 不能在 transaction 裡執行）。沒有 `CONCURRENTLY` 時 squawk 擋下（`require-concurrent-index-creation`）。
- 對已有資料的表加帶常數 default 的 `NOT NULL` 欄位（`ADD COLUMN n bigint NOT NULL DEFAULT 0`）是安全的：PostgreSQL 11 起不重寫表，squawk 也放行。會重寫表或讓既有列違反約束的是 volatile default（例如 `DEFAULT uuidv7()`）與沒有 default 的 `NOT NULL` 欄位，squawk 會擋下。這兩種改走 expand 步驟，每一步是一支 revision：先加 nullable、沒有 default 的欄位 → 手寫 backfill。需要 default 時，backfill 之後再加：`ALTER COLUMN … SET DEFAULT` 只影響之後的新列，不重寫表。
- 對已有資料的表補 `NOT NULL` 或加 FK、CHECK：照上面的例外手寫不鎖表的步驟。`NOT NULL` 的步驟是 `CHECK (x IS NOT NULL) NOT VALID` → 下一支 `VALIDATE CONSTRAINT` → 再下一支 `SET NOT NULL` 並 `DROP CONSTRAINT` 那個 CHECK（PostgreSQL 12 起 `SET NOT NULL` 會用已驗證的 CHECK 跳過掃表）。
- **NEVER** 在 revision 裡寫 `SET lock_timeout`：`app migrate` 已在連線上設定 `lock_timeout` 5s（`app.platform.config.MigrateSettings`），每支 revision 在自己的 transaction 裡執行（`migrations/env.py` 的 `transaction_per_migration`）。

## DB 角色與權限

照 OWASP 最小權限（ADR go-backend D5）：

- `POSTGRES_USER`（superuser）只用於初始化與容器內維運，app **NEVER** 用它連線。
- migrate 用 owner 角色（`DB_OWNER_USER`，例如 `template_fastapi_nextjs_owner`）：擁有 database 與 `public` schema，不是 superuser，經 `MIGRATE_DATABASE_URL` 連線。
- `serve` 與 `seed-admin` 用 app 角色（`DB_APP_USER`，例如 `template_fastapi_nextjs_app`），只有 DML，經 `DATABASE_URL` 連線。
- 角色由 `deploy/postgres/initdb/10-roles.sh` 建立；授權（含 `ALTER DEFAULT PRIVILEGES`）由 `app migrate` 每次套用後冪等執行，所以新表自動給 app 角色 DML。
- Alembic 的 `alembic_version` 放在 owner 擁有的 `alembic` schema，app 角色沒有那個 schema 的 `USAGE`，改不了套用過哪些 revision（`tests/test_migrate_db.py`）。
- 密碼不放在 URL 或環境變數：用 `*_FILE`（compose secrets 掛檔）傳入，URL 只寫使用者名稱。
- db 測試（testcontainers，與 compose 同一個 PostgreSQL image，`backend/tests/dbtest.py`）使用同一套角色，只在 CI 跑。

## 連線設定

`app.platform.db.create_engine` 建立 SQLAlchemy 的 async engine（asyncpg），設定只有以下這些：

| 設定 | 來源 | 預設 |
|------|------|------|
| `pool_size`（`max_overflow=0`） | `DB_MAX_CONNS` | 20 |
| `statement_timeout` | `DB_STATEMENT_TIMEOUT` | 5s |
| `lock_timeout` | 常數 | 3s |
| `idle_in_transaction_session_timeout` | 常數 | 30s |
| `pool_pre_ping`、`pool_recycle` | 常數 | 開、1h（取出前確認連線還活著，與 pgxpool 的預設對齊） |
| `TimeZone` | 常數 | UTC |

- 這些以 asyncpg 的 `server_settings` 設在每條連線上，讓失控的查詢、排隊等鎖或忘了結束的 transaction 佔不住連線。
- 密碼不放進連線 URL（`DATABASE_PASSWORD` 或 `DATABASE_PASSWORD_FILE`）；`sslmode=verify-full` 用系統的 CA 驗證伺服器憑證與主機名稱，其他 sslmode 必須同時設 `DATABASE_PLAINTEXT=same-host`（`app.platform.config`）。
- engine 是 lazy 連線：DB 暫時連不上不會讓啟動失敗，由 readiness（`/health/ready`，container healthcheck 打的就是它）回報。
- 所有服務的 `DB_MAX_CONNS` 總和不得超過 PostgreSQL 的 `max_connections`。
- migrate 連線改設 `lock_timeout` 5s、不設 `statement_timeout`（`app.platform.config.MigrateSettings`）。

## Transaction

- 需要多個語句原子完成的 service 方法，自己用 `pgx.BeginFunc` 開 transaction，並以 `store.New(tx)` 取得查詢。單一語句不用 transaction。
- **NEVER** 在 transaction 內呼叫外部服務（HTTP、HIBP、第三方 API）或做耗時的 CPU 工作（密碼雜湊）：transaction 持有連線與鎖，回應時間不可控會耗盡 pool，也會撞上 `idle_in_transaction_session_timeout`。先在 transaction 外做完，再開 transaction 寫入。
- 需要在 commit 之後才做的事（通知、webhook），放在 `BeginFunc` 回傳成功之後。

```go
// 正確：internal/modules/auth/auth.go 的 CreateUser，雜湊在 transaction 外
hash, err := s.newPasswordHash(ctx, password)
if err != nil {
	return User{}, Session{}, err
}
err = pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error {
	q := store.New(tx)
	row, err := q.CreateUser(ctx, store.CreateUserParams{Account: accountKey(account), PasswordHash: hash, Role: RoleUser})
	// ...
	sess, err = s.newSession(ctx, q, u.ID)
	return err
})

// 錯誤：transaction 內呼叫外部服務
err = pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error {
	if _, err := store.New(tx).CreateOrder(ctx, params); err != nil {
		return err
	}
	return s.notifier.Send(ctx, userID, "order created") // 禁止
})
```

## 禁止事項

- **NEVER** 手寫 DDL revision（不鎖表的改寫與 backfill 例外），或修改、刪除、改名已合併進 `main` 的 revision
- ~~**NEVER** 繞過 sqlc：在 Go 裡組 SQL 字串、用 ORM、手改生成碼~~（Go 版規則，已失效：FastAPI 版用 SQLAlchemy，D3；查詢規則在步驟 3 改寫）
- **NEVER** 在模組的查詢讀其他模組的表；**NEVER** 新增指向 `users` 以外的跨模組 FK
- **NEVER** 用 `serial` 或 `int` 自增當主鍵；主鍵一律 `uuid DEFAULT uuidv7()`
- **NEVER** 用 `varchar(n)`；有長度限制用 `text` + CHECK，數值與契約一致
- **NEVER** 用 PG `ENUM` 型別，或讓狀態欄沒有 `CHECK (x IN (...))`
- **NEVER** 存不帶時區的時間；**NEVER** 用 `float` 存金額
- **NEVER** 對資源表寫一個不設 `updated_at = now()` 的 UPDATE 查詢
- **NEVER** 對既有的表直接加 volatile default 或沒有 default 的 `NOT NULL` 欄位，或不用 `CONCURRENTLY` 建索引
- **NEVER** 讓 app 用 owner 或 superuser 角色連線；**NEVER** 把 DB 密碼寫進 URL
- **NEVER** 在 JSONB 中塞可以正規化的關聯資料
- **NEVER** 在 DB transaction 內呼叫外部服務或做密碼雜湊這類耗時工作
