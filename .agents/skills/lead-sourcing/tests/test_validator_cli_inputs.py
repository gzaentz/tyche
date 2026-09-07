import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_output_contract import VALIDATOR, VALIDATOR_PATH, shortfall_result


class ValidatorCliInputTests(unittest.TestCase):
    def run_cli(self, document, flags, raw=None):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "results.json"
            path.write_bytes(json.dumps(document).encode("utf-8") if raw is None else raw)
            before = path.read_bytes()
            result = subprocess.run([sys.executable, str(VALIDATOR_PATH), str(path), *flags],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertEqual(path.read_bytes(), before)
            output = json.loads(result.stdout)
            self.assertFalse(output["valid"])
            self.assertTrue(output["errors"])
            return output

    def test_non_object_drafts_return_json_even_with_diagnostic_flags(self):
        for document in (None, [], "unfinished", 7, False):
            for flags in ([], ["--show-cost-summary"], ["--show-progress", "--show-cost-summary"]):
                with self.subTest(document=document, flags=flags):
                    output = self.run_cli(document, flags)
                    self.assertNotIn("calculated_cost_summary", output)
                    self.assertNotIn("progress", output)

    def test_invalid_collection_types_and_schema_version_return_errors_without_mutation(self):
        for field in ("routes", "rejected", "unresolved", "accepted", "schema_version"):
            values = (None, {}, "unfinished", False) if field != "schema_version" else ([], {}, False, 7)
            for value in values:
                with self.subTest(field=field, value=value):
                    document = shortfall_result()
                    document[field] = value
                    before = copy.deepcopy(document)
                    errors = VALIDATOR.validate_run(document)
                    self.assertTrue(any(field in error for error in errors), errors)
                    self.assertEqual(document, before)

    def test_null_collection_draft_cli_still_returns_available_diagnostics(self):
        for field in ("routes", "rejected", "unresolved"):
            with self.subTest(field=field):
                document = shortfall_result()
                document[field] = None
                output = self.run_cli(document, ["--show-progress", "--show-cost-summary"])
                self.assertTrue(any(field in error for error in output["errors"]))
                self.assertIn("progress", output)
                self.assertIn("calculated_cost_summary", output)

    def test_invalid_utf8_file_is_reported_without_traceback(self):
        self.run_cli(None, ["--show-progress", "--show-cost-summary"], raw=b'{"request": "\xff"}')


if __name__ == "__main__":
    unittest.main()
