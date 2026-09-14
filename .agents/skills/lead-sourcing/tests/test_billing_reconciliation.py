"""Read-only billing settlement never guesses costs or redispatches research."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import billing_reconciliation as billing
import budget_guard as budget
import deepline


class BillingReconciliationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'results.json'
        doc = {'request': {'target_count': 10, 'contact_fields': []}, 'accepted': [], 'routes': [],
               'budget': {'paid_calls': 0, 'limits': {'deepline_credits': 50, 'scrapingdog_credits': 0}}}
        self.path.write_text(json.dumps(doc))
        budget.initialize(self.path)
        budget.reserve({'run_file': str(self.path), 'route_id': 'call-1', 'max_cost_credits': 1}, 'deepline')
        self.receipt = {'run_fingerprint': budget.run_fingerprint(self.path), 'request_fingerprint': 'fingerprint',
                        'tool': 'fixture_email_finder', 'provider': 'deepline', 'job_id': 'request-1', 'status': 'no_results',
                        'attempt': {'action': {'id': 'call-1', 'cost_upper_bound_credits': 1}},
                        'spend_receipt': {'route_id': 'call-1', 'ledger': str(budget.ledger_path(self.path)), 'state': 'reserved'}}
        (self.path.parent / 'receipts').mkdir()
        self.receipt_path = self.path.parent / 'receipts/call-1.json'
        self.receipt_path.write_text(json.dumps(self.receipt))
        doc = budget.read_object(self.path)
        doc['routes'] = [{'route_id': 'call-1', 'provider': 'deepline', 'tool': 'fixture_email_finder', 'paid_calls': 1,
                          'request_fingerprint': 'fingerprint', 'accepted_leads_before_call': 0,
                          'cost_credits': None, 'cost_upper_bound_credits': 1, 'cost_basis': 'estimated'}]
        self.path.write_text(json.dumps(doc))
        self.row = {'id': 'ledger-1', 'request_id': 'request-1', 'operation': 'fixture_email_finder',
                    'provider': 'fixture', 'status': 'completed', 'charge_state': 'posted', 'credits': .5, 'delta': -.5}

    def test_unique_posted_charge_settles_once_preserving_original_receipt_and_caps(self):
        before = self.receipt_path.read_bytes()
        ledger = budget.load_ledger(self.path)
        result = billing.reconcile(self.path, fetch=lambda: {'recent': {'entries': [self.row]}})
        self.assertEqual(result['matched'], ['call-1'])
        after = budget.load_ledger(self.path)
        self.assertEqual(after['calls']['call-1']['actual_credits'], '0.5')
        self.assertEqual(after['calls']['call-1']['maximum_credits'], '1')
        self.assertEqual({k: v for k, v in ledger.items() if k != 'calls'}, {k: v for k, v in after.items() if k != 'calls'})
        self.assertEqual(before, self.receipt_path.read_bytes())
        self.assertEqual(budget.audit_ledger(self.path, budget.read_object(self.path)), [])
        billing.reconcile(self.path, fetch=lambda: self.fail('Do not repeat an unchanged billing read'))

    def test_ambiguous_unmatched_pending_and_missing_id_remain_reserved(self):
        for rows in ([self.row, self.row], [dict(self.row, request_id='other')], [dict(self.row, operation='other')],
                     [dict(self.row, provider='unrelated')],
                     [dict(self.row, metadata={'chargeGroupIds': ['one', 'two']})],
                     [dict(self.row, charge_state='held')], [dict(self.row, delta=1)], [dict(self.row, credits=True)]):
            with self.subTest(rows=rows):
                self.assertIsNone(billing.matching_charge(self.receipt, rows))
        self.assertIsNone(billing.matching_charge({'tool': 'fixture_email_finder'}, [self.row]))
        result = billing.reconcile(self.path, fetch=lambda: {'recent': {'entries': [dict(self.row, request_id='other')]}})
        self.assertEqual(result['unmatched'], ['call-1'])
        self.assertIsNone(budget.load_ledger(self.path)['calls']['call-1']['actual_credits'])
        billing.reconcile(self.path, fetch=lambda: self.fail('Unchanged unresolved calls do not trigger repeated reads'))
        # A later invocation gets one fresh bounded read for late posting.
        result = billing.reconcile(self.path, refresh=True, fetch=lambda: {'recent': {'entries': [self.row]}})
        self.assertEqual(result['matched'], ['call-1'])

    def test_overrun_is_recorded_and_blocks_instead_of_increasing_caps(self):
        billing.reconcile(self.path, fetch=lambda: {'recent': {'entries': [dict(self.row, credits=2, delta=-2)]}})
        ledger = budget.load_ledger(self.path)
        self.assertEqual(ledger['blocked'], budget.PRICE_OVERRUN)
        self.assertEqual(ledger['calls']['call-1']['actual_credits'], '2')
        self.assertEqual(ledger['calls']['call-1']['maximum_credits'], '1')
        self.assertTrue(budget.audit_ledger(self.path, budget.read_object(self.path)))
        result = budget.reconcile_overruns(self.path, [self.receipt_path], pricing_note='Fixture pricing policy corrected; original cap unchanged')
        self.assertIsNone(result['blocked'])
        self.assertEqual(budget.audit_ledger(self.path, budget.read_object(self.path)), [])

    def test_one_billing_entry_cannot_settle_two_different_routes(self):
        budget.reserve({'run_file': str(self.path), 'route_id': 'call-2', 'max_cost_credits': 1}, 'deepline')
        second = copy.deepcopy(self.receipt)
        second['request_fingerprint'] = 'another-fingerprint'
        (self.path.parent / 'receipts/call-2.json').write_text(json.dumps(second))
        doc = budget.read_object(self.path)
        doc['routes'].append(dict(doc['routes'][0], route_id='call-2', request_fingerprint='another-fingerprint'))
        self.path.write_text(json.dumps(doc))
        result = billing.reconcile(self.path, fetch=lambda: {'recent': {'entries': [self.row]}})
        self.assertEqual(result['matched'], [])
        self.assertEqual(len(result['unmatched']), 2)

    def test_outage_retains_reservation_without_blocking_research(self):
        with patch.object(deepline, '_invoke', side_effect=deepline.CallTimeout('billing timeout', '', '')):
            result = billing.reconcile(self.path)
        self.assertIn('error', result)
        self.assertIsNone(budget.load_ledger(self.path)['calls']['call-1']['actual_credits'])

    def test_changed_billing_proof_fails_audit(self):
        billing.reconcile(self.path, fetch=lambda: {'recent': {'entries': [self.row]}})
        with budget.transaction(budget.ledger_path(self.path)) as ledger:
            ledger['calls']['call-1']['billing_evidence']['request_id'] = 'unrelated'
        self.assertTrue(any('posted billing evidence' in e for e in budget.audit_ledger(self.path, budget.read_object(self.path))))
