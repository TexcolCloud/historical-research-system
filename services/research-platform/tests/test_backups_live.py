import os
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from sqlalchemy import text
from sqlalchemy.engine import make_url

from hrs_platform.services.storage import objects_for
from hrs_platform.services.backups import dump_database
from hrs_platform.services.backups import restore_database


@pytest.mark.skipif(os.environ.get("PLATFORM_TEST_BACKUP") != "1", reason="Opt-in real pg_dump/S3/pg_restore drill")
def test_dump_survives_local_loss_and_restores_into_a_separate_database(platform, tmp_path):
    settings, engine = platform
    with engine.begin() as connection:
        namespace = connection.scalar(text("SELECT current_schema()"))
        connection.execute(text("CREATE TABLE recovery_probe (content text NOT NULL)"))
        connection.execute(text("INSERT INTO recovery_probe VALUES ('immutable source backup test')"))
    url = make_url(settings.database_url.get_secret_value())
    assert url.database == "historical_research_v2_test"
    dump = tmp_path / "database.dump"
    dump_database(settings, url.database, dump)
    objects = objects_for(settings)
    ref = objects.put_file(dump)
    dump.unlink()
    objects.materialize(ref, dump)
    target = "hrs_restore_test_" + uuid4().hex[:16]
    admin = url.set(drivername="postgresql", database="postgres").render_as_string(hide_password=False)
    owned = False
    try:
        restore_database(settings, target, dump)
        owned = True
        with psycopg.connect(url.set(drivername="postgresql", database=target).render_as_string(hide_password=False)) as restored:
            content = restored.execute(sql.SQL("SELECT content FROM {}.recovery_probe").format(sql.Identifier(namespace))).fetchone()[0]
            assert content == "immutable source backup test"
        # Restoring again must fail rather than overwrite an existing destination.
        with pytest.raises(psycopg.errors.DuplicateDatabase):
            restore_database(settings, target, dump)
    finally:
        if owned:
            with psycopg.connect(admin, autocommit=True) as connection:
                connection.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(target)))
