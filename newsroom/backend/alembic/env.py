from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from nr.config import get_settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _url() -> str:
    # Tests point migrations at another database via -x db_url=...
    return context.get_x_argument(as_dictionary=True).get("db_url") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=None, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
