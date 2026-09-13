"""Create only the two named module databases on the existing local test PostgreSQL."""

import json
from pathlib import Path

import psycopg
from dotenv import dotenv_values
from psycopg import sql
from sqlalchemy.engine import make_url

from research_cards.database import upgrade_database
from research_cards.settings import Settings

root = Path(__file__).resolve().parents[4]
environment_file = root / ".env"
configured = dotenv_values(environment_file)
source = make_url(configured["INGEST_DATABASE_URL"])
if source.host not in ("127.0.0.1", "localhost") or source.port != 55436:
    raise SystemExit("Expected the already configured local acceptance PostgreSQL on port 55436.")
report, additions = [], []
for key, name in (("CARDS_DATABASE_URL", "historical_research_cards"),
                  ("CARDS_TEST_DATABASE_URL", "historical_research_cards_test")):
    url = source.set(drivername="postgresql", database=name)
    if key in configured:
        existing = make_url(configured[key])
        if (existing.host, existing.port, existing.database) != (url.host, url.port, name):
            raise SystemExit("An existing module database setting differs; preserve it for explicit review.")
        url = existing.set(drivername="postgresql")
    with psycopg.connect(source.set(drivername="postgresql", database="postgres").render_as_string(hide_password=False),
                         autocommit=True) as connection:
        exists = connection.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone()
        if not exists:
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    settings = Settings(database_url=url.render_as_string(hide_password=False),
                        state_root=root / "services/research-cards/state")
    migration = upgrade_database(settings)
    report.append({"database": name, "created": not bool(exists), **migration})
    if key not in configured:
        additions.append(f"{key}={url.set(drivername='postgresql+psycopg').render_as_string(hide_password=False)}")
if additions:
    with environment_file.open("a", encoding="utf-8") as stream:
        stream.write("\n# Research cards: module-owned local PostgreSQL databases\n" + "\n".join(additions) + "\n")
print(json.dumps(report, ensure_ascii=False))
