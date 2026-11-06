from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from hrs.config import Settings, load_environment
from hrs.storage.db import migrate


@pytest.fixture
def platform(tmp_path):
    settings = Settings.load().model_copy(
        update={"codex_root": tmp_path / "native", "cache_root": tmp_path / "cache"}
    )
    url = make_url(load_environment(settings.project_root)["PLATFORM_TEST_DATABASE_URL"])
    if url.database != "historical_research_v2_test":
        raise RuntimeError("Tests require the dedicated platform test database.")
    name = "test_" + uuid4().hex
    admin = create_engine(url, hide_parameters=True)
    with admin.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {name}"))
    engine = create_engine(
        url, hide_parameters=True, connect_args={"options": f"-csearch_path={name}"}
    )
    settings = settings.model_copy(
        update={"database_url": SecretStr(url.render_as_string(hide_password=False))}
    )
    try:
        migrate(engine)
        yield settings, engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f"DROP SCHEMA {name} CASCADE"))
        admin.dispose()
