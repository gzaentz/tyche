import os
import contextlib
import io
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from codex_tyche import smoke, tool_configuration, workspace_environment, close_worker


class WorkspaceRuntimeTests(unittest.TestCase):
    def test_worker_limit_saves_resumable_status_without_relaunch_or_export(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = root / 'request.txt'
            request.write_text('fixture')
            run = root / 'results.json'
            run.write_text(json.dumps({'request': {'target_count': 10}, 'accepted': [{}] * 4}))
            before = run.read_bytes()
            receipt = SimpleNamespace(data={'exit_code': 1, 'failure_kind': 'model_usage_limit'})
            with patch('codex_tyche.subprocess.Popen', side_effect=AssertionError('Do not relaunch a model')):
                status = json.loads(close_worker(request, receipt).read_text())
            self.assertFalse(status['delivery_allowed'])
            self.assertEqual(status['reason'], 'model_usage_limit')
            self.assertEqual(status['accepted_count'], 4)
            self.assertEqual(run.read_bytes(), before)

    def test_worker_recovers_only_an_explicit_current_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = root / 'request.txt'; request.write_text('fixture')
            run = root / 'results.json'
            document = {'request': {'target_count': 1}, 'accepted': [{}], 'unresolved': [], 'rejected': []}
            reviewed = hashlib.sha256(json.dumps(dict(document, source_reviews=[]), sort_keys=True).encode()).hexdigest()
            document['final_review'] = {'review_ref': reviewed}
            run.write_text(json.dumps(document))
            receipt = SimpleNamespace(data={'exit_code': 1})
            # Import the tool class via close_worker before patching; an
            # unreviewed initial pass must not invoke any exporter.
            original = run.read_text()
            run.write_text(json.dumps({k: v for k, v in document.items() if k != 'final_review'}))
            self.assertEqual(json.loads(close_worker(request, receipt).read_text())['status'], 'review_required')
            run.write_text(original)
            def exported(*args, **kwargs):
                book = root / 'leads.xlsx'; book.write_bytes(b'fixture workbook')
                (root / 'validation.json').write_text(json.dumps({'delivery_allowed': True,
                    'results_sha256': hashlib.sha256(run.read_bytes()).hexdigest(),
                    'workbook_sha256': hashlib.sha256(book.read_bytes()).hexdigest()}))
                return {'export': {'path': str(book)}}
            with patch('research_tools.ResearchTools.finish', side_effect=exported) as finish:
                status = json.loads(close_worker(request, receipt, {'TYCHE_WORKSPACE_NODE': '/fixture/node'}).read_text())
            finish.assert_called_once()
            self.assertTrue(status['delivery_allowed'])
            with patch('research_tools.ResearchTools.finish', side_effect=AssertionError('Already delivered')):
                self.assertTrue(json.loads(close_worker(request, receipt).read_text())['delivery_allowed'])
            document['accepted'] = [{'changed': True}]
            run.write_text(json.dumps(document))
            with patch('research_tools.ResearchTools.finish', side_effect=AssertionError('Stale review')):
                self.assertFalse(json.loads(close_worker(request, receipt).read_text())['delivery_allowed'])

    def test_installed_bundle_paths_are_supplied_without_changing_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle=Path(directory)/'.cache/codex-runtimes/codex-primary-runtime/dependencies'
            for name in ('node/bin/node','node/node_modules','python/bin/python3'):
                path=bundle/name;path.parent.mkdir(parents=True,exist_ok=True);path.touch()
            original={'PATH':'/existing'}
            with patch('codex_tyche.Path.home',return_value=Path(directory)):
                env=workspace_environment(original)
            self.assertEqual(original,{'PATH':'/existing'})
            self.assertEqual(env['TYCHE_WORKSPACE_NODE_MODULES'],str(bundle/'node/node_modules'))
            self.assertEqual(env['PATH'],str(bundle/'node/bin')+os.pathsep+'/existing')

    def test_explicit_host_configuration_is_preserved(self):
        supplied={'PATH':'/bin','TYCHE_WORKSPACE_NODE':'/custom/bin/node',
                  'TYCHE_WORKSPACE_NODE_MODULES':'/custom/node_modules','TYCHE_WORKSPACE_PYTHON':'/custom/bin/python3'}
        env=workspace_environment(supplied)
        for k in supplied:
            if k!='PATH':self.assertEqual(env[k],supplied[k])

    def test_native_config_binds_paths_and_forwards_names_not_secrets(self):
        with tempfile.TemporaryDirectory(prefix='tyche space ') as directory:
            path = Path(directory) / 'results.json'
            with patch.dict(os.environ, {'DEEPLINE_API_KEY': 'not-a-real-secret'}):
                config = tool_configuration(path, readonly=True)
            self.assertIn(str(path), config)
            self.assertIn('--read-only', config)
            self.assertIn('DEEPLINE_API_KEY', config)
            self.assertIn('CODEX_HOME', config)
            self.assertIn('TYCHE_RUN_STARTED_AT', config)
            self.assertIn('TYCHE_REQUEST_FILE', config)
            self.assertNotIn('not-a-real-secret', config)
            self.assertNotIn('sandbox_mode', config)
            self.assertNotIn('permission-profile', config)
            self.assertIn('required = true', config)

    def test_smoke_requires_successful_native_call_even_when_model_exits_zero(self):
        event = {'type':'item.completed', 'item':{'type':'mcp_tool_call', 'server':'tyche', 'tool':'tyche_inspect', 'status':'completed'}}
        for status in ('completed', 'failed'):
            event['item']['status'] = status
            output = SimpleNamespace(returncode=0, stdout=(json.dumps(event)+'\n') * 2, stderr='')
            with patch('codex_tyche.subprocess.run', return_value=output), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                if status == 'completed': self.assertEqual(smoke(['fixture'], {}), 0)
                else:
                    with self.assertRaisesRegex(RuntimeError, 'smoke test failed'): smoke(['fixture'], {})


if __name__=='__main__':unittest.main()
