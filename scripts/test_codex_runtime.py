import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from codex_tyche import workspace_environment


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


if __name__=='__main__':unittest.main()
