import unittest
from unittest.mock import patch
import json
import deepline


class BillingIdentityTests(unittest.TestCase):
    def test_error_response_retains_provider_supplied_ids_and_zero_charge(self):
        raw={'ok':False,'job_id':'job-123','error':{'message':'Upstream unavailable','request_id':'request-456'},
             'billing':{'credits_charged':0,'cost_usd':0}}
        request={'operation':'execute','tool':'example_lookup','entity_type':'company','limit':1}
        with patch.object(deepline,'_invoke',return_value=(1,json.dumps(raw),'')):
            result,code=deepline._run_command(request,['synthetic'],10)
        self.assertEqual(code,0)  # Provider failures are normalized response statuses.
        self.assertEqual(result['status'],'provider_error')
        self.assertEqual(result['job_id'],'job-123')
        self.assertEqual(result['request_id'],'request-456')
        self.assertEqual(result['billing']['credits_charged'],0)

    def test_missing_identity_or_billing_is_not_invented(self):
        result=deepline._execution_metadata({'ok':False,'error':{'message':'No result'}})
        self.assertNotIn('job_id',result)
        self.assertNotIn('request_id',result)
        self.assertNotIn('billing',result)

    def test_timeout_retains_ids_from_partial_provider_response(self):
        raw = {'job_id': 'pending-job', 'request_id': 'pending-request',
               'billing': {'credits_charged': 0.1}}
        request = {'operation': 'execute', 'tool': 'example_lookup', 'entity_type': 'company'}
        with patch.object(deepline, '_invoke', side_effect=deepline.CallTimeout('timeout', json.dumps(raw))):
            result, _ = deepline._run_command(request, ['synthetic'], 10)
        self.assertEqual(result['status'], 'timeout')
        self.assertEqual(result['job_id'], 'pending-job')
        self.assertEqual(result['request_id'], 'pending-request')
        self.assertEqual(result['billing'], raw['billing'])

    def test_known_no_results_response_stays_no_results_with_billing_ids(self):
        raw = {'ok': False, 'job_id': 'no-match-job', 'request_id': 'no-match-request',
               'error': {'message': 'not_found: The domain does not exist in our database',
                         'code': 'UPSTREAM_NOT_FOUND', 'details': {'statusCode': 404}}}
        request = {'operation': 'execute', 'tool': 'hunter_companies_find', 'entity_type': 'company'}
        with patch.object(deepline, '_invoke', return_value=(1, json.dumps(raw), '')):
            result, _ = deepline._run_command(request, ['synthetic'], 10)
        self.assertEqual(result['status'], 'no_results')
        self.assertEqual(result['job_id'], 'no-match-job')
        self.assertEqual(result['request_id'], 'no-match-request')


if __name__=='__main__':unittest.main()
