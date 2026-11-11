"""Alembic environment and revisions (app.cli.migrate runs them)."""

# Alembic's bookkeeping table lives in a schema of its own, owned by the owner role and never granted to the app role,
# so the application cannot rewrite which revisions ran (the Go version keeps Atlas's table out of public likewise).
VERSION_SCHEMA = "alembic"
