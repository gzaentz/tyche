"""Run-bound native TYCHE tools inside the lab's existing gVisor sandbox."""

import argparse
import copy
import json
import os
from pathlib import Path
import threading

from .broker import Broker
from .output import deliver
from research_tools import ResearchTools, TOOLS
from tyche_tools import serve


def arena_schema(schema):
    """Hoist nested shapes to fit PR #198's 12-level operation JSON ceiling."""
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


def lab_tools():
    tools = copy.deepcopy(TOOLS)
    del tools["tyche_start"]
    tools["tyche_lookup"][1]["properties"]["checks"]["items"]["properties"]["provider"]["enum"] = ["deepline"]
    del tools["tyche_review"][1]["properties"]["web"]
    return {name: (description, arena_schema(schema)) for name, (description, schema) in tools.items()}


LAB_TOOLS = lab_tools()


def model_result(result):
    encoded = json.dumps(result, ensure_ascii=True)
    if len(encoded) <= 24000:
        return result
    return {"truncated": True, "status": result.get("status"), "review_ref": result.get("review_ref"),
            "preview": encoded[:8000],
            "next": "Read narrower fields with tyche_inspect. For final review, inspect each accepted company's evidence_review before approving review_ref. This preview is incomplete."}


class LabTools:
    def __init__(self, run_file, deadline):
        import lab_arena_checkpoint

        self.broker = Broker(os.environ["LAB_ARENA_WORKER_SOCKET"], deadline)
        icp = json.loads(json.loads(Path(run_file).read_text())["request"]["original_text"])
        self.lock = threading.Lock()
        self.delivered = False

        def save(path, validation):
            result = deliver(path, validation, icp, lab_arena_checkpoint.write)
            self.delivered = True
            return result

        self.research = ResearchTools(run_file, execute=self.broker.execute, deliver=save)

    def call(self, name, arguments):
        if name not in LAB_TOOLS:
            raise ValueError("The lab initialized this run; use its bound research tools")
        if name == "tyche_review" and "web" in arguments:
            raise ValueError("Lab evidence must come through the bound provider adapter")
        # Finish cannot race a review or a paid lookup. A lookup can still run
        # the normal three-check batch internally. Never alter delivered state.
        with self.lock:
            if self.delivered and (name != "tyche_inspect" or any(key in arguments for key in ("recover", "refresh", "query", "tool"))):
                raise ValueError("Reviewed JSON is delivered; end the Codex turn now")
            return model_result(self.research.call(name, arguments))


def main():
    from .runtime import require_lab

    require_lab()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-file", type=Path, required=True)
    parser.add_argument("--deadline", type=float, required=True)
    args = parser.parse_args()
    session = LabTools(args.run_file, args.deadline)
    try:
        # Already isolated by the lab. Do not use the local Codex sandbox relay.
        serve(session, tools=LAB_TOOLS)
    finally:
        session.broker.stopped.set()


if __name__ == "__main__":
    main()
