"""Bounded Agent research over a frozen book; search previews never become evidence."""

import asyncio
import json
from typing import Literal

from agents import function_tool
from pydantic import BaseModel, Field
from sqlalchemy import select
from temporalio.exceptions import ApplicationError

from . import schema as db
from .books import get_run
from .domain.generation_contracts import DigestFact
from .domain.tokens import estimate_request
from .outputs import execution_parent, fingerprint
from .reading import card_model, neighbor_context, source_payload
from .retrieval_chunks import source_excerpt
from .search import Search, recover_retrieval, task_search

PURPOSES = {"support", "counter", "qualify"}
READ_TOKENS = 10000


class EvidenceQuery(BaseModel):
    query: str = Field(min_length=1, max_length=300)
    purpose: Literal["support", "counter", "qualify"]


class EvidenceAssessment(BaseModel):
    source_unit_ids: list[str]
    findings: list[DigestFact]
    unresolved_questions: list[str]
    sufficient: bool


class CardEvidence:
    def __init__(self, settings, engine, outputs, library):
        self.settings, self.engine = settings, engine
        self.outputs, self.library = outputs, library
        self.search = Search(settings, engine)

    async def prepare(self, run, snapshot):
        return await recover_retrieval(self.engine, self.outputs,
            run["id"], "card-evidence-corpus", lambda: asyncio.to_thread(self.pin, run, snapshot)
        )

    def pin(self, run, snapshot):
        """Bind the index and canonical sources before any new research requests."""
        parent = get_run(self.engine, run["parent_run_id"])
        generation = (parent["result"] or {}).get("retrieval_generation")
        if not generation or not parent["result"].get("published"):
            raise ApplicationError("本书尚无已发布检索索引，请完成索引后重试。", type="card_index_missing")
        with self.engine.connect() as connection:
            committed = connection.scalar(
                select(db.stage_outputs.c.input_sha256).where(
                    db.stage_outputs.c.run_id == parent["id"],
                    db.stage_outputs.c.step == "retrieval-chunks:" + generation,
                )
            )
        if committed != generation:
            raise ApplicationError("索引版本尚未完成，保留制卡进度等待重建。", type="card_index_missing")
        if not self.search.client.indices.exists(index=self.settings.opensearch_index):
            raise ApplicationError("检索索引不存在，请恢复索引后重试。", type="card_index_missing")
        count = self.search.client.count(
            index=self.settings.opensearch_index,
            body={
                "query": {
                    "bool": {
                        "filter": [
                            {"term": {"book_id": run["book_id"]}},
                            {"term": {"generation": generation}},
                        ]
                    }
                }
            },
        )["count"]
        if not count:
            raise ApplicationError("本书索引内容缺失，请恢复索引后重试。", type="card_index_missing")
        dependency = {"snapshot": fingerprint(snapshot), "rule": "card-evidence-v1"}
        saved = self.outputs.get(run["id"], "card-evidence-corpus", dependency)
        if saved:
            self.check_generation(saved)
            return saved
        for chapter in snapshot["chapters"]:
            original = self.library.chapter(chapter["id"])
            units = [u for u in snapshot["units"] if u["chapter_id"] == chapter["id"]]
            if (
                original["book_id"] != run["book_id"]
                or "".join(u["text"] for u in units) != original["text"]
                or any(
                    u["sources"] != source_excerpt(original, u["start"], u["end"])["sources"] for u in units
                )
            ):
                raise ApplicationError(
                    "来源版本已变化，请创建新的制卡任务。", type="card_corpus_changed", non_retryable=True
                )
        corpus = {
            "book_id": run["book_id"],
            "parent_run_id": parent["id"],
            "generation": generation,
            "index": self.settings.opensearch_index,
            "retrieval_policy": parent["result"].get("retrieval_policy"),
            **dependency,
        }
        self.check_generation(corpus)
        return self.outputs.put(run["id"], "card-evidence-corpus", corpus, dependency)

    def check_generation(self, corpus):
        parent = get_run(self.engine, corpus["parent_run_id"])
        result = parent["result"] or {}
        if (
            result.get("retrieval_generation") != corpus["generation"]
            or not result.get("published")
            or result.get("retrieval_policy") != corpus["retrieval_policy"]
            or self.settings.opensearch_index != corpus["index"]
        ):
            raise ApplicationError(
                "检索版本已变化；保留旧证据，请创建新任务以使用新版本。",
                type="card_corpus_changed",
                non_retryable=True,
            )

    async def research(self, models, run_id, key, corpus, topic, clues, all_units, parent, previous=None):
        dependency = {
            "corpus": corpus,
            "topic": topic.model_dump(),
            "clues": clues,
            "previous": previous,
            "rule": "bounded-evidence-v3",
        }
        stage = f"{key}:evidence:{fingerprint(dependency)}"
        saved = await asyncio.to_thread(self.outputs.get, run_id, stage, dependency)
        if saved is not None:
            self.outputs.node(
                run_id,
                stage,
                kind="component",
                label="复用主题检索证据",
                objective=topic.objective,
                parent=parent,
                state="completed",
                details={"corpus": corpus, "assessment": saved["assessment"], "cached": True},
            )
            return saved
        known = {u["unit_id"]: u for u in all_units}
        # Fixed slots survive model retries, activity retries and process restarts.
        searches = [
            await asyncio.to_thread(self.outputs.get, run_id, f"{stage}:search:{i}") for i in range(3)
        ]
        intents = [await asyncio.to_thread(self.outputs.get, run_id, f"{stage}:intent:{i}") for i in range(3)]
        reads = [await asyncio.to_thread(self.outputs.get, run_id, f"{stage}:read:{i}") for i in range(3)]

        def allowed():
            return (
                set(topic.unit_ids)
                | {u for row in reads if row for u in row.get("related_unit_ids", [])}
                | {u for row in searches if row for hit in row["hits"] for u in hit["unit_ids"]}
            )

        def read_units():
            return {u["unit_id"]: u for row in reads if row for u in row["units"]}

        tool_lock = asyncio.Lock()

        async def perform_query(index):
            request = intents[index]
            query = EvidenceQuery.model_validate(request)
            tool_key = f"{stage}:search:{index}"
            tool_parent = execution_parent.get() or parent
            with self.outputs.operation(
                run_id,
                tool_key,
                "检索主题证据",
                kind="tool",
                objective=f"{query.purpose}: {query.query}",
                parent=tool_parent,
            ):
                await asyncio.to_thread(self.check_generation, corpus)
                if not await asyncio.to_thread(self.search.client.indices.exists, index=corpus["index"]):
                    raise ApplicationError("检索索引不可用，保留进度等待恢复。", type="card_index_missing")
                metrics = {}
                chapters = sorted({known[i]["chapter_id"] for i in topic.unit_ids})
                hits = await task_search(
                    self.search.search,
                    getattr(self.settings, "retrieval_endpoint", None),
                    run_id,
                    query.query,
                    corpus["book_id"],
                    limit=4,
                    total_chars=16000,
                    metrics=metrics,
                    decompose=False,
                    chapter_ids=chapters if query.purpose == "support" else None,
                )
                if not hits and query.purpose == "support":
                    local_metrics, metrics = metrics, {}
                    hits = await task_search(
                        self.search.search,
                        getattr(self.settings, "retrieval_endpoint", None),
                        run_id,
                        query.query,
                        corpus["book_id"],
                        limit=4,
                        total_chars=16000,
                        metrics=metrics,
                        decompose=False,
                    )
                    metrics["topic_chapter_attempt"] = local_metrics
                await asyncio.to_thread(self.check_generation, corpus)
                previews = []
                for rank, hit in enumerate(hits, 1):
                    if hit.get("generation") != corpus["generation"] or hit["book_id"] != corpus["book_id"]:
                        raise ApplicationError(
                            "召回结果不属于固定书籍版本。",
                            type="card_corpus_changed",
                            non_retryable=True,
                        )
                    mapped = []
                    for part in [hit, *hit.get("context", [])]:
                        chapter_units = [u for u in all_units if u["chapter_id"] == part["chapter_id"]]
                        text = "".join(u["text"] for u in chapter_units)
                        if text[part["start"] : part["end"]] != part["text"]:
                            raise ApplicationError(
                                "召回正文与固定原文不符。",
                                type="card_corpus_changed",
                                non_retryable=True,
                            )
                        mapped.extend(
                            u["unit_id"]
                            for u in chapter_units
                            if u["start"] < part["end"] and u["end"] > part["start"]
                        )
                    previews.append(
                        {
                            "rank": rank,
                            "unit_ids": list(dict.fromkeys(mapped)),
                            "pages": hit["pages"],
                            "preview": hit["text"][:400],
                            "preview_only": True,
                            "score": hit["score"],
                        }
                    )
                receipt = {
                    "request": request,
                    "hits": previews,
                    "metrics": metrics,
                    "generation": corpus["generation"],
                }
                searches[index] = await asyncio.to_thread(
                    self.outputs.put, run_id, tool_key, receipt, dependency
                )
                self.outputs.node(
                    run_id,
                    tool_key,
                    kind="tool",
                    label="检索主题证据",
                    objective=f"{query.purpose}: {query.query}",
                    parent=tool_parent,
                    state="completed",
                    details=receipt,
                )
            return receipt

        async def complete_query(index):
            return await recover_retrieval(self.engine, self.outputs, run_id, f"{stage}:search:{index}", lambda: perform_query(index))

        @function_tool(failure_error_function=None)
        async def search_evidence(queries: list[EvidenceQuery]) -> str:
            """Search support, counter and qualify once each; reuse each purpose's fixed query.

            Returns previews and unit IDs. Read original units before using evidence.
            """
            async with tool_lock:
                purposes = [query.purpose for query in queries]
                if not 1 <= len(queries) <= 3 or len(set(purposes)) != len(purposes):
                    return json.dumps(
                        {"error": "Supply one to three distinct purposes: support, counter, qualify."}
                    )
                results = []
                # Freeze the whole requested batch before starting any I/O, so a
                # restart can finish it without another planning/model request.
                for query in queries:
                    index = ["support", "counter", "qualify"].index(query.purpose)
                    if intents[index] is None:
                        intents[index] = await asyncio.to_thread(
                            self.outputs.put,
                            run_id,
                            f"{stage}:intent:{index}",
                            query.model_dump(),
                            dependency,
                        )
                for query in queries:
                    index = ["support", "counter", "qualify"].index(query.purpose)
                    results.append(searches[index] or await complete_query(index))
                return json.dumps([{k: r[k] for k in ("request", "hits")} for r in results], ensure_ascii=False)

        async def read_sources(unit_ids: list[str]) -> dict:
            """Read complete original units with required linked notes/owners and headers.

            Accepts returned search IDs or assigned topic IDs for direct-source fallback.
            At most three distinct reads. Related neighbor IDs can be read explicitly; originals are never truncated.
            """
            async with tool_lock:
                identities = sorted(set(unit_ids))
                if not identities or set(identities) - allowed():
                    return {"error": "Only search result IDs or assigned topic IDs are readable."}
                cached = next((r for r in reads if r and r["requested_ids"] == identities), None)
                if cached:
                    return cached
                if all(reads):
                    raise ApplicationError(
                        "读取次数不足以容纳主题证据，需拆分主题。",
                        type="card_input_budget",
                        non_retryable=True,
                    )
                selected = [known[i] for i in identities]
                expanded = {
                    u["unit_id"]: {k: v for k, v in u.items() if k != "context"}
                    for u in [
                        *selected,
                        *(
                            dict(extra, unit_id=extra["id"])
                            for unit in selected
                            for extra in unit.get("context", [])
                        ),
                    ]
                }
                receipt = {
                    "requested_ids": identities,
                    "units": list(expanded.values()),
                    "source_sha256": fingerprint(list(expanded.values())),
                }
                if estimate_request(receipt)["input_tokens"] > READ_TOKENS:
                    raise ApplicationError(
                        "单元及必需关联证据超过预算，需缩小主题。",
                        type="card_input_budget",
                        non_retryable=True,
                    )
                accumulated = {**read_units(), **expanded}
                if estimate_request(list(accumulated.values()))["input_tokens"] > READ_TOKENS:
                    raise ApplicationError(
                        "累计取证超过预算，需拆分主题后继续。", type="card_input_budget", non_retryable=True
                    )
                receipt["related_unit_ids"] = [
                    u["unit_id"]
                    for u in neighbor_context(all_units, selected)
                    if u["unit_id"] in known and u["unit_id"] not in expanded
                ]
                index = reads.index(None)
                tool_key = f"{stage}:read:{index}"
                reads[index] = await asyncio.to_thread(
                    self.outputs.put, run_id, tool_key, receipt, dependency
                )
                self.outputs.node(
                    run_id,
                    tool_key,
                    kind="tool",
                    label="读取证据原文",
                    objective=topic.objective,
                    parent=execution_parent.get() or parent,
                    state="completed",
                    details=receipt,
                )
                return receipt

        def read_view(receipt):
            if "units" not in receipt:
                return receipt
            packed = source_payload({"source_units": receipt["units"]})
            return {"units": [*packed["source_units"], *packed["context_units"]],
                    "related_unit_ids": receipt["related_unit_ids"]}

        @function_tool(failure_error_function=None)
        async def read_evidence(unit_ids: list[str]) -> str:
            """Read complete original units and linked notes; at most three distinct reads."""
            return json.dumps(read_view(await read_sources(unit_ids)), ensure_ascii=False)

        def validate(value):
            if {r["request"]["purpose"] for r in searches if r} != PURPOSES:
                raise ValueError("Search support, counter and qualify once each before completing research.")
            if set(value.source_unit_ids) - read_units().keys():
                raise ValueError("Only actually read source units may support the assessment.")
            cited = {identity for fact in value.findings for identity in fact.source_unit_ids}
            if cited != set(value.source_unit_ids) or any(
                not fact.source_unit_ids for fact in value.findings
            ):
                raise ValueError(
                    "Each finding requires read source IDs; the selected sources must match the findings."
                )
            if value.sufficient and not value.source_unit_ids:
                raise ValueError("Sufficient evidence requires at least one original source read.")
            if not value.sufficient and not value.unresolved_questions:
                raise ValueError("Explain the unresolved evidence before deferring.")

        # Finish interrupted queries before asking the model to continue. Dynamic
        # tool receipts are replayed by tools; planned searches use a frozen bootstrap below.
        for index, intent in enumerate(intents):
            if intent and searches[index] is None:
                await complete_query(index)

        initial = None
        if topic.queries and previous is None:
            if len(topic.queries) != 3 or {q.purpose for q in topic.queries} != PURPOSES:
                raise ValueError("Planned queries must cover three distinct evidence purposes.")
            # Existing saved plans without queries, and targeted repair research,
            # use the same tools below to plan dynamically. New plans skip those round trips.
            initial = await asyncio.to_thread(self.outputs.get, run_id, stage + ":bootstrap", dependency)
            if initial is None:
                for query in topic.queries:
                    index = ["support", "counter", "qualify"].index(query.purpose)
                    if intents[index] is None:
                        intents[index] = await asyncio.to_thread(
                            self.outputs.put, run_id, f"{stage}:intent:{index}", query.model_dump(), dependency
                        )
                for index in range(3):
                    if searches[index] is None:
                        await complete_query(index)
                selected = list(dict.fromkeys([
                    *topic.unit_ids,
                    *(u for row in searches for hit in row["hits"][:1] for u in hit["unit_ids"]),
                ]))
                try:
                    originals = read_view(await read_sources(selected))
                except ApplicationError as error:
                    if error.type != "card_input_budget":
                        raise
                    originals = {"units": [], "selection_required": True}
                initial = await asyncio.to_thread(
                    self.outputs.put, run_id, stage + ":bootstrap",
                    {"searches": [{k: r[k] for k in ("request", "hits")} for r in searches],
                     "originals": originals}, dependency,
                )

        result = await card_model(
            models,
            run_id,
            stage + ":research",
            "围绕主题主动检索证据。一次 search_evidence 提交 support、counter、qualify 各一项查询，"
            "随后 read_evidence 读取相关完整原文，必要时分批读取。预览和阅读线索不是引文。"
            "检索无命中可以直接读取指定主题单元；无命中不证明不存在反证。"
            "检查人物、时间、数量口径、作者/译者归属、反证和限定。findings 每条绑定实际已读来源 ID，"
            "分类记录具体发现，区分原文事实与推断；source_unit_ids 恰为这些发现使用的来源并集。"
            "预算不足或关键证据无法确定时 sufficient=false 并列出 unresolved_questions，不强行闭合。"
            "若有 initial_evidence，三类检索已经完成，originals.units 是程序已读取的完整原文。"
            "优先据此直接评估，不重复检索和读取；只有原文尚未取得或需要更多相关原文时才调用工具。"
            "预装配不保证证据充分；预览提示尚未读取的反证或口径差异时，必须补读或明确保留未决问题。",
            {
                "topic": topic.model_dump(),
                "clues": clues,
                "previous_check": previous,
                "corpus": corpus,
                **({"initial_evidence": initial} if initial is not None else {}),
            },
            EvidenceAssessment,
            model=self.settings.reasoning_model,
            parent=parent,
            tools=[search_evidence, read_evidence],
            validate=validate,
        )
        receipt = {
            "assessment": result.model_dump(),
            "units": list(read_units().values()),
            "searches": [r for r in searches if r],
            "corpus": corpus,
        }
        return await asyncio.to_thread(self.outputs.put, run_id, stage, receipt, dependency)
