"""Writes each Alembic revision's SQL to <dir>/<revision file>.sql, for squawk (`make -C backend squawk`).

Alembic's offline mode renders the SQL without a database, so this runs on any machine. Each revision gets its own
file, wrapped in the BEGIN and COMMIT app migrate runs it in, so squawk judges every revision on its own.
"""

import io
import pathlib
import re
import sys

from alembic import command
from alembic.script import ScriptDirectory

from app.main.migrate import VERSION_SCHEMA, alembic_config

# Alembic creates its bookkeeping table ahead of the first revision. It is Alembic's, not a revision's (VARCHAR(32),
# which squawk's prefer-text-field would flag), so it is cut out; failing to find it fails the script, so a change
# in Alembic's output cannot slip past.
VERSION_TABLE = re.compile(rf"CREATE TABLE {VERSION_SCHEMA}\.alembic_version \(.*?\);\n", re.DOTALL)


def write(out: pathlib.Path) -> list[pathlib.Path]:
    written: list[pathlib.Path] = []
    # walk_revisions goes from head to base; squawk reads them oldest first
    for rev in reversed(list(ScriptDirectory.from_config(alembic_config()).walk_revisions())):
        buffer = io.StringIO()
        config = alembic_config(output=buffer)
        target = rev.revision if rev.down_revision is None else f"{rev.down_revision}:{rev.revision}"
        command.upgrade(config, target, sql=True)
        sql = buffer.getvalue()
        if rev.down_revision is None:
            sql, found = VERSION_TABLE.subn("", sql)
            if found != 1:
                msg = f"expected Alembic's CREATE TABLE {VERSION_SCHEMA}.alembic_version once, found it {found} times"
                raise RuntimeError(msg)
        path = out / f"{pathlib.Path(rev.path).stem}.sql"
        path.write_text(sql, encoding="utf-8")
        written.append(path)
    return written


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        sys.stderr.write("usage: revision_sql.py <dir>\n")
        return 2
    out = pathlib.Path(argv[0])
    out.mkdir(parents=True, exist_ok=True)
    for path in write(out):
        sys.stdout.write(f"{path}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
