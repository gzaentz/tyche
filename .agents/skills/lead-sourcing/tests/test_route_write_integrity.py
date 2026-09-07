import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


from test_record_route import MODULE


def frontier(route_id, state="exhausted", refs=(), **overrides):
    route = {
        "route_id": route_id,
        "phase": "account_discovery",
        "provider": "public_web",
        "operation": "search",
        "request_summary": f"query {route_id}",
        "state": state,
        "reason": f"reason {route_id}",
    }
    if refs:
        route["continuation_route_ids"] = list(refs)
    route.update(overrides)
    return route


def receipt(route):
    result = {
        key: route[key] for key in MODULE.IDENTITY
    }
    if route.get("exhaustion_basis") == "no_results":
        result.update(provider_status="no_results", rows_returned=0)
    else:
        result["provider_status"] = "ok"
    return result


def linked_document(*routes):
    return {
        "routes": [receipt(route) for route in routes],
        "accepted": [{"preserved": True}],
        "stop_audit": {
            "frontier_complete": False,
            "route_frontier": copy.deepcopy(list(routes)),
        },
    }


class RouteWriteIntegrityTests(unittest.TestCase):
    def test_record_rejects_multi_route_continuation_cycle_without_mutating_input(self):
        first = frontier("first", exhaustion_basis="no_results")
        second = frontier("second", refs=["first"], exhaustion_basis="continuation_exhausted")
        document = linked_document(first, second)
        before = copy.deepcopy(document)

        with self.assertRaises(ValueError):
            MODULE.record(document, dict(first, continuation_route_ids=["second"],
                                         exhaustion_basis="continuation_exhausted", reason="attempted rewrite"))

        self.assertEqual(document, before)

    def test_record_rejects_reopening_child_referenced_by_exhausted_parent(self):
        parent = frontier("parent", refs=["child"], exhaustion_basis="continuation_exhausted")
        child = frontier("child", exhaustion_basis="no_results")
        document = linked_document(parent, child)
        before = copy.deepcopy(document)

        with self.assertRaises(ValueError):
            MODULE.record(document, dict(child, state="continuable", reason="retry"))

        self.assertEqual(document, before)

    def test_persist_rejects_non_locking_edit_after_read_without_overwriting_disk(self):
        route = frontier("one", state="untried")
        original = linked_document()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "results.json"
            path.write_text(json.dumps(original), encoding="utf-8")
            concurrent = json.dumps({"concurrent": True, "preserved": "user edit"})
            real_record = MODULE.record

            def edit_after_read(document, update, receipt=None):
                path.write_text(concurrent, encoding="utf-8")
                return real_record(document, update, receipt)

            with mock.patch.object(MODULE, "record", side_effect=edit_after_read):
                with self.assertRaises(OSError):
                    MODULE.persist(path, {"frontier": route})

            self.assertEqual(path.read_text(encoding="utf-8"), concurrent)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_reopening_parent_then_child_and_closing_child_then_parent_is_valid(self):
        parent = frontier("parent", refs=["child"], exhaustion_basis="continuation_exhausted")
        child = frontier("child", exhaustion_basis="no_results")
        document = linked_document(parent, child)
        reopened = MODULE.record(document, dict(parent, state="continuable"))
        reopened = MODULE.record(reopened, dict(child, state="continuable"))
        closed = MODULE.record(reopened, child)
        closed = MODULE.record(closed, parent)
        self.assertEqual(closed, document)

    def test_symlink_and_replace_failure_preserve_user_data_and_clean_temporary_files(self):
        route = frontier("one", state="untried")
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "user.json"
            target.write_text(json.dumps(linked_document()), encoding="utf-8")
            original = target.read_bytes()
            path = Path(directory) / "results.json"
            path.symlink_to(target)
            with self.assertRaises(OSError):
                MODULE.persist(path, {"frontier": route})
            self.assertTrue(path.is_symlink())
            self.assertEqual(target.read_bytes(), original)
            with mock.patch.object(MODULE.os, "replace", side_effect=OSError("injected rename failure")):
                with self.assertRaises(OSError):
                    MODULE.persist(target, {"frontier": route})
            self.assertEqual(target.read_bytes(), original)
            self.assertEqual(set(Path(directory).iterdir()), {path, target})


if __name__ == "__main__":
    unittest.main()
