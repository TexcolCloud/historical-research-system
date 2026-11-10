"""Durable account/model admission, bounded cooldown and single-task recovery.

The caller supplies provider diagnostics and committed-result signals. This module
never judges source content and never runs or changes a model. All writes join the
caller's ledger transaction, including failure deduplication and probe ownership.
"""

import hashlib
import json
import math
import time
from pathlib import Path

import psutil

from hrs.processing.contracts import SOL, TERRA
from hrs.processing.execution_policy import configured_policy
from hrs.processing.ledger import Ledger, encode

FAILURE_LIMIT = 6
WAIT_LIMIT_SECONDS = 1800
RECOVERY_WINDOW_SECONDS = 30
RECOVERY_SUCCESSES = 2
PRESSURE_CODES = frozenset({"rate_limit", "capacity", "service_unavailable"})
PERMANENT_CODES = frozenset({"quota_exhausted", "authentication"})
FAILURE_CODES = PRESSURE_CODES | PERMANENT_CODES | {"transport", "timeout", "tool_error", "unknown"}


class RuntimeControl:
    def __init__(self, root, ledger=None):
        self.root = Path(root)
        path = self.root / "ledger.sqlite3"
        self.ledger = ledger if ledger is not None else Ledger(path) if path.exists() else None

    def _require_writable(self):
        if self.ledger is None:
            raise ValueError("Runtime control requires a writable ledger workspace")

    def _capacity(self):
        return configured_policy(self.root)["capacity"]

    @staticmethod
    def _key(scope, model):
        if scope not in {"account", "model"}:
            raise ValueError("Runtime scope must be account or model")
        if scope == "model" and (not isinstance(model, str) or not model.strip()):
            raise ValueError("Model-scoped pressure requires a model identity")
        return "account" if scope == "account" else "model:" + model

    def _normal(self, key):
        return {
            "scope": "account" if key == "account" else "model",
            "model": None if key == "account" else key.removeprefix("model:"),
            "state": "normal",
            "code": None,
            "reason": None,
            "limit": self._capacity(),
            "retry_at": 0,
            "failures": 0,
            "first_at": None,
            "generation": 0,
            "probe_owner": None,
            "probe_until": 0,
            "successes": 0,
            "recovery_at": 0,
        }

    @staticmethod
    def _exists(connection):
        return (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='runtime_control'"
            ).fetchone()
            is not None
        )

    def _read(self, connection):
        if not self._exists(connection):
            return {}
        return {
            key: json.loads(value)
            for key, value in connection.execute("SELECT scope,payload FROM runtime_control")
        }

    @staticmethod
    def _save(connection, key, state):
        connection.execute(
            "INSERT INTO runtime_control VALUES (?,?) ON CONFLICT(scope) DO UPDATE SET payload=excluded.payload",
            (key, encode(state)),
        )

    @staticmethod
    def _event(connection, identity, payload):
        return (
            connection.execute(
                "INSERT OR IGNORE INTO runtime_events VALUES (?,?)", (identity, encode(payload))
            ).rowcount
            == 1
        )

    def _public(self, state, now):
        result = dict(state)
        result["configured_limit"] = self._capacity()
        result["limit"] = min(result["limit"], result["configured_limit"])
        if (
            result["state"] in {"cooldown", "probing"}
            and result["first_at"] is not None
            and now - result["first_at"] >= WAIT_LIMIT_SECONDS
        ):
            result.update(state="blocked", reason="wait_budget_exhausted", retry_at=0)
        elif result["state"] == "cooldown" and result["retry_at"] <= now:
            result["state"] = "probing"
        if result["state"] == "blocked":
            result["retry_at"] = 0
        return result

    def snapshot(self, *, now=None):
        now = time.time() if now is None else now
        stored = {}
        if self.ledger is not None:
            with self.ledger.connect() as connection:
                stored = self._read(connection)
        account = self._public(stored.get("account", self._normal("account")), now)
        models = {
            model: self._public(stored.get("model:" + model, self._normal("model:" + model)), now)
            for model in sorted(
                {TERRA, SOL}
                | {key.removeprefix("model:") for key in stored if key.startswith("model:")}
            )
        }
        scopes = [account, *models.values()]
        priority = {"normal": 0, "recovering": 1, "probing": 2, "cooldown": 3, "blocked": 4}
        return {
            "state": max(scopes, key=lambda state: priority[state["state"]])["state"],
            "scopes": scopes,
            "account": account,
            "models": models,
            "configured_capacity": self._capacity(),
            "next_retry_at": min(
                (state["retry_at"] for state in scopes if state["retry_at"] > now), default=0
            ),
        }

    def failure(
        self,
        code,
        event_id,
        *,
        model=None,
        scope="model",
        retry_after_seconds=None,
        observed_capacity=None,
        now=None,
    ):
        self._require_writable()
        now = time.time() if now is None else now
        code = "rate_limit" if code == "429" else code
        if code not in FAILURE_CODES or not isinstance(event_id, str) or not event_id:
            raise ValueError("A supported runtime failure and stable event identity are required")
        # A diagnostic without a model cannot safely restrict only one model.
        scope = "account" if model is None else scope
        key = self._key(scope, model)
        if retry_after_seconds is not None and (
            not isinstance(retry_after_seconds, (int, float))
            or not math.isfinite(retry_after_seconds)
            or retry_after_seconds < 0
        ):
            raise ValueError("Retry delay must be a finite nonnegative number of seconds")
        if observed_capacity is not None and (
            type(observed_capacity) is not int or observed_capacity < 0
        ):
            raise ValueError("Observed capacity must be a nonnegative integer")
        with self.ledger.connect(write=True) as connection:
            self.ledger.ensure_runtime_schema(connection)
            previous = connection.execute(
                "SELECT payload FROM runtime_events WHERE identity=?", ("failure:" + event_id,)
            ).fetchone()
            if previous:
                key = json.loads(previous[0])["scope_key"]
                state = self._read(connection).get(key, self._normal(key))
                return {**self._public(state, now), "duplicate": True}
            self._event(
                connection, "failure:" + event_id, {"scope_key": key, "at": now, "code": code}
            )
            state = self._read(connection).get(key, self._normal(key))
            if state["state"] == "blocked":
                return {**self._public(state, now), "duplicate": False}
            if code in PERMANENT_CODES:
                state.update(state="blocked", code=code, reason=code, retry_at=0, probe_owner=None)
            elif state["state"] == "cooldown" and state["retry_at"] > now:
                if retry_after_seconds is not None:
                    state["retry_at"] = max(state["retry_at"], now + retry_after_seconds)
            else:
                # A successful probe ended the previous outage wait. A relapse
                # starts a new wait period, but does not replenish failure rounds.
                if state["state"] == "recovering" or state["first_at"] is None:
                    state["first_at"] = now
                state["failures"] += 1
                state["generation"] += 1
                limit = min(state["limit"], self._capacity())
                if observed_capacity:
                    limit = min(limit, observed_capacity)
                if code in PRESSURE_CODES:
                    limit = max(1, limit // 2)
                delay = min(300, 30 * 2 ** min(state["failures"] - 1, 4))
                # Deterministic per-incident jitter is restart-stable and prevents herd retries.
                fraction = int(hashlib.sha256(event_id.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
                delay += fraction * min(5, delay * 0.1)
                state.update(
                    state="cooldown",
                    code=code,
                    reason=code,
                    limit=limit,
                    retry_at=now + max(delay, retry_after_seconds or 0),
                    probe_owner=None,
                    probe_until=0,
                    successes=0,
                )
                if state["failures"] >= FAILURE_LIMIT:
                    state.update(state="blocked", reason="retry_budget_exhausted", retry_at=0)
            if (
                state["state"] != "blocked"
                and state["first_at"] is not None
                and max(now, state["retry_at"]) - state["first_at"] >= WAIT_LIMIT_SECONDS
            ):
                state.update(state="blocked", reason="wait_budget_exhausted", retry_at=0)
            self._save(connection, key, state)
            return {**self._public(state, now), "duplicate": False}

    def gate(self, model, *, now=None):
        """Read-only coordinator gate; coordinators must not occupy worker probes."""
        self._key("model", model)
        now = time.time() if now is None else now
        snapshot = self.snapshot(now=now)
        states = [
            snapshot["account"],
            snapshot["models"].get(model, self._normal("model:" + model)),
        ]
        blocked = next((state for state in states if state["state"] == "blocked"), None)
        cooling = max(
            (state for state in states if state["state"] == "cooldown"),
            key=lambda state: state["retry_at"],
            default=None,
        )
        denial = blocked or cooling
        phase = max(
            states,
            key=lambda state: {
                "normal": 0,
                "recovering": 1,
                "probing": 2,
                "cooldown": 3,
                "blocked": 4,
            }[state["state"]],
        )["state"]
        return {
            "allowed": denial is None,
            "limit": min(state["limit"] for state in states),
            "retry_at": denial["retry_at"] if denial else 0,
            "reason": denial["reason"] if denial else None,
            "state": phase,
            "probe": phase == "probing",
        }

    def admit(
        self,
        model,
        owner,
        *,
        occupied=0,
        total_occupied=None,
        lease_seconds=900,
        now=None,
        priority="normal",
    ):
        self._require_writable()
        now = time.time() if now is None else now
        self._key("model", model)
        if priority not in {"normal", "research"}:
            raise ValueError("Unsupported runtime admission priority")
        if not isinstance(owner, str) or not owner or lease_seconds <= 0:
            raise ValueError("Admission requires a stable owner and positive lease")
        if (
            type(occupied) is not int
            or occupied < 0
            or (
                total_occupied is not None
                and (type(total_occupied) is not int or total_occupied < 0)
            )
        ):
            raise ValueError("Admission occupancy must be nonnegative integers")
        total_occupied = occupied if total_occupied is None else total_occupied
        keys = ["account", "model:" + model]
        with self.ledger.connect(write=True) as connection:
            self.ledger.ensure_runtime_schema(connection)
            stored = self._read(connection)
            for key in keys:
                state = stored.get(key, self._normal(key))
                if state["state"] == "probing" and state["probe_until"] <= now:
                    self.failure(
                        "timeout",
                        f"probe-expired:{key}:{state['probe_owner']}:{state['probe_until']}",
                        model=state["model"],
                        scope=state["scope"],
                        now=now,
                    )
            stored = self._read(connection)
            decisions, probe_keys = [], []
            source_ready = priority == "research" and self.ledger.source_work_ready(now=now)
            for key in keys:
                raw = stored.get(key, self._normal(key))
                state = self._public(raw, now)
                count = total_occupied if key == "account" else occupied
                denied = state["state"] in {"blocked", "cooldown"}
                if state["state"] == "probing":
                    denied = bool(raw["probe_owner"] and raw["probe_owner"] != owner)
                    probe_keys.append(key)
                reserved = (
                    min(2, max(0, state["limit"] - 1)) if source_ready and key == "account" else 0
                )
                if count >= state["limit"] - reserved:
                    denied = True
                    state = {
                        **state,
                        "reason": "source_processing_priority" if reserved else "capacity_occupied",
                    }
                if source_ready and state["state"] in {"probing", "recovering"}:
                    denied = True
                    state = {**state, "reason": "source_processing_priority"}
                decisions.append((key, state, denied))
            denial = next((state for _, state, denied in decisions if denied), None)
            if denial:
                return {
                    "allowed": False,
                    "limit": min(state["limit"] for _, state, _ in decisions),
                    "retry_at": denial["retry_at"],
                    "reason": denial["reason"],
                    "state": denial["state"],
                    "probe": False,
                }
            generations = {}
            expires_at = now + lease_seconds
            for key, state, _ in decisions:
                raw = stored.get(key, self._normal(key))
                if key in probe_keys:
                    if raw["probe_owner"] != owner:
                        raw.update(state="probing", probe_owner=owner, probe_until=expires_at)
                    expires_at = min(expires_at, raw["probe_until"])
                    self._save(connection, key, raw)
                generations[key] = raw["generation"]
            admission = {
                "model": model,
                "generations": generations,
                "expires_at": expires_at,
                "probe_keys": probe_keys,
            }
            connection.execute(
                "INSERT INTO runtime_admissions VALUES (?,?) ON CONFLICT(owner) DO UPDATE SET payload=excluded.payload",
                (owner, encode(admission)),
            )
            return {
                "allowed": True,
                "limit": 1 if probe_keys else min(state["limit"] for _, state, _ in decisions),
                "retry_at": 0,
                "reason": None,
                "state": "probing" if probe_keys else "normal",
                "probe": bool(probe_keys),
            }

    def reserve_coordinator(self, model, owner, *, lease_seconds=900, now=None):
        """Reserve a dispatcher without consuming the worker's recovery probe.

        One recovery dispatcher is free in affected scopes. A second dispatcher
        consumes ordinary capacity and must leave headroom for its worker.
        """
        self._require_writable()
        self._key("model", model)
        if (
            not isinstance(owner, str)
            or not owner.startswith("research-coordinator:")
            or type(lease_seconds) is not int
            or not 1 <= lease_seconds <= 86400
        ):
            raise ValueError(
                "Coordinator reservations require a stable research owner and bounded lease"
            )
        now = time.time() if now is None else now
        with self.ledger.connect(write=True) as connection:
            self.ledger.ensure_runtime_schema(connection)
            stored = self._read(connection)
            for key in ("account", "model:" + model):
                raw = stored.get(key, self._normal(key))
                if raw["state"] == "probing" and raw["probe_until"] <= now:
                    self.failure(
                        "timeout",
                        f"probe-expired:{key}:{raw['probe_owner']}:{raw['probe_until']}",
                        model=raw["model"],
                        scope=raw["scope"],
                        now=now,
                    )
            stored = self._read(connection)
            states = [
                self._public(stored.get(key, self._normal(key)), now)
                for key in ("account", "model:" + model)
            ]
            gate = self.gate(model, now=now)
            if not gate["allowed"]:
                return {**gate, "probe": False, "control_only": True}
            busy_probe = next(
                (
                    stored[key]
                    for key in ("account", "model:" + model)
                    if stored.get(key, {}).get("state") == "probing"
                    and stored[key].get("probe_owner")
                    and stored[key]["probe_until"] > now
                ),
                None,
            )
            if busy_probe:
                return {
                    **gate,
                    "allowed": False,
                    "reason": "worker_probe_active",
                    "retry_at": busy_probe["probe_until"],
                    "probe": False,
                    "control_only": True,
                }
            coordinators = []
            for candidate, payload in connection.execute(
                "SELECT owner,payload FROM runtime_admissions WHERE owner GLOB 'research-coordinator:*'"
            ).fetchall():
                value = json.loads(payload)
                if value["expires_at"] <= now or not self.ledger.coordinator_alive(value):
                    connection.execute("DELETE FROM runtime_admissions WHERE owner=?", (candidate,))
                else:
                    coordinators.append((candidate, value))
            existing = connection.execute(
                "SELECT payload FROM runtime_admissions WHERE owner=?", (owner,)
            ).fetchone()
            previous = json.loads(existing[0]) if existing else None
            if previous and previous["expires_at"] > now:
                if previous["model"] != model or not previous.get("coordinator"):
                    raise ValueError("Coordinator owner already identifies a different admission")
                renewed = self.renew(model, owner, expires_at=now + lease_seconds, now=now)
                return {
                    **gate,
                    "allowed": renewed,
                    "probe": False,
                    "reason": None if renewed else "admission_yield",
                    "control_only": previous.get("control_only", False)
                    and any(state["state"] != "normal" for state in states),
                }
            occupancy = (
                self.ledger.capacity_used(now=now),
                self.ledger.model_capacity_used(model, now=now),
            )
            recovery = any(state["state"] != "normal" for state in states)
            source_ready = self.ledger.source_work_ready(now=now)
            if source_ready and recovery:
                return {
                    **gate,
                    "allowed": False,
                    "probe": False,
                    "reason": "source_processing_priority",
                    "control_only": False,
                }
            if len(coordinators) >= 2 or (
                recovery
                and coordinators
                and any(state["state"] not in {"normal", "recovering"} for state in states)
            ):
                return {
                    **gate,
                    "allowed": False,
                    "probe": False,
                    "reason": "recovery_coordinator_active",
                    "control_only": False,
                }
            control_only = recovery and not any(
                value.get("control_only") for _, value in coordinators
            )
            control_scopes = [
                key
                for key, state in zip(("account", "model:" + model), states)
                if control_only and state["state"] != "normal"
            ]
            for position, (state, count) in enumerate(zip(states, occupancy)):
                reserved = (
                    min(2, max(0, state["limit"] - 1)) if source_ready and position == 0 else 0
                )
                # Even a free recovery controller counts against an unaffected normal scope.
                cost = int(("account" if position == 0 else "model:" + model) not in control_scopes)
                if cost and count + cost >= state["limit"] - reserved:
                    return {
                        **gate,
                        "allowed": False,
                        "reason": "source_processing_priority" if reserved else "capacity_occupied",
                        "probe": False,
                        "control_only": control_only,
                    }
            admission = {
                "model": model,
                "generations": {
                    key: stored.get(key, self._normal(key))["generation"]
                    for key in ("account", "model:" + model)
                },
                "probe_keys": [],
                "coordinator": True,
                "control_only": control_only,
                "control_scopes": control_scopes,
                "expires_at": now + lease_seconds,
                "pid": psutil.Process().pid,
                "process_create_time": psutil.Process().create_time(),
            }
            connection.execute(
                "INSERT INTO runtime_admissions VALUES (?,?) ON CONFLICT(owner) DO UPDATE SET payload=excluded.payload",
                (owner, encode(admission)),
            )
            return {**gate, "probe": False, "control_only": control_only}

    def renew(self, model, owner, *, expires_at, now=None):
        """Extend one live admission; a stale generation never changes a newer probe or budget."""
        self._require_writable()
        now = time.time() if now is None else now
        self._key("model", model)
        if (
            not isinstance(owner, str)
            or not owner
            or type(expires_at) not in {int, float}
            or not math.isfinite(expires_at)
            or not now < expires_at <= now + 86400
        ):
            raise ValueError("Admission renewal requires an owner and bounded future deadline")
        with self.ledger.connect(write=True) as connection:
            self.ledger.ensure_runtime_schema(connection)
            row = connection.execute(
                "SELECT payload FROM runtime_admissions WHERE owner=?", (owner,)
            ).fetchone()
            admission = json.loads(row[0]) if row else None
            if not admission or admission["model"] != model or admission["expires_at"] <= now:
                return False
            states = self._read(connection)
            if admission.get("coordinator"):
                if not self.ledger.coordinator_alive(admission):
                    return False
                keys = ("account", "model:" + model)
                public = {
                    key: self._public(states.get(key, self._normal(key)), now) for key in keys
                }
                control_scopes = set(
                    admission.get("control_scopes", keys if admission.get("control_only") else ())
                )
                for key, state in public.items():
                    if state["state"] == "normal":
                        control_scopes.discard(key)
                    elif (
                        key in control_scopes
                        and "control_scopes" in admission
                        and key not in admission["generations"]
                    ):
                        return False
                    elif (
                        key in control_scopes
                        and state["generation"]
                        > admission["generations"].get(key, state["generation"]) + 1
                    ):
                        # Normal recovery and then a relapse can both occur
                        # between renewals; do not regain a former free scope.
                        return False
                    elif key not in control_scopes and (
                        state["state"] not in {"normal", "recovering"}
                        or state["generation"] != admission["generations"].get(key)
                    ):
                        return False
                if (
                    not admission.get("control_only")
                    and any(state["state"] == "recovering" for state in public.values())
                    and self.ledger.source_work_ready(now=now)
                ):
                    return False
                admission["control_scopes"] = sorted(control_scopes)
                admission["control_only"] = bool(control_scopes)
                admission["generations"] = {
                    key: state["generation"] for key, state in public.items()
                }
                admission["expires_at"] = max(admission["expires_at"], expires_at)
                connection.execute(
                    "UPDATE runtime_admissions SET payload=? WHERE owner=?",
                    (encode(admission), owner),
                )
                return True
            for key in admission["probe_keys"]:
                state = states.get(key)
                if (
                    state
                    and state["generation"] == admission["generations"].get(key)
                    and state["state"] == "probing"
                    and state["probe_owner"] == owner
                    and state["probe_until"] > now
                    and expires_at > state["probe_until"]
                ):
                    state["probe_until"] = expires_at
                    self._save(connection, key, state)
            if expires_at > admission["expires_at"]:
                admission["expires_at"] = expires_at
                connection.execute(
                    "UPDATE runtime_admissions SET payload=? WHERE owner=?",
                    (encode(admission), owner),
                )
            return True

    def success(self, model, owner, event_id, *, now=None):
        self._require_writable()
        now = time.time() if now is None else now
        with self.ledger.connect(write=True) as connection:
            self.ledger.ensure_runtime_schema(connection)
            if not self._event(connection, "success:" + event_id, {"owner": owner, "at": now}):
                return self.snapshot(now=now)
            row = connection.execute(
                "SELECT payload FROM runtime_admissions WHERE owner=?", (owner,)
            ).fetchone()
            admission = json.loads(row[0]) if row else None
            if not admission or admission["model"] != model or admission["expires_at"] <= now:
                return self.snapshot(now=now)
            if admission.get("coordinator"):
                return self.snapshot(now=now)
            stored = self._read(connection)
            for key, generation in admission["generations"].items():
                state = stored.get(key)
                if not state or state["generation"] != generation:
                    continue
                current = self._public(state, now)
                if current["state"] == "blocked":
                    state.update(state="blocked", reason=current["reason"], retry_at=0)
                    self._save(connection, key, state)
                    continue
                if state["state"] == "probing" and state["probe_owner"] == owner:
                    state.update(
                        state="recovering",
                        limit=1,
                        probe_owner=None,
                        probe_until=0,
                        successes=0,
                        recovery_at=now,
                        retry_at=0,
                        first_at=None,
                    )
                elif state["state"] == "recovering":
                    state["successes"] += 1
                    if (
                        state["successes"] >= RECOVERY_SUCCESSES
                        and now - state["recovery_at"] >= RECOVERY_WINDOW_SECONDS
                    ):
                        state.update(
                            limit=min(self._capacity(), state["limit"] + 1),
                            successes=0,
                            recovery_at=now,
                        )
                        if state["limit"] == self._capacity():
                            state.update(self._normal(key), generation=generation + 1)
                self._save(connection, key, state)
            connection.execute("DELETE FROM runtime_admissions WHERE owner=?", (owner,))
            return self.snapshot(now=now)

    def release(self, owner, *, now=None):
        """Return a reservation which never started a real task; do not count a failure."""
        self._require_writable()
        now = time.time() if now is None else now
        with self.ledger.connect(write=True) as connection:
            self.ledger.ensure_runtime_schema(connection)
            for key, state in self._read(connection).items():
                if state["state"] == "probing" and state["probe_owner"] == owner:
                    state.update(state="cooldown", retry_at=now, probe_owner=None, probe_until=0)
                    self._save(connection, key, state)
            connection.execute("DELETE FROM runtime_admissions WHERE owner=?", (owner,))

    def reset(self, scope=None, *, model=None, now=None):
        """Explicit technical recovery only; keep event identities and content evidence."""
        self._require_writable()
        now = time.time() if now is None else now
        target = self._key(scope, model) if scope is not None else None
        with self.ledger.connect(write=True) as connection:
            self.ledger.ensure_runtime_schema(connection)
            for key, state in self._read(connection).items():
                if target is None or key == target:
                    normal = self._normal(key)
                    normal["generation"] = state["generation"] + 1
                    self._save(connection, key, normal)
            if target is None:
                connection.execute("DELETE FROM runtime_admissions")
            else:
                # One task can probe both account and model scopes. Resetting one
                # must not orphan its valid probe/success signal for the other.
                for owner, payload in connection.execute(
                    "SELECT owner,payload FROM runtime_admissions"
                ).fetchall():
                    admission = json.loads(payload)
                    if target not in admission["generations"]:
                        continue
                    admission["generations"].pop(target)
                    admission["probe_keys"] = [
                        key for key in admission["probe_keys"] if key != target
                    ]
                    if admission["generations"]:
                        connection.execute(
                            "UPDATE runtime_admissions SET payload=? WHERE owner=?",
                            (encode(admission), owner),
                        )
                    else:
                        connection.execute("DELETE FROM runtime_admissions WHERE owner=?", (owner,))
        return self.snapshot(now=now)

    def recover_local_errors(self, *, models, now=None):
        """Explicit research resume clears legacy local-only pressure without revoking live workers."""
        self._require_writable()
        now = time.time() if now is None else now
        with self.ledger.connect(write=True) as connection:
            self.ledger.ensure_runtime_schema(connection)
            recovered = []
            for key, state in self._read(connection).items():
                if key not in {"account", *("model:" + model for model in models)} or state.get(
                    "code"
                ) not in {"tool_error", "unknown"}:
                    continue
                since = state.get("first_at") or state.get("recovery_at") or 0
                codes = [
                    json.loads(row[0])["code"]
                    for row in connection.execute(
                        "SELECT payload FROM runtime_events WHERE identity LIKE 'failure:%' "
                        "AND json_extract(payload,'$.scope_key')=? AND json_extract(payload,'$.at')>=?",
                        (key, since),
                    )
                ]
                if any(code not in {"tool_error", "unknown"} for code in codes):
                    continue
                normal = self._normal(key)
                normal["generation"] = state["generation"] + 1
                self._save(connection, key, normal)
                for owner, payload in connection.execute(
                    "SELECT owner,payload FROM runtime_admissions"
                ).fetchall():
                    admission = json.loads(payload)
                    if key in admission["generations"]:
                        admission["generations"][key] = normal["generation"]
                        admission["probe_keys"] = [
                            scope for scope in admission["probe_keys"] if scope != key
                        ]
                        connection.execute(
                            "UPDATE runtime_admissions SET payload=? WHERE owner=?",
                            (encode(admission), owner),
                        )
                self._event(
                    connection,
                    f"research-local-recovery:{key}:{normal['generation']}",
                    {"scope_key": key, "at": now, "reason": "explicit_research_resume"},
                )
                recovered.append(key)
            return recovered


def effective_policy(root):
    """Apply durable account admission limits to the configured worker policy."""
    result = configured_policy(root)
    if (Path(root) / "ledger.sqlite3").exists():
        account = RuntimeControl(root).snapshot()["account"]
        result["capacity"] = min(result["capacity"], account["limit"])
    return result
