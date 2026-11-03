# Spike (c): reflect the Atlas-applied schema with SQLAlchemy 2.0 async, then compare a hand-written model to it.
import asyncio, uuid, datetime
from sqlalchemy import MetaData, String, Text, ForeignKey, DateTime
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

URL = "postgresql+asyncpg://postgres:spike@localhost:55432/spike"

class Base(DeclarativeBase): pass
class Item(Base):
    __tablename__ = "items"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))

async def main():
    eng = create_async_engine(URL)
    async with eng.connect() as conn:
        md = MetaData()
        await conn.run_sync(md.reflect)
        print("tables:", sorted(md.tables))
        db = md.tables["items"]
        for c in db.columns:
            print(f"  items.{c.name}: {c.type} nullable={c.nullable} default={c.server_default is not None}")
        print("  fks:", [str(fk.target_fullname) + (" ondelete=" + str(fk.ondelete)) for fk in db.foreign_keys])
        print("  checks:", [str(c.sqltext) for c in db.constraints if c.__class__.__name__ == "CheckConstraint"])
        # model vs DB: names and nullability
        diff = [(c.name, c.nullable, db.c[c.name].nullable) for c in Item.__table__.columns
                if c.name not in db.c or c.nullable != db.c[c.name].nullable]
        missing = sorted(set(db.c.keys()) - set(Item.__table__.c.keys()))
        print("model/db nullability diffs:", diff, "db columns missing in model:", missing)
    await eng.dispose()
asyncio.run(main())
