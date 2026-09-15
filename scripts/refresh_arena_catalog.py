"""Refresh free provider metadata against a checked-out Leadpoet allowlist."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("leadpoet_source", type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(args.leadpoet_source.resolve()))
    from lab_arena.operations import DEEPLINE_TOOLS
    output = Path(__file__).resolve().parents[1] / "tyche_arena/catalog.json"
    tools = {}
    for name in DEEPLINE_TOOLS:
        result = subprocess.run(["deepline", "tools", "describe", name, "--json"],
                                capture_output=True, text=True, timeout=45, check=True)
        row = next(json.loads(line) for line in reversed(result.stdout.splitlines()) if line.startswith("{"))
        if row.get("toolId") != name or not row.get("callable") or row.get("disabled"):
            raise ValueError(f"Unavailable tool: {name}")
        # Public contract only. Omit account/connection metadata and examples.
        tools[name] = {k: row[k] for k in ("toolId", "provider", "description", "inputSchema", "pricing", "callable") if k in row}
    revision = subprocess.check_output(["git", "-C", str(args.leadpoet_source), "rev-parse", "HEAD"], text=True).strip()
    output.write_text(json.dumps({"leadpoet_revision": revision,
        "retrieved_at": datetime.now(timezone.utc).isoformat(), "tools": tools}, indent=2) + "\n")
    print(f"Saved {len(tools)} approved tool contracts to {output}")


if __name__ == "__main__":
    main()
