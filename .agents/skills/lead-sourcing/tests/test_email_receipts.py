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

    def test_pending_fallback_cannot_be_repeated_with_a_different_mode(self):
        with_fallback(self.doc)
        self.validation['source']['tool'] = self.doc['routes'][0]['tool'] = 'zerobounce_validate'
        self.validation['fallback']['source']['tool'] = self.doc['routes'][-1]['tool'] = 'bounceban_verify_single'
        write_email_receipts(self.path, self.doc)
        pending = self.doc['routes'].pop()
        self.doc.setdefault('stop_audit', {})['route_frontier'] = [dict(pending, state='continuable')]
        request = {'operation': 'execute', 'tool': 'bounceban_verify_single',
                   'payload': {'email': self.contact['email'], 'mode': 'different-mode'}}
        with self.assertRaisesRegex(ValueError, 'already attempted'):
            receipts.check_fallback(self.path, self.doc, request)

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
