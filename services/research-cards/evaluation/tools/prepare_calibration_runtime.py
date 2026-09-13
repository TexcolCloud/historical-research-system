"""Create an isolated owned database and record its pre-send configuration."""

from pathlib import Path

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from research_cards.database import upgrade_database
from research_cards.records import atomic_json, now, read_json, sha256
from research_cards.settings import Settings

MODULE = Path(__file__).resolve().parents[2]
OUTPUT = MODULE / "evaluation/development/calibration-v1"
CONFIG = MODULE / "config/calibration-v1.toml"
settings = Settings.load(CONFIG, MODULE.parents[1] / ".env")
url = make_url(settings.database_url.get_secret_value()).set(drivername="postgresql")
if (url.host, url.port, url.database) != ("127.0.0.1", 55436, "historical_research_cards_calibration"):
    raise SystemExit("Expected the explicitly named local calibration database.")
with psycopg.connect(url.set(database="postgres").render_as_string(hide_password=False), autocommit=True) as connection:
    exists = connection.execute("SELECT 1 FROM pg_database WHERE datname=%s", (url.database,)).fetchone()
    if not exists:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(url.database)))
upgrade_database(settings)
with psycopg.connect(url.render_as_string(hide_password=False)) as connection:
    if connection.execute("SELECT count(*) FROM tasks").fetchone()[0]:
        raise SystemExit("Tasks already exist; preserve the pre-send configuration lock.")
lock_path = OUTPUT / "configuration-lock.json"
prior = read_json(lock_path)
prior_path = OUTPUT / "configuration-lock-before-runtime-isolation.json"
if not prior_path.exists():
    atomic_json(prior_path, prior)
prior.update({"at": now(), "settings": settings.public(), "budget": settings.budget(),
    "runtime_isolation": {"database": url.database, "config_path": str(CONFIG),
        "config_sha256": sha256(CONFIG.read_bytes()), "prior_configuration_sha256": sha256(prior_path.read_bytes()),
        "reason": "Separate calibration from existing engineering services; no existing process stopped or database reset.",
        "strategy_changed": False, "tasks_at_lock": 0}})
atomic_json(lock_path, prior)
print({"database": url.database, "created": not bool(exists), "port": settings.port, "tasks_at_lock": 0})
