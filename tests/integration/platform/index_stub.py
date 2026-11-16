"""Stub only external search writes while keeping real SQL activation contracts."""

from sqlalchemy import select, update

from hrs.library.books import get_run
from hrs.library.catalog import Library
from hrs.library.runs.outputs import fingerprint
from hrs.search.indexing import BookIndexer, index_rules, indexed_fields, source_manifest
from hrs.storage import models as db


def frozen_manifest(settings, engine, run_id):
    run = get_run(engine, run_id)
    rows = Library(settings, engine).chapters(run["book_id"])
    with engine.connect() as connection:
        title = connection.scalar(select(db.books.c.title).where(db.books.c.id == run["book_id"]))
    return run, {
        "source": source_manifest(run, rows, title),
        "rules": index_rules(settings),
        "synthetic_external_write": True,
    }


def seed_current_index(settings, engine, run_id):
    """Seed a previously completed backend without manufacturing new outbox events."""
    run, manifest = frozen_manifest(settings, engine, run_id)
    with engine.begin() as connection:
        connection.execute(
            update(db.runs)
            .where(db.runs.c.id == run_id)
            .values(
                result={
                    **run["result"],
                    **indexed_fields(manifest),
                    "retrieval_manifest": manifest,
                    "retrieval_generation": fingerprint(manifest),
                }
            )
        )


def completed_index(settings, engine, calls=None):
    def index(self, run_id):
        if calls is not None:
            calls.append(run_id)
        run, manifest = frozen_manifest(settings, engine, run_id)
        generation = fingerprint(manifest)
        adapter = object.__new__(BookIndexer)
        adapter.engine = engine
        adapter.activate(run, generation, manifest)
        return {"run_id": run_id, "generation": generation, "chunks": 0}

    return index
