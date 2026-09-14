"""Native Agents SDK execution, with durable request/response references."""

import asyncio
import json

from agents import (
    Agent,
    AgentOutputSchema,
    ModelBehaviorError,
    ModelSettings,
    OpenAIResponsesModel,
    RunConfig,
    Runner,
)
from openai import AsyncOpenAI, DefaultAsyncHttpxClient
from temporalio.exceptions import ApplicationError

from .books import get_run
from .domain.prompts import COMMON
from .outputs import Outputs, StageInputMismatch, execution_parent, fingerprint


class Models:
    def __init__(self, settings, engine):
        self.settings, self.outputs = settings, Outputs(settings, engine)

    async def run(self, run_id, key, instructions, payload, output_type, **kwargs):
        # Provider schema mode can still return malformed JSON. Reuse the original
        # source and durable response feedback, with a bounded total of three sends.
        parent = kwargs.pop("parent", None) or execution_parent.get()
        with self.outputs.operation(
            run_id, key, key[:100], kind="agent", objective=instructions[:200], parent=parent
        ):
            for attempt in range(3):
                try:
                    return await self._attempt(
                        run_id, key, instructions, payload, output_type, parent=parent, **kwargs
                    )
                except StageInputMismatch:
                    raise
                except (ModelBehaviorError, ValueError) as error:
                    if attempt == 2:
                        raise ApplicationError(
                            "该模型步骤三次请求仍未通过输出校验，原始回执保留：" + str(error)[:500],
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
        if cached is not None:
            value = output_type.model_validate(cached["output"])
            if validate:
                validate(value)
            return value
        if not self.settings.deepseek_api_key:
            raise ApplicationError("尚未配置 DeepSeek 文本模型密钥。", non_retryable=True)
        attempt = 1
        validation_feedback = []
        request_maximum = maximum
        while (
            await asyncio.to_thread(
                self.outputs.get, run_id, f"{request_prefix}:request:{attempt}", dependency
            )
            is not None
        ):
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
                used_limit = previous.get("max_output_tokens") or request_maximum
                if used_limit >= self.settings.model_max_output_ceiling:
                    raise ApplicationError(
                        "模型达到配置的输出 token 上限，请缩小本步骤范围或调整输出上限；截断回执保留。",
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
                validation_feedback.append({"attempt": attempt, "problem": failure["problem"]})
            if not tools:
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
            attempt += 1
        if attempt > 3:
            raise ApplicationError("该模型步骤已达到三次请求上限，原始回执保留。", non_retryable=True)
        await asyncio.to_thread(
            self.outputs.put,
            run_id,
            f"{request_prefix}:request:{attempt}",
            dependency,
            dependency,
        )
        responses = []
        requests = []

        async def record_request(request):
            body = json.loads((await request.aread()).decode("utf-8"))
            requests.append(body)
            await asyncio.to_thread(
                self.outputs.reserve_request,
                run_id,
                f"{request_prefix}:http-request:{attempt}:{len(requests)}",
                body,
                self.settings.model_max_calls,
            )

        async def capture(response):
            raw = await response.aread()
            # Only protocol bodies are persisted; authorization headers are never serialized.
            try:
                value = json.loads(raw)
            except ValueError:
                value = {"status_code": response.status_code, "body_sha256": fingerprint(raw.hex())}
            responses.append(value)
            await asyncio.to_thread(
                self.outputs.put,
                run_id,
                f"{request_prefix}:response:{attempt}:{len(responses)}",
                value,
                dependency,
            )

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
            async with asyncio.timeout(self.settings.model_timeout_seconds):
                result = await Runner.run(
                    agent,
                    input=json.dumps(
                        {"task": payload, "validation_feedback": validation_feedback}
                        if validation_feedback
                        else payload,
                        ensure_ascii=False,
                        default=str,
                    ),
                    max_turns=6 if tools else 1,
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
