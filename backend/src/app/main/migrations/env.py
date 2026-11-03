"""Alembic's environment: runs the revisions on the connection app.main.migrate hands over.

There is no alembic.ini and no URL here; the caller opens the connection as the owner role (app.main.migrate) and
passes it in `config.attributes["connection"]`, so credentials never pass through Alembic's configuration.
"""

from alembic import context

from app.main.migrate import VERSION_SCHEMA
from app.main.schema import metadata

context.configure(
    connection=context.config.attributes["connection"],
    target_metadata=metadata(),
    # each revision commits on its own, so a failure leaves the earlier ones applied and a re-run continues from it
    transaction_per_migration=True,
    compare_server_default=True,
)
with context.begin_transaction():
    context.run_migrations()
