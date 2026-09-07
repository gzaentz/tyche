from __future__ import annotations

import random
import unittest

from test_output_contract import VALIDATOR


def has_cycle(node_ids: list[str], edges: dict[str, list[str]]) -> bool:
    """Independent Kahn oracle for directed-cycle detection."""
    indegree = {node_id: 0 for node_id in node_ids}
    for source in node_ids:
        for target in edges[source]:
            indegree[target] += 1
    ready = [node_id for node_id, degree in indegree.items() if degree == 0]
    visited = 0
    while ready:
        source = ready.pop()
        visited += 1
        for target in edges[source]:
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    return visited != len(node_ids)


def validate_graph(edges: dict[str, list[str]], **item_overrides):
    frontier = {
        route_id: {"state": "continuable", "continuation_route_ids": refs}
        for route_id, refs in edges.items()
    }
    for route_id, overrides in item_overrides.items():
        frontier[route_id].update(overrides)
    errors: list[str] = []
    VALIDATOR.validate_continuations(frontier, errors)
    return errors


class ContinuationMatrixTests(unittest.TestCase):
    def test_exhaustive_three_node_graphs_match_independent_cycle_oracle(self):
        node_ids = ["n0", "n1", "n2"]
        possible_edges = [(source, target) for source in node_ids for target in node_ids]
        for mask in range(1 << len(possible_edges)):
            edges = {node_id: [] for node_id in node_ids}
            for bit, (source, target) in enumerate(possible_edges):
                if mask & (1 << bit):
                    edges[source].append(target)
            errors = validate_graph(edges)
            cycle_errors = [error for error in errors if "cycle" in error]
            self.assertEqual(
                bool(cycle_errors),
                has_cycle(node_ids, edges),
                f"mask={mask} edges={edges} errors={errors}",
            )
            self.assertFalse(
                [error for error in errors if "missing continuation" in error],
                f"all exhaustive graph references must exist: {edges}",
            )

    def test_seeded_larger_graphs_match_independent_cycle_oracle(self):
        rng = random.Random(20260907)
        for case in range(150):
            node_ids = [f"r{index}" for index in range(rng.randint(4, 24))]
            edges = {
                source: [target for target in node_ids if rng.random() < 0.12]
                for source in node_ids
            }
            errors = validate_graph(edges)
            self.assertEqual(
                any("cycle" in error for error in errors),
                has_cycle(node_ids, edges),
                f"case={case} edges={edges} errors={errors}",
            )

    def test_deep_chain_is_valid_without_recursion(self):
        node_ids = [f"r{index}" for index in range(2500)]
        chain = {
            node_id: ([node_ids[index + 1]] if index + 1 < len(node_ids) else [])
            for index, node_id in enumerate(node_ids)
        }
        self.assertEqual(validate_graph(chain), [])

    def test_missing_duplicate_and_non_string_references_are_rejected(self):
        base = {"parent": [], "child": []}
        cases = (
            ({"parent": ["missing"]}, "missing continuation"),
            ({"parent": ["child", "child"]}, "duplicate continuation_route_ids"),
            ({"parent": ["child", 7]}, "must be an array of route IDs"),
            ({"parent": "child"}, "must be an array of route IDs"),
            ({"parent": [""], "child": []}, "must be an array of route IDs"),
        )
        for change, expected in cases:
            edges = {route_id: list(refs) for route_id, refs in base.items()}
            edges.update(change)
            errors = validate_graph(edges)
            self.assertTrue(any(expected in error for error in errors), (change, errors))

    def test_exhaustion_basis_requires_a_continuation(self):
        for basis in ("continuation_exhausted", "query_family_exhausted"):
            errors = validate_graph(
                {"parent": [], "child": []},
                parent={"state": "exhausted", "exhaustion_basis": basis},
            )
            self.assertTrue(any("must reference its continuation attempts" in error for error in errors))

    def test_exhausted_parent_rejects_actionable_child_but_allows_terminal_child(self):
        for parent_basis in ("continuation_exhausted", "query_family_exhausted", "no_results"):
            for child_state in ("untried", "continuable"):
                errors = validate_graph(
                    {"parent": ["child"], "child": []},
                    parent={"state": "exhausted", "exhaustion_basis": parent_basis},
                    child={"state": child_state},
                )
                self.assertTrue(any("actionable continuation" in error for error in errors))
            for child_state in ("exhausted", "blocked"):
                errors = validate_graph(
                    {"parent": ["child"], "child": []},
                    parent={"state": "exhausted", "exhaustion_basis": parent_basis},
                    child={"state": child_state},
                )
                self.assertFalse(any("actionable continuation" in error for error in errors))

    def test_non_exhausted_parent_can_reference_actionable_or_terminal_child(self):
        for parent_state in ("untried", "continuable", "blocked"):
            for child_state in ("untried", "continuable", "exhausted", "blocked"):
                errors = validate_graph(
                    {"parent": ["child"], "child": []},
                    parent={"state": parent_state},
                    child={"state": child_state},
                )
                self.assertFalse(any("actionable continuation" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
