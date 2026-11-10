from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


def engine_for(settings):
    url = make_url(settings.database_url.get_secret_value())
    if url.drivername != "postgresql+psycopg":
        raise ValueError("The platform requires PostgreSQL with psycopg.")
    configured_options = url.query.get("options", "")
    if isinstance(configured_options, tuple):
        configured_options = " ".join(configured_options)
    # Explicit deployment options come last: schema identity and intentional
    # timeout overrides survive the driver's connect_args precedence.
    options = "-cstatement_timeout=30000 -clock_timeout=10000 " + configured_options
    return create_engine(
        url,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={
            "connect_timeout": int(url.query.get("connect_timeout", 5)),
            "options": options.strip(),
        },
    )


def migrate(engine):
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent.parent / "migrations"))
    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(1388427265)"))
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
