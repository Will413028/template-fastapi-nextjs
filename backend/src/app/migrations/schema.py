"""The whole database schema as SQLAlchemy metadata: what autogenerate and `alembic check` compare the database with."""

import importlib
from typing import TYPE_CHECKING

from app.core.db import Base

if TYPE_CHECKING:
    from sqlalchemy import MetaData

# Every module that declares tables; tests/test_schema.py fails when a module's models.py is missing here.
MODEL_MODULES = (
    "app.modules.auth.models",
    "app.modules.items.models",
)


def metadata() -> MetaData:
    """The shared metadata, with every module's tables registered by importing its models."""
    for module in MODEL_MODULES:
        importlib.import_module(module)
    return Base.metadata
