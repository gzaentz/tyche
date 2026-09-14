import os
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from codex_tyche import smoke, tool_configuration, workspace_environment


class WorkspaceRuntimeTests(unittest.TestCase):
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
