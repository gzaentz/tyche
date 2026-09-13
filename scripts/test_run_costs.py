import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import uuid

from run_costs import UsageJournal, UsageReceipt, estimate, execute_with_usage, report, save_report


class RunCostsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.request = self.root / 'request.txt'
        self.request.write_text('Synthetic no-provider test.')
        self.thread = str(uuid.uuid4())
        self.usage = dict(input_tokens=1000, cached_input_tokens=800, cache_write_input_tokens=0,
                          output_tokens=100, reasoning_output_tokens=50, total_tokens=1100)

    def receipt(self):
        receipt = UsageReceipt(self.request, 'gpt-5.6-luna', 'xhigh', 'fast')
        receipt.observe({'type': 'thread.started', 'thread_id': self.thread})
        return receipt

    def response(self, usage=None, response_id='response-1'):
        return {'type': 'token_usage_record', 'timestamp': '2026-09-13T00:00:00Z',
                'payload': {'thread_id': self.thread, 'turn_id': 'turn-1', 'response_id': response_id,
                            'usage': usage or self.usage}}

    def record_response(self, receipt, usage=None, response_id='response-1'):
        record = self.response(usage, response_id)
        receipt.observe_response(record['payload'], record['timestamp'], 'gpt-5.6-luna')

    def completed(self):
        receipt = self.receipt()
        self.record_response(receipt)
        receipt.observe({'type': 'turn.completed', 'usage': self.usage})
        receipt.finish(0)
        return receipt

    def journal_path(self):
        path = self.root / 'profile' / 'sessions' / '2026' / '09' / '13' / ('rollout-' + self.thread + '.jsonl')
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def results(self, high=0.831):
        return {'accepted': [{}]*5, 'cost_summary': {'deepline': {'confirmed_usd': 0.831, 'maximum_usd': high},
                                                  'scrapingdog': {'maximum_credits': 0}}}

    def test_caching_writes_output_and_context_are_priced_separately(self):
        self.assertEqual(estimate(self.usage, 'gpt-5.6-luna', per_request=True),
                         {'minimum': 0.000176, 'maximum': 0.000176})
        usage = dict(input_tokens=300000, cached_input_tokens=200000, cache_write_input_tokens=10000,
                     output_tokens=10000, total_tokens=310000)
        self.assertEqual(estimate(usage, 'gpt-5.6-luna', per_request=True), {'minimum': 0.067, 'maximum': 0.067})
        usage.pop('cache_write_input_tokens')
        self.assertEqual(estimate(usage, 'gpt-5.6-luna'), {'minimum': 0.036, 'maximum': 0.076})
        for bad in ({'input_tokens': -1}, dict(self.usage, cached_input_tokens=1001), dict(self.usage, total_tokens=1)):
            with self.assertRaises(ValueError):
                estimate(bad, 'gpt-5.6-luna')

    def test_aggregate_input_does_not_trigger_per_request_long_context_rates(self):
        receipt = self.receipt()
        usage = dict(self.usage, input_tokens=200000, cached_input_tokens=100000, total_tokens=200100)
        self.record_response(receipt, usage, 'first')
        self.record_response(receipt, usage, 'second')
        receipt.observe({'type': 'turn.completed', 'usage': {k: v*2 for k, v in usage.items()}})
        receipt.finish(0)
        self.assertEqual(receipt.data['status'], 'complete')
        self.assertEqual(receipt.data['standard_api_equivalent_usd'], {'minimum': 0.04424, 'maximum': 0.04424})

    def test_real_process_stream_and_journal_capture_only_numeric_metadata(self):
        receipt = self.receipt()
        path = self.journal_path()
        records = [{'type': 'turn_context', 'payload': {'model': 'gpt-5.6-luna', 'instructions': 'private prompt'}},
                   {'type': 'response_item', 'payload': {'text': 'private tool data'}}, self.response()]
        events = [{'type': 'thread.started', 'thread_id': self.thread},
                  {'type': 'item.completed', 'item': {'text': 'private tool data'}},
                  {'type': 'turn.completed', 'usage': self.usage}]
        program = ('import json; from pathlib import Path; Path(' + repr(str(path)) + ').write_text('
                   + repr(''.join(json.dumps(r)+'\n' for r in records)) + '); events=' + repr(events)
                   + '; [print(json.dumps(e)) for e in events]')
        with contextlib.redirect_stdout(io.StringIO()):
            code = execute_with_usage([sys.executable, '-c', program], self.root, os.environ.copy(),
                                      receipt, profile=self.root / 'profile')
        self.assertEqual(code, 0)
        saved = json.loads(receipt.path.read_text())
        self.assertEqual(saved['status'], 'complete')
        self.assertTrue(saved['usage_reconciled'])
        self.assertEqual(saved['response_usage_totals'], self.usage)
        self.assertIsNone(saved['actual_model_billed_usd'])
        self.assertNotIn('private prompt', receipt.path.read_text())
        self.assertNotIn('private tool data', receipt.path.read_text())

    def test_large_and_partial_journal_rows_are_skipped_or_retried(self):
        receipt = self.receipt()
        path = self.journal_path()
        row = json.dumps(self.response()).encode() + b'\n'
        path.write_bytes(json.dumps({'type': 'response_item', 'payload': {'text': 'x'*200000}}).encode()
                         + b'\n' + row[:70])
        journal = UsageJournal(self.root / 'profile', receipt)
        journal.poll()
        self.assertEqual(receipt.data['responses'], [])
        with path.open('ab') as stream:
            stream.write(row[70:])
        journal.poll()
        journal.poll()
        self.assertEqual(len(receipt.data['responses']), 1)

    def test_duplicate_conflicting_and_cross_worker_records(self):
        receipt = self.receipt()
        self.record_response(receipt)
        self.record_response(receipt)
        self.assertEqual(len(receipt.data['responses']), 1)
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            self.record_response(receipt, dict(self.usage, output_tokens=101, total_tokens=1101))
        record = self.response()['payload']
        record['thread_id'] = str(uuid.uuid4())
        with self.assertRaisesRegex(ValueError, 'another worker'):
            receipt.observe_response(record, None, 'gpt-5.6-luna')

    def test_missing_or_mismatched_usage_remains_incomplete(self):
        first, second = self.receipt(), self.receipt()
        self.assertNotEqual(first.path, second.path)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(execute_with_usage([sys.executable, '-c', 'raise SystemExit(2)'], self.root,
                                                os.environ.copy(), first), 2)
        self.assertEqual(first.data['status'], 'incomplete')
        self.assertIsNone(first.data['standard_api_equivalent_usd'])
        self.record_response(second)
        second.observe({'type': 'turn.completed', 'usage': dict(self.usage, input_tokens=1100, total_tokens=1200)})
        second.finish(0)
        self.assertFalse(second.data['usage_reconciled'])
        self.assertEqual(second.data['status'], 'incomplete')

    def test_interrupted_attempt_retains_completed_responses_without_claiming_full_cost(self):
        receipt = self.receipt()
        self.record_response(receipt)
        receipt.finish(130)
        self.assertEqual(receipt.data['standard_api_equivalent_usd']['minimum'], 0.000176)
        self.assertEqual(receipt.data['status'], 'incomplete')
        self.assertIsNone(report(self.results(), [receipt.path], self.root)['combined_standard_equivalent_usd'])

    def test_partial_final_usage_cannot_pass_reconciliation(self):
        receipt = self.receipt()
        self.record_response(receipt)
        receipt.observe({'type': 'turn.completed', 'usage': {'input_tokens': 1000}})
        receipt.finish(0)
        self.assertFalse(receipt.data['usage_reconciled'])
        self.assertEqual(receipt.data['status'], 'incomplete')

    def test_rerouted_model_remains_incomplete(self):
        receipt = self.receipt()
        path = self.journal_path()
        path.write_text(json.dumps({'type': 'event_msg', 'payload': {'type': 'model_rerouted'}}) + '\n')
        with self.assertRaisesRegex(ValueError, 'rerouted'):
            UsageJournal(self.root / 'profile', receipt).poll()

    def test_report_cli_saves_the_same_report_it_prints(self):
        import subprocess
        self.completed()
        results = self.root / 'results.json'
        results.write_text(json.dumps(self.results()))
        executed = subprocess.run([sys.executable, str(Path(__file__).with_name('run_costs.py')), str(results)],
                                  capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(executed.stdout), json.loads((self.root / 'run-costs.json').read_text()))

    def test_telemetry_error_does_not_interrupt_worker(self):
        receipt = self.receipt()
        event = {'type': 'turn.completed', 'usage': self.usage}
        marker = self.root / 'worker-finished'
        program = ('import json; from pathlib import Path; event=' + repr(event)
                   + '; print(json.dumps(event)); print(json.dumps(event)); Path(' + repr(str(marker)) + ').touch()')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(execute_with_usage([sys.executable, '-c', program], self.root,
                                                os.environ.copy(), receipt), 2)
        self.assertTrue(marker.exists())
        self.assertEqual(receipt.data['exit_code'], 0)
        self.assertEqual(receipt.data['status'], 'incomplete')

    def test_run_total_automatically_includes_every_attempt_and_excludes_outer_chat(self):
        missing = report(self.results(), [])
        self.assertEqual(missing['status'], 'incomplete')
        self.assertIsNone(missing['combined_standard_equivalent_usd'])
        receipt = self.completed()
        (self.root / 'results.json').write_text(json.dumps(self.results()))
        saved = json.loads(save_report(self.root).read_text())
        self.assertEqual(saved['scope'], 'tyche_run_only')
        self.assertNotIn('monitoring', saved)
        self.assertEqual(saved['status'], 'calculated')
        self.assertEqual(saved['combined_standard_equivalent_usd'], {'minimum': 0.831176, 'maximum': 0.831176})
        self.assertEqual(saved['cost_per_accepted_lead_standard_equivalent_usd']['minimum'], 0.1662352)
        ranged = report(self.results(0.845), [receipt.path], self.root)
        self.assertEqual(ranged['status'], 'estimated_range')
        self.assertEqual(ranged['combined_standard_equivalent_usd']['maximum'], 0.845176)
        self.receipt().finish(1)
        self.assertEqual(json.loads(save_report(self.root).read_text())['status'], 'incomplete')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            report(self.results(), [receipt.path, receipt.path], self.root)
        with self.assertRaisesRegex(ValueError, 'different run'):
            report(self.results(), [receipt.path], self.root / 'another-run')


if __name__ == '__main__':
    unittest.main()
