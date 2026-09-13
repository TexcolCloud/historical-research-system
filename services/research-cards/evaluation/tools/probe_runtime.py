"""Bounded real SDK connectivity check; synthetic content is not historical QA."""

import argparse
import asyncio
import base64
import importlib.metadata
import json
import os
import time
from pathlib import Path

from agents import (
    Agent,
    AgentOutputSchema,
    ModelSettings,
    OpenAIResponsesModel,
    RunConfig,
    Runner,
    RunState,
    function_tool,
    set_tracing_disabled,
)
from openai import AsyncOpenAI, DefaultAsyncHttpxClient
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, ConfigDict

from research_cards.records import atomic_json, now, sha256
from research_cards.settings import Settings


class ProbeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str
    values: list[int]
    missing: str | None


async def probe(settings, output, label, model, text, tools=None):
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    requests = []
    responses = []

    async def capture(request):
        raw = await request.aread()
        record = {
            "sequence": len(requests) + 1, "sent_at": now(), "method": request.method,
            "url": str(request.url), "body_sha256": sha256(raw), "body_bytes": len(raw),
            "body": json.loads(raw),
        }
        requests.append(record)
        atomic_json(output / f"request-{len(requests):02}.json", record)

    key = settings.deepseek_api_key.get_secret_value()
    client = AsyncOpenAI(
        api_key=key, base_url=settings.deepseek_base_url, max_retries=0,
        timeout=180, http_client=DefaultAsyncHttpxClient(event_hooks={"request": [capture]}),
    )
    agent = Agent(
        name=label,
        instructions="这是合成工程连接测试。只按输入或工具结果填写结构化输出，不补充内容。",
        model=OpenAIResponsesModel(model=model, openai_client=client),
        output_type=AgentOutputSchema(ProbeOutput),
        tools=tools or [],
        model_settings=ModelSettings(
            store=False, reasoning={"effort": "high"}, max_tokens=3000,
            preserve_raw_usage=True,
        ),
    )
    record = {"label": label, "requested_model": model, "started_at": now(), "kind": "synthetic_runtime_probe"}
    atomic_json(output / "intent.json", record)
    try:
        async with asyncio.timeout(240):
            result = Runner.run_streamed(
                agent, input=text, max_turns=4, run_config=RunConfig(tracing_disabled=True),
            )
            with (output / "events.jsonl").open("x", encoding="utf-8") as stream:
                async for event in result.stream_events():
                    if event.type != "raw_response_event":
                        continue
                    value = event.data.model_dump(mode="json", exclude_unset=True)
                    stream.write(json.dumps(value, ensure_ascii=False) + "\n")
                    stream.flush()
                    if value.get("type") in ("response.completed", "response.failed", "response.incomplete"):
                        responses.append(value["response"])
                        atomic_json(output / f"response-{len(responses):02}.json", value["response"])
                        os.fsync(stream.fileno())
            final = result.final_output.model_dump(mode="json")
            history = result.to_input_list()
            atomic_json(output / "history.json", history)
            state = result.to_state().to_json()
            atomic_json(output / "run-state.json", state)
            restored = await RunState.from_json(agent, state)
            restored_state = restored.to_json()
            atomic_json(output / "restored-state.json", restored_state)
            record.update({
                "status": "completed", "final_output": final,
                "response_statuses": [r.get("status") for r in responses],
                "usage": [r.get("usage") for r in responses],
                "response_models": [r.get("model") for r in responses],
                "history_items": len(history), "state_roundtrip": state == restored_state,
                "input_sha256": sha256(json.dumps(text, ensure_ascii=False).encode()),
            })
    except Exception as exc:  # noqa: BLE001 - capture any probe failure without losing protocol evidence
        # Exact request bodies and protocol events are local artifacts; never emit credentials.
        record.update({"status": "failed", "error_class": type(exc).__name__,
                       "message": str(exc).replace(key, "[REDACTED]")[:3000]})
    finally:
        await client.close()
    record.update({"elapsed_seconds": time.monotonic() - started,
                   "actual_requests": len(requests), "finished_at": now()})
    atomic_json(output / "result.json", record)
    print(json.dumps({k: record.get(k) for k in (
        "label", "status", "final_output", "actual_requests", "usage", "error_class", "message"
    )}, ensure_ascii=True), flush=True)
    return record


async def main(args):
    settings = Settings.load(env_file=args.env_file)
    if not settings.deepseek_api_key:
        raise SystemExit("DeepSeek API key is not configured")
    set_tracing_disabled(True)
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = {"started_at": now(), "versions": {
        package: importlib.metadata.version(package) for package in ("openai", "openai-agents")
    }, "kind": "synthetic_engineering_not_historical_quality", "results": []}
    tasks = [
        ("flash-structure", settings.reading_model, "label 填 工程73；values 填 [17,23]；missing 填 null。"),
        ("pro-structure", settings.reasoning_model, "label 填 工程73；values 填 [17,23]；missing 填 null。"),
    ]
    for label, model, prompt in tasks:
        manifest["results"].append(await probe(settings, args.output / label, label, model, prompt))
        atomic_json(args.output / "manifest.json", manifest)

    @function_tool
    async def read_probe_source(source_id: str) -> str:
        """Read the synthetic source by its exact ID."""
        return json.dumps({"label": source_id, "values": [17, 23], "missing": None})

    manifest["results"].append(await probe(
        settings, args.output / "flash-tool", "flash-tool", settings.reading_model,
        "必须先调用 read_probe_source，source_id 为 工程73，再根据工具结果填写输出。",
        [read_probe_source],
    ))
    atomic_json(args.output / "manifest.json", manifest)
    # A deterministic positive image check, explicitly outside the historical dataset.
    picture = Image.new("RGB", (800, 440), "white")
    draw = ImageDraw.Draw(picture)
    font = ImageFont.load_default(size=70)
    draw.text((40, 40), "K7319", font=font, fill="black")
    draw.text((40, 170), "17", font=font, fill="black")
    draw.text((390, 170), "23", font=font, fill="black")
    picture.save(args.output / "probe-original.png")
    raw = (args.output / "probe-original.png").read_bytes()
    manifest["image_sha256"] = sha256(raw)
    if not args.skip_vision:
        manifest["results"].append(await probe(
            settings, args.output / "vision-image", "vision-image", settings.vision_model,
            [{"role": "user", "content": [
                {"type": "input_text", "text": "读取图片，label 填上方字母数字代码，values 依次填下方两个数字，missing 填 null。"},
                {"type": "input_image", "image_url": "data:image/png;base64," + base64.b64encode(raw).decode(), "detail": "high"},
            ]}],
        ))
    manifest["finished_at"] = now()
    atomic_json(args.output / "manifest.json", manifest)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--skip-vision", action="store_true")
    asyncio.run(main(parser.parse_args()))
