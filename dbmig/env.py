import os # Add this import
from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from alembic import context
from alembic.script import ScriptDirectory
from alembic.script.revision import ResolutionError

# Import your Base object here
from core.database import Base # Corrected import path for Base

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
target_metadata = Base.metadata # Change this line

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table="alembic_version_backend",
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    # Dynamically construct the database URL from environment variables
    # Prefer DB_* if provided; fall back to MARIADB_* for backward compatibility
    db_host = os.environ.get("DB_HOST") or os.environ.get("MARIADB_HOST", "mariadb")
    db_port = os.environ.get("DB_PORT") or os.environ.get("MARIADB_PORT", "3306")
    db_user = os.environ.get("DB_USER") or os.environ.get("MARIADB_USER", "coe_user")
    db_password = os.environ.get("DB_PASSWORD") or os.environ.get("MARIADB_PASSWORD", "coe_password")
    db_name = os.environ.get("DB_NAME") or os.environ.get("MARIADB_DATABASE", "coe_db")

    db_url = f"mysql+pymysql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"

    connectable = engine_from_config(
        {"sqlalchemy.url": db_url}, # Use the dynamically constructed URL
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    script_directory = ScriptDirectory.from_config(config)
    version_table = "alembic_version_backend"

    def prune_overlapping_versions(connection) -> None:
        """Remove ancestor revisions lingering in the version table.

        When a previous run leaves both a head and one of its ancestors in the
        version table, Alembic treats them as independent targets and aborts
        with an "overlaps" error. Deleting the ancestor rows restores the
        expected single-head state while leaving true multi-branch situations
        untouched.
        """

        try:
            rows = connection.execute(
                text(f"SELECT version_num FROM {version_table}")
            ).fetchall()
        except SQLAlchemyError:
            return

        version_ids = [row[0] for row in rows if row[0]]
        if len(version_ids) <= 1:
            return

        ancestors_cache: dict[str, set[str]] = {}

        def ancestor_set(revision_id: str) -> set[str]:
            if revision_id in ancestors_cache:
                return ancestors_cache[revision_id]

            stack = [revision_id]
            seen: set[str] = set()

            while stack:
                current = stack.pop()
                if current is None or current in seen:
                    continue

                seen.add(current)
                try:
                    revision = script_directory.get_revision(current)
                except ResolutionError:
                    continue

                down = revision.down_revision
                if not down:
                    continue

                if isinstance(down, (tuple, list, set)):
                    stack.extend(down)
                else:
                    stack.append(down)

            seen.discard(revision_id)
            ancestors_cache[revision_id] = seen
            return seen

        to_remove: set[str] = set()

        for revision_id in version_ids:
            ancestors = ancestor_set(revision_id)
            for candidate in version_ids:
                if candidate == revision_id:
                    continue
                if candidate in ancestors:
                    to_remove.add(candidate)

        if not to_remove:
            return

        for revision_id in to_remove:
            connection.execute(
                text(
                    f"DELETE FROM {version_table} WHERE version_num = :revision_id"
                ),
                {"revision_id": revision_id},
            )

        connection.commit()

    with connectable.connect() as connection:
        prune_overlapping_versions(connection)
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table=version_table,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
