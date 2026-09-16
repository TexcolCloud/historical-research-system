"""Native Agents SDK execution, with durable request/response references."""

import asyncio
import json
import time
from email.utils import parsedate_to_datetime

from agents import (
    Agent,
    AgentOutputSchema,
    MaxTurnsExceeded,
    ModelBehaviorError,
    ModelSettings,
    OpenAIResponsesModel,
    RunConfig,
    Runner,
    UserError,
)
from openai import APIConnectionError, APIStatusError, AsyncOpenAI, DefaultAsyncHttpxClient
from temporalio.exceptions import ApplicationError

from .books import get_run
from .domain.prompts import COMMON
from .outputs import Outputs, StageInputMismatch, execution_parent, fingerprint


class Models:
    def __init__(self, settings, engine):
        self.settings, self.outputs = settings, Outputs(settings, engine)

    def _defer(self, run_id, receipt):
        error = ApplicationError(
            "文本服务暂不可用，保留进度并等待自动恢复。",
            receipt,
            type="model_transport_wait",
            non_retryable=True,
        )
        # One activity owns this instance. Stop new sends, but drain requests already
        # in flight before handing the shared cooldown to the durable workflow timer.
        self._transport_failure = (run_id, receipt)
        raise error from None

    def _check_transport(self, run_id):
        failure = getattr(self, "_transport_failure", None)
        if failure and failure[0] == run_id:
            self._defer(run_id, failure[1])

    async def run(self, run_id, key, instructions, payload, output_type, **kwargs):
        # Provider schema mode can still return malformed JSON. Reuse the original
        # source and durable response feedback, with three content attempts.
        # Transport outages are separately paced by durable workflow timers.
        parent = kwargs.pop("parent", None) or execution_parent.get()
        close_dependency = {
            "instructions": instructions,
            "payload": payload,
            "schema": output_type.model_json_schema(),
            "model": kwargs.get("model"),
            "tools": [tool.name for tool in kwargs.get("tools") or []],
        }
        close_key = f"{key}:tool-closeout:{fingerprint(close_dependency)}"

        async def close(history):
            options = {name: value for name, value in kwargs.items() if name != "tools"}
            from .reading import card_model

            return await card_model(
                self,
                run_id,
                close_key + ":final",
                instructions
                + "\n工具读取阶段已结束。仅使用给定任务和已取得信息输出最终结果，不能要求继续调用工具。",
                {"task": payload, "completed_tool_history": history},
                output_type,
                parent=execution_parent.get() or parent,
                **options,
            )

        saved = None
        if kwargs.get("tools"):
            saved = await asyncio.to_thread(self.outputs.get, run_id, close_key, close_dependency)
        with self.outputs.operation(
            run_id, key, key[:100], kind="agent", objective=instructions[:200], parent=parent
        ):
            if saved is not None:
                return await close(saved["history"])
            for attempt in range(3):
                try:
                    return await self._attempt(
                        run_id, key, instructions, payload, output_type, parent=parent, **kwargs
                    )
                except MaxTurnsExceeded as error:
                    if not kwargs.get("tools"):
                        raise ApplicationError(
                            "收尾步骤未能返回完整输出。", type="model_output_invalid", non_retryable=True
                        ) from error
                    history = (
                        [item.to_input_item() for item in error.run_data.new_items] if error.run_data else []
                    )
                    await asyncio.to_thread(
                        self.outputs.put, run_id, close_key, {"history": history}, close_dependency
                    )
                    return await close(history)
                except StageInputMismatch:
                    raise
                except UserError as error:
                    # Agents SDK wraps tool exceptions. Preserve typed domain control
                    # signals (split, service wait, source conflict), not arbitrary errors.
                    cause, seen = error.__cause__, set()
                    while cause is not None and id(cause) not in seen:
                        if isinstance(cause, ApplicationError):
                            raise cause from None
                        seen.add(id(cause))
                        cause = cause.__cause__
                    raise
                except (ModelBehaviorError, ValueError) as error:
                    if attempt == 2:
                        raise ApplicationError(
                            "该模型步骤三次请求仍未通过输出校验，原始回执保留：" + str(error)[:500],
                            type="model_output_invalid",
                            non_retryable=True,
                        ) from error

    async def _attempt(
        self,
        run_id,
        key,
        instructions,
        payload,
        output_type,
        *,
        model=None,
        tools=None,
        parent=None,
        validate=None,
        validation_version="1",
        resume_context=None,
    ):
        # Callers explicitly select the reasoning model for planning/synthesis.
        # Model-name equality cannot distinguish roles when both use Flash.
        reasoning_call = model is not None
        effort = "high" if reasoning_call else "low"
        model = model or self.settings.reading_model
        maximum = self.settings.reasoning_max_output if reasoning_call else self.settings.reading_max_output
        schema = AgentOutputSchema(output_type)
        dependency = {
            "model": model,
            "max_output_tokens": maximum,
            "reasoning_effort": effort,
            "instructions": instructions,
            "input": payload,
            "schema": schema.json_schema(),
            "tools": [{"name": tool.name, "schema": tool.params_json_schema} for tool in tools or []],
        }
        if validation_version != "1":
            dependency["validation_version"] = validation_version
        recovery = get_run(self.outputs.engine, run_id)["recovery_attempt"]
        # Keep matching legacy results/receipts. Changed upstream repairs get a
        # separate immutable slot instead of overwriting or replaying old evidence.
        for storage_key in (key, f"{key}:input:{fingerprint(dependency)}"):
            request_prefix = f"{storage_key}:recovery:{recovery}" if recovery else storage_key
            try:
                cached = await asyncio.to_thread(self.outputs.get, run_id, storage_key, dependency)
                if cached is None:
                    await asyncio.to_thread(
                        self.outputs.get, run_id, f"{request_prefix}:request:1", dependency
                    )
            except StageInputMismatch:
                if storage_key != key:
                    raise
            else:
                break
        validation_feedback = []
        if cached is not None:
            try:
                value = output_type.model_validate(cached["output"])
                if validate:
                    validate(value)
                return value
            except ValueError as error:
                rejected = {
                    "output": cached,
                    "problem": str(error)[:4000],
                    "validation_version": validation_version,
                }
                await asyncio.to_thread(
                    self.outputs.put,
                    run_id,
                    f"{storage_key}:rejected:{fingerprint(rejected)}",
                    rejected,
                    dependency,
                )
                # One repair slot per input/policy. Never overwrite evidence or grow
                # an unbounded chain when the repaired output is also invalid.
                storage_key += f":validation-repair:{fingerprint(dependency)}"
                request_prefix = f"{storage_key}:recovery:{recovery}" if recovery else storage_key
                repaired = await asyncio.to_thread(self.outputs.get, run_id, storage_key, dependency)
                if repaired is not None:
                    value = output_type.model_validate(repaired["output"])
                    if validate:
                        validate(value)
                    return value
                validation_feedback.append({"attempt": 0, "problem": rejected["problem"]})
        if not self.settings.deepseek_api_key:
            raise ApplicationError("尚未配置 DeepSeek 文本模型密钥。", non_retryable=True)
        attempt = 1
        request_maximum = maximum
        last_transport = None
        failed_outputs = 0
        while (
            await asyncio.to_thread(
                self.outputs.get, run_id, f"{request_prefix}:request:{attempt}", dependency
            )
            is not None
        ):
            output_failure = False
            deferred = await asyncio.to_thread(
                self.outputs.get, run_id, f"{request_prefix}:request-deferred:{attempt}"
            )
            if deferred and not await asyncio.to_thread(
                self.outputs.get, run_id, f"{request_prefix}:http-request:{attempt}:1"
            ):
                # The shared circuit opened between saving the request intent and
                # reserving its HTTP send. Reuse that unused slot after cooldown.
                last_transport = deferred
                break
            last_transport = await asyncio.to_thread(
                self.outputs.get, run_id, f"{request_prefix}:transport-error:{attempt}"
            )
            previous = None
            response_number = 1
            # A tool round can exhaust its output after earlier completed tool calls.
            while response := await asyncio.to_thread(
                self.outputs.get, run_id, f"{request_prefix}:response:{attempt}:{response_number}"
            ):
                previous = response
                response_number += 1
            if (
                previous
                and previous.get("status") == "incomplete"
                and (previous.get("incomplete_details") or {}).get("reason") == "max_output_tokens"
            ):
                output_failure = True
                used_limit = previous.get("max_output_tokens") or request_maximum
                if used_limit >= self.settings.model_max_output_ceiling:
                    raise ApplicationError(
                        "模型达到配置的输出 token 上限，请缩小本步骤范围或调整输出上限；截断回执保留。",
                        type="model_output_limit",
                        non_retryable=True,
                    )
                request_maximum = min(used_limit * 2, self.settings.model_max_output_ceiling)
                validation_feedback.append(
                    {
                        "attempt": attempt,
                        "problem": "max_output_tokens：上次输出被截断，未被采用。请缩短重复分析和表述，返回完整 JSON。",
                    }
                )
            failure = await asyncio.to_thread(
                self.outputs.get, run_id, f"{request_prefix}:validation-error:{attempt}"
            )
            if failure:
                output_failure = True
                validation_feedback.append({"attempt": attempt, "problem": failure["problem"]})
            if previous and all(
                item.get("type") in {"message", "reasoning"} for item in previous.get("output", [])
            ):
                if previous and previous.get("status") == "completed":
                    text = "".join(
                        part["text"]
                        for item in previous.get("output", [])
                        if item.get("type") == "message"
                        for part in item.get("content", [])
                        if part.get("type") == "output_text"
                    )
                    try:
                        recovered = output_type.model_validate_json(text)
                        if validate:
                            validate(recovered)
                    except ValueError as error:
                        output_failure = True
                        if not failure:
                            validation_feedback.append({"attempt": attempt, "problem": str(error)[:4000]})
                    else:
                        await asyncio.to_thread(
                            self.outputs.put,
                            run_id,
                            storage_key,
                            {"output": recovered.model_dump(mode="json")},
                            dependency,
                        )
                        await asyncio.to_thread(
                            self.outputs.node,
                            run_id,
                            key,
                            kind="agent",
                            label=key[:100],
                            objective=instructions[:200],
                            parent=parent,
                            state="completed",
                            details={"recovered_response_id": previous.get("id"), "model": model},
                        )
                        return recovered
            if not last_transport and not deferred:
                failed_outputs += 1
            attempt += 1
        if failed_outputs >= 3:
            raise ApplicationError(
                "该模型步骤已达到三次请求上限，原始回执保留。",
                type="model_output_invalid" if output_failure else "model_request_exhausted",
                non_retryable=True,
            )
        if last_transport:
            if last_transport["retry_at"] is None:
                raise ApplicationError(
                    "先前请求被拒绝，需修正配置后手动重试。",
                    type="model_request_rejected",
                    non_retryable=True,
                )
            if time.time() < last_transport["retry_at"]:
                self._defer(run_id, last_transport)
        self._check_transport(run_id)
        await asyncio.to_thread(
            self.outputs.put,
            run_id,
            f"{request_prefix}:request:{attempt}",
            dependency,
            dependency,
        )
        responses = []
        requests = []
        phase, hook_error, status_code = "request", None, None
        started = time.monotonic()

        async def record_request(request):
            nonlocal phase, hook_error
            phase = "request_save"
            try:
                self._check_transport(run_id)
                body = json.loads((await request.aread()).decode("utf-8"))
                if tools:
                    from .domain.tokens import estimate_request
                    from .reading import CARD_INPUT_TOKENS

                    if estimate_request(body)["input_tokens"] > CARD_INPUT_TOKENS:
                        raise ApplicationError(
                            "工具回执累计超过制卡输入预算，保留证据并缩小主题。",
                            type="card_input_budget",
                            non_retryable=True,
                        )
                requests.append(body)
                await asyncio.to_thread(
                    self.outputs.reserve_request,
                    run_id,
                    f"{request_prefix}:http-request:{attempt}:{len(requests)}",
                    body,
                    self.settings.model_max_calls,
                )
            except Exception as error:
                hook_error = error
                if (
                    not requests
                    and isinstance(error, ApplicationError)
                    and error.type == "model_transport_wait"
                ):
                    await asyncio.to_thread(
                        self.outputs.put,
                        run_id,
                        f"{request_prefix}:request-deferred:{attempt}",
                        error.details[0],
                        dependency,
                    )
                raise
            phase = "request"
            if tools:
                deadline.reschedule(asyncio.get_running_loop().time() + self.settings.model_timeout_seconds)

        async def capture(response):
            nonlocal phase, hook_error, status_code
            phase, status_code = "response_read", response.status_code
            raw = await response.aread()
            if tools:
                deadline.reschedule(None)
            # Only protocol bodies are persisted; authorization headers are never serialized.
            try:
                value = json.loads(raw)
            except ValueError:
                value = {"status_code": response.status_code, "body_sha256": fingerprint(raw.hex())}
            responses.append(value)
            phase = "response_save"
            try:
                await asyncio.to_thread(
                    self.outputs.put,
                    run_id,
                    f"{request_prefix}:response:{attempt}:{len(responses)}",
                    value,
                    dependency,
                )
            except Exception as error:
                hook_error = error
                raise
            phase = "model_execution"

        client = AsyncOpenAI(
            api_key=self.settings.deepseek_api_key.get_secret_value(),
            base_url=self.settings.deepseek_base_url,
            max_retries=0,
            timeout=self.settings.model_timeout_seconds,
            http_client=DefaultAsyncHttpxClient(
                event_hooks={"request": [record_request], "response": [capture]}
            ),
        )
        agent = Agent(
            name=key[:100],
            instructions=(instructions if instructions.startswith(COMMON) else COMMON + "\n" + instructions)
            + "\n最终输出必须为给定 JSON Schema 的单个 JSON 对象，不加前言、Markdown 或代码围栏。",
            model=OpenAIResponsesModel(model, client),
            output_type=schema,
            tools=tools or [],
            model_settings=ModelSettings(
                store=False,
                max_tokens=request_maximum,
                reasoning={"effort": effort},
                parallel_tool_calls=False,
            ),
        )
        try:
            # HTTP requests retain their own timeout. Tool queue/inference time is
            # bounded by the tool, not charged against a whole multi-turn run.
            async with asyncio.timeout(None if tools else self.settings.model_timeout_seconds) as deadline:
                model_input = {**payload, **resume_context()} if resume_context else payload
                result = await Runner.run(
                    agent,
                    input=json.dumps(
                        {"task": model_input, "validation_feedback": validation_feedback}
                        if validation_feedback
                        else model_input,
                        ensure_ascii=False,
                        default=str,
                    ),
                    max_turns=4 if tools else 1,
                    run_config=RunConfig(tracing_disabled=True),
                )
            value = output_type.model_validate(result.final_output)
            if validate:
                validate(value)
            await asyncio.to_thread(
                self.outputs.put, run_id, storage_key, {"output": value.model_dump(mode="json")}, dependency
            )
            await asyncio.to_thread(
                self.outputs.node,
                run_id,
                key,
                kind="agent",
                label=key[:100],
                objective=instructions[:200],
                parent=parent,
                state="completed",
                details={
                    "model": model,
                    "response_ids": [item.get("id") for item in responses],
                    "output": value.model_dump(mode="json"),
                },
            )
            return value
        except (APIConnectionError, APIStatusError, TimeoutError) as error:
            # SDK hooks include local S3/SQL work. A wrapped persistence/budget
            # failure must never open the provider circuit or consume network retries.
            if hook_error is not None:
                raise hook_error from None
            if isinstance(error, TimeoutError) and phase not in {"request", "response_read"}:
                raise ApplicationError(
                    "模型执行或本地证据保存超时，已有证据保留。",
                    {"phase": phase},
                    type="model_execution_timeout",
                    non_retryable=True,
                ) from None
            status_code = getattr(error, "status_code", status_code)
            retryable = (
                not isinstance(error, APIStatusError) or status_code in {408, 409, 429} or status_code >= 500
            )
            causes, cause = [], error
            while cause is not None and len(causes) < 5:
                causes.append(type(cause).__name__)
                cause = cause.__cause__
            delay = min(60 * 2 ** min(attempt - 1, 5), 1800)
            if isinstance(error, APIStatusError) and retryable:
                header = error.response.headers.get("retry-after", "")
                try:
                    requested = (
                        float(header)
                        if header.isdecimal()
                        else parsedate_to_datetime(header).timestamp() - time.time()
                    )
                    delay = max(delay, requested)
                except (ValueError, TypeError, OverflowError):
                    pass
            now = time.time()
            receipt = {
                "step": key,
                "attempt": attempt,
                "phase": phase,
                "status_code": status_code,
                "cause_types": causes,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "failed_at": now,
                "retry_at": now + delay if retryable else None,
            }
            await asyncio.to_thread(
                self.outputs.put, run_id, f"{request_prefix}:transport-error:{attempt}", receipt, dependency
            )
            if not retryable:
                raise ApplicationError(
                    "文本服务拒绝请求，请检查模型配置、密钥或请求参数。",
                    receipt,
                    type="model_request_rejected",
                    non_retryable=True,
                ) from None
            self._defer(run_id, receipt)
        except (ModelBehaviorError, ValueError) as error:
            await asyncio.to_thread(
                self.outputs.put,
                run_id,
                f"{request_prefix}:validation-error:{attempt}",
                {"problem": str(error)[:4000]},
                dependency,
            )
            raise
        finally:
            await client.close()
