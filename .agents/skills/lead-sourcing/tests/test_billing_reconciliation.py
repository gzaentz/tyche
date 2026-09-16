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

    def prospector(self, count):
        """Minimal, anonymized shape from the 2026-09-16 live billing probe."""
        self.receipt.update(tool='prospector', status='ok', results=[{'persons': [
            {'id': f'contact-{i}', 'email_verified': True} for i in range(count)]}])
        self.receipt_path.write_text(json.dumps(self.receipt))
        catalog = {'toolId': 'prospector', 'provider': 'deepline_native', 'operation': 'prospector',
                   'operationId': 'deepline_native_prospector',
                   'operationAliases': ['prospector', 'deepline_native_prospector']}
        descriptor = {'provider': 'deepline', 'operation': 'describe', 'status': 'ok', 'tool': 'prospector',
                      'run_fingerprint': budget.run_fingerprint(self.path), 'request_fingerprint': 'catalog',
                      'results': [catalog]}
        (self.path.parent / 'receipts/catalog.json').write_text(json.dumps(descriptor))
        doc = budget.read_object(self.path)
        doc['routes'][0]['tool'] = 'prospector'
        doc['routes'].insert(0, {'route_id': 'catalog', 'provider': 'deepline', 'operation': 'describe',
                               'tool': 'prospector', 'provider_status': 'ok', 'request_fingerprint': 'catalog',
                               'paid_calls': 0, 'cost_credits': 0, 'cost_upper_bound_credits': 0, 'cost_basis': 'actual'})
        self.path.write_text(json.dumps(doc))
        self.row.update(provider='deepline_native', operation='prospector', charge_state='free',
                        credits=0, delta=0, outcome='miss', provider_units=0, metadata=None, pricing_basis='result')
        return catalog

    def test_catalog_alias_matches_both_spellings_without_provider_prefix_guess(self):
        catalog = self.prospector(0)
        for operation in ('prospector', 'deepline_native_prospector'):
            with self.subTest(operation=operation):
                row = dict(self.row, operation=operation)
                self.assertIsNotNone(billing.matching_charge(self.receipt, [row], catalog))
                self.assertIsNone(billing.matching_charge(self.receipt, [dict(row, provider='wrong')], catalog))
                self.assertIsNone(billing.matching_charge(self.receipt, [dict(row, request_id='other')], catalog))
        self.assertIsNone(billing.matching_charge(self.receipt, [self.row]))
        self.assertIsNone(billing.matching_charge(self.receipt, [self.row], dict(catalog, toolId='other',
                         operation='other', operationId='other', operationAliases=[])))

    def test_explicit_free_empty_result_settles_zero_with_null_metadata(self):
        self.prospector(0)
        result = billing.reconcile(self.path, fetch=lambda: {'recent': {'entries': [self.row]}})
        self.assertEqual(result['unmatched'], [])
        ledger = budget.load_ledger(self.path)
        self.assertEqual(ledger['calls']['call-1']['actual_credits'], '0')
        self.assertEqual(budget.audit_ledger(self.path, budget.read_object(self.path)), [])
        costs = budget.accounting_summary(ledger)['providers']['deepline']
        self.assertEqual(costs, dict(billed_usd=0, unresolved_reserved_usd=0, maximum_usd=0, unresolved_calls=0))
        budget.check_allowance(ledger, 'deepline', '49.5', 0)

    def test_zero_billing_with_returned_contacts_is_saved_without_releasing_reservation(self):
        for count in (1, 2):
            self.prospector(count)
            billing.reconcile(self.path, refresh=True, fetch=lambda: {'recent': {'entries': [self.row]}})
            ledger = budget.load_ledger(self.path)
            call = ledger['calls']['call-1']
            self.assertIsNone(call['actual_credits'])
            self.assertEqual(call['billing_evidence']['credits'], 0)
            self.assertIn('Results returned', call['billing_issue'])
            self.assertEqual(budget.audit_ledger(self.path, budget.read_object(self.path)), [])
            summary = budget.accounting_summary(ledger)
            self.assertEqual(summary['providers']['deepline'], dict(billed_usd=0, unresolved_reserved_usd=.1,
                             maximum_usd=.1, unresolved_calls=1))
            self.assertEqual(len(summary['billing_issues']), 1)
            with self.assertRaises(budget.BudgetError):
                budget.check_allowance(ledger, 'deepline', '49.5', 0)

    def test_later_posted_correction_settles_once_and_preserves_billing_history(self):
        self.prospector(1)
        billing.reconcile(self.path, fetch=lambda: {'recent': {'entries': [self.row]}})
        posted = dict(self.row, id='ledger-corrected', credits=.5, delta=-.5, charge_state='posted', outcome='hit', provider_units=1)
        billing.reconcile(self.path, refresh=True, fetch=lambda: {'recent': {'entries': [posted]}})
        call = budget.load_ledger(self.path)['calls']['call-1']
        self.assertEqual(call['actual_credits'], '0.5')
        self.assertIsNone(call['billing_issue'])
        self.assertEqual(call['billing_history'][0]['credits'], 0)
        self.assertEqual(budget.audit_ledger(self.path, budget.read_object(self.path)), [])
        billing.reconcile(self.path, refresh=True, fetch=lambda: self.fail('Already settled'))

    def test_missing_or_invalid_free_charge_never_becomes_zero(self):
        catalog = self.prospector(0)
        for change in ({'credits': None}, {'credits': .1, 'delta': -.1}, {'charge_state': 'pending'},
                       {'metadata': 'invalid'}, {'metadata': {'chargeGroupIds': ['other']}},
                       {'metadata': {'chargeGroupIds': ['request-1', 'request-2']}}):
            with self.subTest(change=change):
                self.assertIsNone(billing.matching_charge(self.receipt, [dict(self.row, **change)], catalog))
        self.assertIsNotNone(billing.matching_charge(self.receipt,
            [dict(self.row, metadata={'chargeGroupIds': ['request-1']})], catalog))

    def test_free_success_is_not_assumed_to_be_a_billing_error(self):
        self.prospector(1)
        row = dict(self.row, outcome='free_operation', provider_units=None, pricing_basis='attempt')
        billing.reconcile(self.path, fetch=lambda: {'recent': {'entries': [row]}})
        self.assertEqual(budget.load_ledger(self.path)['calls']['call-1']['actual_credits'], '0')

    def test_timeout_retries_only_the_billing_read_then_settles(self):
        with patch.object(deepline, '_invoke', side_effect=[deepline.CallTimeout('timeout', '', ''),
                (0, json.dumps({'recent': {'entries': [self.row]}}), '')]) as invoke:
            result = billing.reconcile(self.path)
        self.assertEqual(result['attempts'], 2)
        self.assertNotIn('error', result)
        self.assertEqual(result['matched'], ['call-1'])
        for call in invoke.call_args_list:
            self.assertEqual(call.args[0][1:3], ['billing', 'usage'])
            self.assertEqual(call.args[1], 30)

    def test_retry_budget_persists_and_refresh_does_not_reset_it(self):
        with patch.object(deepline, '_invoke', side_effect=deepline.CallTimeout('timeout', '', '')) as invoke:
            first = billing.reconcile(self.path)
            self.assertEqual(first['attempts'], 2)
            billing.reconcile(self.path)  # Cooldown applies even across a new caller.
            self.assertEqual(invoke.call_count, 2)
            final = billing.reconcile(self.path, refresh=True)
            self.assertEqual(final['attempts'], 3)
            billing.reconcile(self.path, refresh=True)
            self.assertEqual(invoke.call_count, 3)
        self.assertIsNone(budget.load_ledger(self.path)['calls']['call-1']['actual_credits'])

    def test_pending_billing_retries_after_cooldown_without_manual_refresh(self):
        with patch.object(billing.time, 'time', return_value=100):
            billing.reconcile(self.path, fetch=lambda: {'recent': {'entries': []}})
        with patch.object(billing.time, 'time', return_value=159):
            billing.reconcile(self.path, fetch=lambda: self.fail('Cooldown'))
        with patch.object(billing.time, 'time', return_value=160):
            result = billing.reconcile(self.path, fetch=lambda: {'recent': {'entries': [self.row]}})
        self.assertEqual(result['matched'], ['call-1'])

    def test_legacy_failed_cache_does_not_permanently_suppress_retry(self):
        import hashlib
        signature = hashlib.sha256(json.dumps(['call-1']).encode()).hexdigest()
        (self.path.parent / 'billing-status.json').write_text(json.dumps(
            {'attempt_signature': signature, 'error': 'provider command timed out'}))
        result = billing.reconcile(self.path, fetch=lambda: {'recent': {'entries': [self.row]}})
        self.assertEqual(result['matched'], ['call-1'])

    def test_catalog_and_discrepancy_tampering_fail_existing_audit(self):
        self.prospector(1)
        billing.reconcile(self.path, fetch=lambda: {'recent': {'entries': [self.row]}})
        with budget.transaction(budget.ledger_path(self.path)) as ledger:
            ledger['calls']['call-1']['billing_issue'] = None
        self.assertTrue(budget.audit_ledger(self.path, budget.read_object(self.path)))

    def test_saved_report_shows_billed_and_reserved_without_changing_research(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[4] / 'scripts'))
        from run_costs import save_report
        self.prospector(1)
        before = self.receipt_path.read_bytes()
        billing.reconcile(self.path, fetch=lambda: {'recent': {'entries': [self.row]}})
        (self.path.parent / 'research-commentary.md').write_text('Fixture research remains unchanged.')
        report_path = save_report(self.path.parent, self.path)
        report = json.loads(report_path.read_text())
        self.assertEqual(report['provider_accounting']['providers']['deepline']['billed_usd'], 0)
        self.assertEqual(report['provider_accounting']['providers']['deepline']['unresolved_reserved_usd'], .1)
        self.assertEqual(len(report['provider_accounting']['billing_issues']), 1)
        text = (self.path.parent / 'report.md').read_text()
        self.assertIn('$0.0000 billed; $0.1000 unresolved reservations', text)
        self.assertIn('Fixture research remains unchanged.', text)
        self.assertEqual(before, self.receipt_path.read_bytes())

    def test_finish_retries_billing_at_final_approval_after_initial_read_timeouts(self):
        from research_tools import ResearchTools
        tools = ResearchTools(self.path)
        with patch.object(ResearchTools, '_overview', return_value={'stop': 'continue'}), patch.object(
                deepline, '_invoke', side_effect=[deepline.CallTimeout('timeout', '', ''),
                    deepline.CallTimeout('timeout', '', ''), (0, json.dumps({'recent': {'entries': [self.row]}}), '')]) as invoke:
            first = tools.finish()
            self.assertEqual(first['status'], 'needs_research')
            self.assertIsNone(budget.load_ledger(self.path)['calls']['call-1']['actual_credits'])
            final = tools.finish(review_ref='final-approval-attempt')
            self.assertEqual(final['status'], 'needs_research')
            self.assertEqual(budget.load_ledger(self.path)['calls']['call-1']['actual_credits'], '0.5')
            self.assertEqual(invoke.call_count, 3)

    def test_invalid_billing_payload_stays_reserved_and_read_retries_are_bounded(self):
        calls = []
        def fetch():
            calls.append(1)
            return []
        result = billing.reconcile(self.path, fetch=fetch)
        self.assertIn('error', result)
        self.assertEqual(len(calls), 2)
        self.assertIsNone(budget.load_ledger(self.path)['calls']['call-1']['actual_credits'])

    def test_billed_plus_unresolved_reservations_are_not_double_counted(self):
        billing.reconcile(self.path, fetch=lambda: {'recent': {'entries': [self.row]}})
        budget.reserve({'run_file': str(self.path), 'route_id': 'call-2', 'max_cost_credits': 1}, 'deepline')
        summary = budget.accounting_summary(budget.load_ledger(self.path))
        self.assertEqual(summary['providers']['deepline'], dict(billed_usd=.05, unresolved_reserved_usd=.1,
                         maximum_usd=.15, unresolved_calls=1))
        self.assertEqual(summary['billing_issues'], [])

    def test_alias_proof_keeps_its_run_bound_catalog_reference(self):
        self.prospector(0)
        billing.reconcile(self.path, fetch=lambda: {'recent': {'entries': [self.row]}})
        catalog_path = self.path.parent / 'receipts/catalog.json'
        catalog = json.loads(catalog_path.read_text())
        catalog['run_fingerprint'] = 'another-run'
        catalog_path.write_text(json.dumps(catalog))
        self.assertTrue(budget.audit_ledger(self.path, budget.read_object(self.path)))
