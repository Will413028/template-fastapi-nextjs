"""Alembic's environment: runs the revisions on the connection app.main.migrate hands over.

There is no alembic.ini and no URL here; the caller opens the connection as the owner role (app.main.migrate) and
passes it in `config.attributes["connection"]`, so credentials never pass through Alembic's configuration. Offline
mode (`--sql`, tests/revision_sql.py) needs no database: it prints each revision's SQL for squawk.
"""

from alembic import context

from app.main.migrate import VERSION_SCHEMA
from app.main.schema import metadata

if context.is_offline_mode():
    context.configure(
        dialect_name="postgresql",
        literal_binds=True,
        target_metadata=metadata(),
        transaction_per_migration=True,
        version_table_schema=VERSION_SCHEMA,
    )
else:
    context.configure(
        connection=context.config.attributes["connection"],
        target_metadata=metadata(),
        # each revision commits on its own, so a failure leaves the earlier ones applied and a re-run continues from it
        transaction_per_migration=True,
        version_table_schema=VERSION_SCHEMA,
        compare_server_default=True,
    )
with context.begin_transaction():
    context.run_migrations()
