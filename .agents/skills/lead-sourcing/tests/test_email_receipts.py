import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_attempt_execution as attempt_tests
from test_email_fallback import with_fallback
from test_output_contract import accepted_email_result
from email_fixtures import write_email_receipts
import email_receipts as receipts
import run_attempt


class EmailReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'results.json'
        self.doc = accepted_email_result()
        self.path.write_text(json.dumps(self.doc))
        write_email_receipts(self.path,self.doc)
        self.contact=self.doc['accepted'][0]['primary_contact']
        self.validation=self.contact['email_validation']
        self.receipt_path=self.path.parent/'receipts'/(self.validation['source']['route_id']+'.json')

    def test_missing_verdict_is_filled_but_conflicting_verdict_is_rejected(self):
        self.validation.pop('status')
        self.assertEqual(receipts.email_receipt_errors(self.doc,self.path,fill_missing=True),[])
        self.assertEqual(self.validation['status'],'valid')
        self.validation['status']='catch-all'
        self.assertIn('saved provider verdict',str(receipts.email_receipt_errors(self.doc,self.path,fill_missing=True)))
        self.assertEqual(self.validation['status'],'catch-all')

    def test_domain_flag_and_edited_normalization_do_not_change_raw_verdict(self):
        saved=json.loads(self.receipt_path.read_text())
        saved['provider_response']['body']['element']['catchall_domain']=True
        saved['results']=[{'email':self.contact['email'],'status':'catch-all'}]
        self.receipt_path.write_text(json.dumps(saved))
        self.assertEqual(receipts.email_receipt_errors(self.doc,self.path),[])
        source=self.validation['source'];source['tool']='zerobounce_validate'
        self.doc['routes'][0]['tool']='zerobounce_validate';saved['tool']='zerobounce_validate'
        self.receipt_path.write_text(json.dumps(saved))
        with self.assertRaisesRegex(ValueError,'valid and hard-negative'):
            receipts.check_fallback(self.path,self.doc,{'operation':'execute','tool':'bounceban_verify_single','payload':{'email':self.contact['email']}})

    def test_cross_run_wrong_address_and_request_mismatch_are_refused(self):
        original=json.loads(self.receipt_path.read_text())
        for mutate in (lambda s:s.update(run_fingerprint='another-run'),
                       lambda s:s.update(request_fingerprint='another-request'),
                       lambda s:s['provider_response']['body']['element'].update(email='other@example.com')):
            saved=copy.deepcopy(original);mutate(saved);self.receipt_path.write_text(json.dumps(saved))
            self.assertTrue(receipts.email_receipt_errors(self.doc,self.path))

    def test_single_deliverable_fallback_uses_both_original_receipts(self):
        with_fallback(self.doc);write_email_receipts(self.path,self.doc)
        self.assertEqual(receipts.email_receipt_errors(self.doc,self.path),[])
        self.validation['fallback']['result']='undeliverable'
        self.assertTrue(receipts.email_receipt_errors(self.doc,self.path))

    def test_preflight_allows_only_eligible_original_verdicts(self):
        source = self.validation['source']
        source['tool'] = self.doc['routes'][0]['tool'] = 'zerobounce_validate'
        request = {'operation': 'execute', 'tool': 'bounceban_verify_single',
                   'payload': {'email': self.contact['email']}}
        for status in ('catch-all', 'unknown', 'valid', 'invalid', 'do_not_mail', 'spamtrap', 'abuse'):
            with self.subTest(status=status):
                self.validation['status'] = status
                write_email_receipts(self.path, self.doc)
                if status in ('catch-all', 'unknown'):
                    receipts.check_fallback(self.path, self.doc, request)
                else:
                    with self.assertRaisesRegex(ValueError, 'cannot use fallback'):
                        receipts.check_fallback(self.path, self.doc, request)
        for failure in receipts.FAILURES:
            with self.subTest(failure=failure):
                self.validation.update(status=None, provider_status=failure)
                self.doc['routes'][0]['provider_status'] = failure
                write_email_receipts(self.path, self.doc)
                receipts.check_fallback(self.path, self.doc, request)

    def prepared_run(self, response=None, *, exit_code=0, timeout=False):
        fixture = attempt_tests.AttemptExecutionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        document = json.loads(fixture.path.read_text())
        document['unresolved'] = [{'stage': 'contact', 'candidate': {'domain': 'target.example'},
            'account_fit': {'evidence_url': 'https://target.example/product',
                            'evidence_text': 'Verified platform fit.'}}]
        fixture.path.write_text(json.dumps(document))
        spec = fixture.spec('zerobounce-original', paid=True)
        spec['action'].update(phase='email_validation', scope='target.example', approach='zerobounce-validation')
        spec['request'].update(tool='zerobounce_validate', payload={'email': 'buyer@target.example'})
        raw = response if response is not None else {'address': 'buyer@target.example', 'status': 'unknown'}
        wire = {'side_effect': receipts.deepline.CallTimeout('timeout')} if timeout else {
            'return_value': (exit_code, json.dumps(raw), '')}
        # Exercise the real wrapper, receipt capture, budget and route preparation.
        with patch.object(receipts.deepline, '_invoke', **wire) as provider:
            run_attempt.run_attempt(fixture.path, spec)
            provider.assert_called_once()
        fallback = copy.deepcopy(spec)
        fallback['action'].update(id='bounceban-first', approach='bounceban-validation')
        fallback['request'].update(tool='bounceban_verify_single',
                                   payload={'email': 'buyer@target.example', 'mode': 'auto'})
        return fixture, fallback

    def test_actual_pending_fallback_cannot_be_repeated_with_a_different_mode(self):
        fixture, spec = self.prepared_run()
        run_attempt._start_attempt(fixture.path, spec)
        pending = json.loads(fixture.path.read_text())['stop_audit']['route_frontier'][-1]
        self.assertNotIn('tool', pending)
        spec['action']['id'] = 'bounceban-repeated'
        spec['request']['payload']['mode'] = 'realtime'
        before = fixture.path.read_bytes()
        with patch.object(receipts.deepline, '_invoke') as provider, \
             patch.object(run_attempt.budget_guard, 'reserve') as reserve:
            with self.assertRaisesRegex(ValueError, 'already attempted'):
                run_attempt.run_attempt(fixture.path, spec)
            provider.assert_not_called()
            reserve.assert_not_called()
        self.assertEqual(fixture.path.read_bytes(), before)

    def test_raw_schema_error_cannot_be_relabelled_as_eligible_failure(self):
        raw = {'ok': False, 'status': 'schema_error',
               'error': {'message': 'Invalid schema: email is required'}}
        fixture, spec = self.prepared_run(raw, exit_code=1)
        document = json.loads(fixture.path.read_text())
        document['routes'][0]['provider_status'] = 'provider_error'
        fixture.path.write_text(json.dumps(document))
        path = fixture.path.parent / 'receipts/zerobounce-original.json'
        saved = json.loads(path.read_text())
        saved['status'] = 'provider_error'
        path.write_text(json.dumps(saved))
        with patch.object(receipts.deepline, '_invoke') as provider, \
             patch.object(run_attempt.budget_guard, 'reserve') as reserve:
            with self.assertRaisesRegex(ValueError, 'original provider response'):
                run_attempt.run_attempt(fixture.path, spec)
            provider.assert_not_called()
            reserve.assert_not_called()

    def test_empty_transport_timeout_is_saved_and_allows_fallback(self):
        fixture, spec = self.prepared_run(timeout=True)
        path = fixture.path.parent / 'receipts/zerobounce-original.json'
        saved = json.loads(path.read_text())
        self.assertTrue(saved.get('provider_response', {}).get('timed_out'))
        run_attempt._start_attempt(fixture.path, spec)

    def test_actual_service_failure_allows_fallback(self):
        raw = {'ok': False, 'status': 'rate_limited', 'error': {'message': 'Too many requests'}}
        fixture, spec = self.prepared_run(raw, exit_code=1)
        run_attempt._start_attempt(fixture.path, spec)

    def test_failure_envelope_cannot_override_an_explicit_hard_negative(self):
        raw = {'status': 'provider_error', 'error': {'message': 'Upstream unavailable'},
               'results': [{'address': 'buyer@target.example', 'status': 'invalid'}]}
        fixture, spec = self.prepared_run(raw, exit_code=1)
        with patch.object(receipts.deepline, '_invoke') as provider:
            with self.assertRaisesRegex(ValueError, 'cannot use fallback'):
                run_attempt.run_attempt(fixture.path, spec)
            provider.assert_not_called()

    def test_pending_fallback_does_not_block_another_email(self):
        fixture, spec = self.prepared_run()
        run_attempt._start_attempt(fixture.path, spec)
        second = copy.deepcopy(spec)
        second['action'].update(id='zerobounce-second', approach='zerobounce-validation')
        second['request'].update(tool='zerobounce_validate', payload={'email': 'other@target.example'})
        with patch.object(receipts.deepline, '_invoke', return_value=(
                0, json.dumps({'address': 'other@target.example', 'status': 'unknown'}), '')):
            run_attempt.run_attempt(fixture.path, second)
        second['action'].update(id='bounceban-other-email', approach='bounceban-validation')
        second['request']['tool'] = 'bounceban_verify_single'
        run_attempt._start_attempt(fixture.path, second)

    def test_free_status_getter_recovers_pending_job_without_resubmitting(self):
        fixture, spec = self.prepared_run()
        with patch.object(receipts.deepline, '_invoke', return_value=(0, json.dumps({
                'status': 'verifying', 'id': 'saved-job', 'email': 'buyer@target.example'}), '')):
            result = run_attempt.run_attempt(fixture.path, spec)
        self.assertEqual(result['provider_status'], 'partial')
        original = fixture.path.parent / 'receipts/bounceban-first.json'
        before = original.read_bytes()
        getter = copy.deepcopy(spec)
        getter['action'].update(id='bounceban-getter', status_read=True, cost_upper_bound_credits=0)
        getter['request'].update(tool='bounceban_get_verification', payload={'id': 'saved-job'})
        with patch.object(receipts.deepline, '_invoke', return_value=(0, json.dumps({
                'status': 'success', 'result': 'deliverable', 'email': 'buyer@target.example'}), '')) as provider:
            run_attempt.run_attempt(fixture.path, getter)
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(provider.call_args.args[0][3], 'bounceban_get_verification')
        self.assertEqual(original.read_bytes(), before)
        document = json.loads(fixture.path.read_text())
        source = {'provider': 'deepline', 'operation': 'execute', 'tool': 'bounceban_get_verification',
                  'validator': 'bounceban', 'route_id': 'bounceban-getter'}
        verdict = receipts.saved_result(fixture.path, document['routes'], source, 'buyer@target.example')
        self.assertEqual((verdict['status'], verdict['result']), ('success', 'deliverable'))

    def test_rejected_dispatch_does_not_reserve_or_call_provider(self):
        fixture=attempt_tests.AttemptExecutionTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        spec=fixture.spec('bad-fallback',paid=True)
        spec['request'].update(tool='bounceban_verify_single',payload={'email':'nobody@example.com'})
        with patch.object(run_attempt,'check_fallback',side_effect=ValueError('no eligible same-email receipt')) as guard:
            with patch.object(run_attempt.budget_guard,'reserve') as reserve:
                with self.assertRaisesRegex(ValueError,'no eligible'):
                    run_attempt.run_attempt(fixture.path,spec,execute=lambda *_:self.fail('Provider called'))
                guard.assert_called_once();reserve.assert_not_called()


if __name__=='__main__':unittest.main()
