import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from run_costs import UsageReceipt, estimate, execute_with_usage, report


class RunCostsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.request = self.root / 'request.txt'
        self.request.write_text('Synthetic no-provider test.')

    def receipt(self):
        return UsageReceipt(self.request, 'gpt-5.6-luna', 'xhigh', 'fast')

    def test_caching_output_and_long_context_are_priced_separately(self):
        self.assertEqual(estimate({'input_tokens':1000, 'cached_input_tokens':800, 'cache_write_input_tokens':0,
                                  'output_tokens':100}, 'gpt-5.6-luna'), {'minimum':0.000176, 'maximum':0.000176})
        band = estimate({'input_tokens':300000, 'cached_input_tokens':200000, 'output_tokens':10000}, 'gpt-5.6-luna')
        self.assertEqual(band, {'minimum':0.036, 'maximum':0.076})
        for usage in ({'input_tokens':-1}, {'input_tokens':1,'cached_input_tokens':2,'output_tokens':0}):
            with self.assertRaises(ValueError): estimate(usage, 'gpt-5.6-luna')

    def test_real_process_stream_saves_usage_without_saving_tool_or_prompt_content(self):
        receipt = self.receipt()
        events = [{'type':'thread.started','thread_id':'fixture-thread'},
                  {'type':'item.completed','item':{'text':'sensitive fixture content'}},
                  {'type':'turn.completed','usage':{'input_tokens':1000,'cached_input_tokens':800,'output_tokens':100}}]
        command = [sys.executable, '-c', 'import json; events=' + repr(events) + '; [print(json.dumps(e)) for e in events]']
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(execute_with_usage(command, self.root, os.environ.copy(), receipt), 0)
        saved = json.loads(receipt.path.read_text())
        self.assertEqual(saved['status'], 'complete')
        self.assertEqual(saved['usage']['cached_input_tokens'], 800)
        self.assertEqual(saved['requested_service_tier'], 'fast')
        self.assertIsNone(saved['actual_model_billed_usd'])
        self.assertNotIn('sensitive fixture content', receipt.path.read_text())

    def test_failed_or_missing_usage_is_incomplete_not_free_and_attempts_are_unique(self):
        first, second = self.receipt(), self.receipt()
        self.assertNotEqual(first.path, second.path)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(execute_with_usage([sys.executable,'-c','raise SystemExit(2)'], self.root, os.environ.copy(), first), 2)
        self.assertEqual(json.loads(first.path.read_text())['status'], 'incomplete')
        self.assertIsNone(first.data['standard_api_equivalent_usd'])
        second.finish(0)
        self.assertEqual(second.data['status'], 'incomplete')

    def test_duplicate_usage_cannot_double_charge(self):
        receipt = self.receipt()
        event = {'type':'turn.completed','usage':{'input_tokens':1000,'cached_input_tokens':800,'output_tokens':100}}
        receipt.observe(event)
        with self.assertRaises(ValueError): receipt.observe(event)

    def test_capture_error_does_not_interrupt_worker_or_claim_complete_cost(self):
        receipt = self.receipt()
        event = {'type':'turn.completed','usage':{'input_tokens':10,'cached_input_tokens':0,'output_tokens':1}}
        marker = self.root / 'worker-finished'
        command = [sys.executable, '-c', 'import json; from pathlib import Path; event=' + repr(event)
                   + '; print(json.dumps(event)); print(json.dumps(event)); Path(' + repr(str(marker)) + ').touch()']
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(execute_with_usage(command, self.root, os.environ.copy(), receipt), 2)
        self.assertTrue(marker.exists())
        self.assertEqual(receipt.data['exit_code'], 0)
        self.assertEqual(receipt.data['status'], 'incomplete')

    def test_all_components_and_unknowns_are_exposed_in_combined_report(self):
        results = {'accepted':[{}]*5, 'cost_summary':{'deepline':{'confirmed_usd':0.266,'maximum_usd':0.608},
                                                    'scrapingdog':{'maximum_credits':0}}}
        missing = report(results, [])
        self.assertEqual(missing['status'], 'incomplete')
        self.assertIsNone(missing['combined_standard_equivalent_usd'])
        receipt = self.receipt()
        receipt.observe({'type':'turn.completed','usage':{'input_tokens':1000,'cached_input_tokens':800,'cache_write_input_tokens':0,'output_tokens':100}})
        receipt.finish(0)
        monitor = {'estimated_usd':{'minimum':1,'maximum':1}, 'basis':'standard_api_equivalent_not_actual_billing'}
        complete = report(results, [receipt.path], monitor, self.root)
        self.assertEqual(complete['combined_standard_equivalent_usd'], {'minimum':1.266176,'maximum':1.608176})
        self.assertEqual(complete['cost_per_accepted_lead_standard_equivalent_usd']['minimum'], 0.2532352)
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            report(results, [receipt.path, receipt.path], monitor, self.root)
        with self.assertRaisesRegex(ValueError, 'different run'):
            report(results, [receipt.path], monitor, self.root / 'another-run')


if __name__ == '__main__':
    unittest.main()
