"""One PydanticAI research agent, one bound run, one reviewed JSON result."""

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import time

import httpx
from openai import AsyncOpenAI
from pydantic_ai import Agent, ModelRetry, Tool
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.usage import UsageLimits

from . import SKILL
from .broker import Broker
from .constraints import validate_constraints
from .output import deliver
from research_tools import ResearchTools, TOOLS


class DeliveryComplete(Exception):
    """End the agent loop immediately after reviewed output is saved."""


def request_for(icp, limit, duration):
    if not isinstance(icp, dict):
        raise ValueError("ICP must be an object")
    if icp.get("intent_details_policy") != "intent_details_v1" or icp.get("contact_policy") != "contacts_v1":
        raise ValueError("This bundle supports intent_details_v1 + contacts_v1 rounds")
    validate_constraints(icp)
    if icp.get("required_attribute") is not None and not isinstance(icp["required_attribute"], str):
        raise ValueError("required_attribute must be text; structured attributes are unsupported")
    if icp.get("bonus_intents"):
        raise ValueError("This bundle requires intent_signals without separate bonus_intents")
    roles, signals = icp.get("target_roles"), icp.get("intent_signals")
    if any(not isinstance(values, list) or not values or any(not isinstance(v, str) or not v.strip() for v in values)
           for values in (roles, signals)):
        raise ValueError("ICP needs nonempty lists of text for target_roles and intent_signals; structured signals are unsupported")
    signals = [value.strip() for value in signals]
    if len(set(signals)) != len(signals):
        raise ValueError("intent_signals must be unique to preserve Arena's signal indexes")
    age = icp.get("intent_max_age_days", 365)
    if type(age) is not int or age < 1:
        raise ValueError("intent_max_age_days must be positive")
    criteria = {}
    exclusions = icp.get("excluded_companies") or []
    if not isinstance(exclusions, list) or any(not isinstance(v, str) or not v.strip() for v in exclusions):
        raise ValueError("excluded_companies must be a list of text")
    if exclusions:
        criteria["exclusions"] = exclusions
    for source, target in (("industry", "industries"), ("geography", "geographies")):
        if icp.get(source):
            criteria[target] = [icp[source]]
    attributes = [icp["required_attribute"]] if icp.get("required_attribute") else []
    for key in ("sub_industry", "company_stage", "country", "state"):
        if icp.get(key):
            attributes.append(key + ": " + str(icp[key]))
    if icp.get("employee_count"):
        attributes.append("Employee range is one of: " + json.dumps(icp["employee_count"]))
    if attributes:
        criteria["required_attributes"] = attributes
    criteria.setdefault("custom_criteria", [icp.get("prompt") or "Match the supplied company criteria"])
    request = {"target_count": limit, "icp": criteria, "requested_roles": roles,
        "buying_signals": [{"kind": f"arena_signal_{index}", "query": signal, "importance": "required",
                            "max_age_days": age} for index, signal in enumerate(signals)],
        "signal_match_mode": "any", "time_window": {"max_age_days": age},
        "contact_fields": ["email"], "max_duration_seconds": duration,
        "original_text": json.dumps(icp, ensure_ascii=True, allow_nan=False),
        "as_of_date": os.environ.get("LAB_ARENA_EVALUATION_DATE") or datetime.now(timezone.utc).date().isoformat()}
    if icp.get("product_service"):
        request["product_service"] = {"description": icp["product_service"], "perspective": "target"}
    return request


class OpenRouterTransport(httpx.AsyncBaseTransport):
    def __init__(self, broker):
        self.broker = broker

    async def handle_async_request(self, request):
        body = json.loads(request.content)
        allowed = {"model", "messages", "tools", "tool_choice", "parallel_tool_calls", "reasoning",
                   "reasoning_effort", "temperature", "max_tokens", "top_p", "stop", "seed", "response_format", "include_reasoning"}
        body = {k: v for k, v in body.items() if k in allowed}
        for message in body.get("messages", []):
            for key in list(message):
                if key not in {"role", "content", "name", "tool_call_id", "tool_calls"} or message[key] is None:
                    message.pop(key)
        # Keep complete assistant/tool exchanges. Saved run state and receipts
        # remain authoritative and can be read with inspect after compaction.
        messages = body.get("messages", [])
        starts = [i for i, message in enumerate(messages) if message.get("role") == "assistant"]
        if starts:
            cut = max(starts[max(0, len(starts) - 20)], next((i for i in starts if len(messages) - i <= 100), starts[-1]))
            messages = messages[:starts[0]] + messages[cut:]
        for message in messages[:-4]:
            if message.get("role") == "tool" and len(message.get("content", "")) > 1500:
                message["content"] = json.dumps({"history_truncated": True, "preview": message["content"][:1000],
                    "next": "Read authoritative state and source receipts with tyche_inspect."})
        body["messages"] = messages
        status, headers, payload = await asyncio.to_thread(self.broker.request, "openrouter.chat", body)
        return httpx.Response(status, json=payload, request=request)


def instructions():
    return (SKILL / "SKILL.md").read_text() + "\n\n" + (
        "Arena integration context overrides only local setup/delivery: the host initialized the authoritative request. "
        "Use tyche_inspect to read it; do not change it. tyche_guide reads the shared references without a shell. "
        "There is no built-in web tool; use the approved exa_search/exa_contents or other catalogued provider tools. "
        "Catalog metadata is bundled because Arena has no catalog endpoint. Unlisted operations and ScrapingDog are unavailable here. "
        "The host enforces model/provider costs and quotas. Local provider reservations remain binding. "
        "Research must finish within the displayed deadline. Preserve the supplied signal order: arena_signal_N maps to intent_signals[N]. "
        "Review contact_geography and target_seniority from original_text for the actual selected contact. "
        "First get a basic HarvestAPI profile with main='true' and review the current buyer. Then use that contact_ref "
        "for harvestapi_get_profile with findEmail='true' (omit main and other add-ons; code supplies identity inputs). "
        "Review the enriched profile ref as primary_contact.ref, choose its returned email, and validate it through TYCHE's normal email gate. "
        "Store company HQ country/state and company_stage only from observed evidence. "
        "Keep intent_details to one plain paragraph, at most 2000 characters. "
        "tyche_finish returns the usual final evidence packet; inspect its original source passages before approving review_ref. "
        "Approved output is saved and checkpointed as Arena JSON. No workbook, preview, launcher or model-cost report is needed. "
        "A final text message cannot deliver leads. Successful tyche_finish ends the run."
    )


def guide(document: str, offset: int = 0) -> dict:
    """Read shared sourcing guidance in bounded chunks; names come from SKILL.md."""
    if document not in {"workflow-rules", "output-contract", "tools", "adapter-io", "deepline-adapter", "provider-pricing"}:
        raise ValueError("Unknown guide")
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be nonnegative")
    content = (SKILL / "references" / (document + ".md")).read_text()
    return {"text": content[offset:offset + 16000], "next_offset": offset + 16000 if len(content) > offset + 16000 else None}


def arena_schema(schema):
    """Use local references to fit Arena's bounded JSON depth without losing fields."""
    definitions = {}
    def visit(value, root=False):
        if isinstance(value, list):
            return [visit(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {key: visit(child) for key, child in value.items()}
        if not root and value.get("type") in {"object", "array"}:
            key = "Shape" + str(len(definitions))
            definitions[key] = result
            return {"$ref": "#/$defs/" + key}
        return result
    result = visit(schema, root=True)
    if definitions:
        result["$defs"] = definitions
    return result


def model_result(result):
    encoded = json.dumps(result, ensure_ascii=True)
    if len(encoded) <= 24000:
        return result
    metadata = result if isinstance(result, dict) else {}
    return {"truncated": True, "status": metadata.get("status"), "review_ref": metadata.get("review_ref"),
        "preview": encoded[:8000],
        "next": "Read narrower fields or page saved state with tyche_inspect. For a final review, inspect each accepted company's evidence_review before approving this review_ref. The preview is incomplete."}


async def research(icp, run_dir, broker, *, limit, duration, model=None, checkpoint=None):
    delivered = None

    def save(path, validation):
        nonlocal delivered
        result = deliver(path, validation, icp, checkpoint)
        delivered = result["companies"]
        return result

    research_tools = ResearchTools(run_dir / "results.json", execute=broker.execute, deliver=save)
    research_tools.start(request=request_for(icp, limit, duration), max_usd=0.5 * limit)

    def wrapper(name):
        async def call(**arguments):
            try:
                result = await asyncio.to_thread(research_tools.call, name, arguments)
            except ValueError as exc:
                raise ModelRetry(str(exc)) from exc
            if name == "tyche_finish" and result.get("delivery_allowed"):
                raise DeliveryComplete
            return model_result(result)
        return call

    tools = [Tool.from_schema(wrapper(name), name, description, arena_schema(schema), sequential=True)
             for name, (description, schema) in TOOLS.items() if name != "tyche_start"]
    tools.append(Tool(guide, name="tyche_guide", sequential=True))
    async with httpx.AsyncClient(transport=OpenRouterTransport(broker), trust_env=False) as client:
        # A public dummy handle satisfies the SDK; it never enters a frame.
        sdk = AsyncOpenAI(api_key="arena-host", base_url="http://openrouter.ai/api/v1", http_client=client, max_retries=0)
        if model is None:
            model = OpenRouterModel("openai/gpt-5.6-sol", provider=OpenRouterProvider(openai_client=sdk),
                settings={"max_tokens": 4096, "parallel_tool_calls": False,
                          "openrouter_reasoning": {"effort": "medium", "exclude": True}})
        agent = Agent(model, instructions=instructions(), tools=tools, retries=4)

        @agent.output_validator
        def reviewed_output(ctx, value):
            if delivered is None:
                raise ModelRetry("Delivery has not passed. Resolve gaps, inspect tyche_finish's evidence packet, then approve its current review_ref.")
            return value

        try:
            await agent.run("Research the saved ICP. Start with tyche_inspect. Research deadline: " + str(duration) +
                            " seconds. Return at most " + str(limit) + " companies.", usage_limits=UsageLimits(request_limit=60))
        except DeliveryComplete:
            pass
    return delivered


def run(icp):
    limit = int(os.environ.get("LAB_ARENA_COMPANY_LIMIT", "5"))
    if not 1 <= limit <= 5:
        raise ValueError("LAB_ARENA_COMPANY_LIMIT must be 1 through 5")
    # The runner currently does not pass its signed wall-clock limit into the
    # bundle. Stay inside the legacy five-minute boundary for either policy.
    timeout, duration = 285, 255
    request_for(icp, limit, duration)  # Fail before creating state or spending.
    run_dir = Path(tempfile.mkdtemp(prefix="tyche-arena-"))
    broker = Broker(os.environ.get("LAB_ARENA_WORKER_SOCKET", ""), time.monotonic() + timeout)
    output_path = os.environ.get("LAB_ARENA_OUTPUT_PATH")
    checkpoint = None
    if output_path:
        import lab_arena_checkpoint
        checkpoint = lambda rows: lab_arena_checkpoint.write(rows, output_path=Path(output_path))

    async def bounded():
        async with asyncio.timeout(timeout):
            return await research(icp, run_dir, broker, limit=limit, duration=duration, checkpoint=checkpoint)

    try:
        return asyncio.run(bounded())
    except BaseException as exc:
        (run_dir / "failure.json").write_text(json.dumps({"error": type(exc).__name__, "message": str(exc)[:2000]}))
        raise
    finally:
        broker.stopped.set()
