"""At-least-once native event consumption; restart replays the durable ledger safely."""

import time


class NativeChanges:
    def __init__(self, repository):
        self.repository = repository
        self.cursor = 0
        self.pending = {}
        self.retry_at = {}
        self.reconcile_at = 0

    def poll(self):
        while events := self.repository.events_after(self.cursor):
            for event in events:
                self.pending.setdefault(event["document_id"], set()).update(
                    (change["kind"], change["key"]) for change in event["changes"]
                )
                self.cursor = event["sequence"]
            if len(events) < 1000:
                break
        reconcile = time.monotonic() >= self.reconcile_at
        if reconcile:
            self.reconcile_at = time.monotonic() + 60
        return (
            None
            if reconcile
            else [
                identity
                for identity in self.pending
                if self.retry_at.get(identity, 0) <= time.time()
            ]
        )

    def pages(self, doc):
        changes = self.pending.get(doc["id"], set())
        pages = {int(key) for kind, key in changes if kind == "page"}
        pages.update(
            task["page"]
            for task in doc["tasks"]
            if ("task", task["task_id"]) in changes and task["page"] is not None
        )
        return pages

    def acknowledge(self, identity):
        self.pending.pop(identity, None)
        self.retry_at.pop(identity, None)
