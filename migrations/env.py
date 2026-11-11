from logging.config import fileConfig

from alembic import context
from sqlalchemy import inspect

from collector.db.models import Base, UTCDateTime
from collector.settings import DATABASE_URL, engine

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Tables from the pre-Alembic schema. If these exist, the target is an old database
# that must be copied with scripts/migrate_sqlite.py, not upgraded in place.
LEGACY_TABLES = {"price_snapshots", "itad_price_history"}


def render_item(type_, obj, autogen_context):
    """Write UTCDateTime as a plain timestamptz so migrations don't import app code."""
    if type_ == "type" and isinstance(obj, UTCDateTime):
        return "sa.DateTime(timezone=True)"
    return False


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it ('alembic upgrade head --sql')."""
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        render_item=render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Tests pass their own connection in; normal runs use DATABASE_URL.
    connection = config.attributes.get("connection")
    if connection is None:
        with engine.connect() as connection:
            _run(connection)
    else:
        _run(connection)


def _run(connection) -> None:
    legacy = LEGACY_TABLES & set(inspect(connection).get_table_names())
    if legacy:
        raise RuntimeError(
            f"{connection.engine.url} has old-schema tables {sorted(legacy)}. "
            "Point DATABASE_URL at a new database and copy the data with "
            "scripts/migrate_sqlite.py instead of upgrading this one."
        )
    # render_as_batch lets future ALTERs work on SQLite, which can't alter columns in place.
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
        render_item=render_item,
    )
    with context.begin_transaction():
        context.run_migrations()
    connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
