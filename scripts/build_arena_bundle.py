"""Stage only Arena runtime files; never include local reports or credentials."""

import argparse
from pathlib import Path
import shutil


def build(destination):
    root = Path(__file__).resolve().parents[1]
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    paths = [root / name for name in ("harness.py", "requirements.txt", "LICENSE")]
    paths += list((root / "tyche_arena").glob("*.py")) + [root / "tyche_arena/catalog.json"]
    skill = root / ".agents/skills/lead-sourcing"
    paths += [skill / "SKILL.md"] + list((skill / "scripts").glob("*.py"))
    paths += list((skill / "references").glob("*.md")) + list((skill / "assets").glob("*.json"))
    for source in paths:
        if source.is_symlink() or not source.is_file():
            raise ValueError("Expected a regular runtime source file: " + str(source))
        target = destination / source.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, help="New directory for the source bundle")
    print(build(parser.parse_args().destination))
