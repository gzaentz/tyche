from __future__ import annotations

import pathlib
import re
import unittest
from urllib.parse import unquote, urlsplit


ROOT = pathlib.Path(__file__).resolve().parents[1]
REFERENCES = ROOT / "references"


def prose(text: str) -> str:
    return re.sub(r"^```[^\n]*\n.*?^```[^\n]*$", "", text, flags=re.M | re.S)


def local_links(text: str):
    for target in re.findall(r"\[[^\]\n]*\]\(([^)\n]+)\)", prose(text)):
        target = target.strip("<>")
        if not urlsplit(target).scheme:
            yield target


def heading_anchors(text: str) -> set[str]:
    seen = {}
    anchors = set()
    for heading in re.findall(r"^#{1,6} (.+?)\s*#*\s*$", prose(text), re.M):
        slug = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        anchors.add(f"{slug}-{count}" if count else slug)
    return anchors


class ReferenceLoadingTests(unittest.TestCase):
    def test_tool_index_stays_small_and_routes_to_adapter_contracts(self):
        text = (REFERENCES / "tools.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(text.split()), 1000)
        targets = {link.split("#", 1)[0] for link in local_links(text)}
        self.assertTrue({
            "adapter-io.md", "deepline-adapter.md", "scrapingdog-adapter.md",
            "provider-capabilities.md",
        }.issubset(targets))

    def test_skill_routes_to_setup_persistence_and_delivery_sections(self):
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        targets = set(local_links(text))
        required = {
            "references/workflow-rules.md",
            "references/output-contract.md#lifecycle-invariants",
            "references/output-contract.md#input-contract",
            "references/output-contract.md#timing",
            "references/output-contract.md#semantic-checks",
            "references/output-contract.md#accepted-lead-sources",
            "references/output-contract.md#resultsjson-schema",
            "references/output-contract.md#client-writing-and-taxonomy-version-12",
            "references/output-contract.md#leadsxlsx-contract",
            "references/output-contract.md#reportmd-minimum-contents",
            "references/output-contract.md#final-response-checklist",
        }
        self.assertTrue(required.issubset(targets), sorted(required - targets))

    def test_reference_files_and_fragment_targets_exist(self):
        paths = [ROOT / "SKILL.md", ROOT.parents[2] / "README.md"]
        paths.extend(sorted(REFERENCES.glob("*.md")))
        for path in paths:
            for target in local_links(path.read_text(encoding="utf-8")):
                file_name, _, fragment = target.partition("#")
                destination = path.parent / unquote(file_name) if file_name else path
                with self.subTest(source=path.name, target=target):
                    self.assertTrue(destination.is_file(), str(destination))
                    if fragment:
                        self.assertIn(
                            unquote(fragment),
                            heading_anchors(destination.read_text(encoding="utf-8")),
                        )


if __name__ == "__main__":
    unittest.main()
