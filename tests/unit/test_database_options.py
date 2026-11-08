"""Deployment database options must preserve isolated schema identity."""

from types import SimpleNamespace

from pydantic import SecretStr

from hrs.storage import db


def test_engine_keeps_url_search_path_and_explicit_timeouts(monkeypatch):
    captured = {}

    def create(url, **options):
        captured.update(options)
        return "engine"

    monkeypatch.setattr(db, "create_engine", create)
    settings = SimpleNamespace(
        database_url=SecretStr(
            "postgresql+psycopg://user:password@localhost/test?options=-csearch_path%3Dtest_isolated%20-cstatement_timeout%3D45000&connect_timeout=9"
        )
    )
    assert db.engine_for(settings) == "engine"
    options = captured["connect_args"]
    assert options["connect_timeout"] == 9
    assert "-clock_timeout=10000" in options["options"]
    assert options["options"].endswith("-csearch_path=test_isolated -cstatement_timeout=45000")
