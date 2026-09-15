"""Provider subprocesses must not share the tool server's control stream."""
import json
from pathlib import Path
import selectors
import subprocess
import sys
import unittest


class ProviderStdinTests(unittest.TestCase):
    def test_provider_cannot_change_control_pipe_mode_or_end_research_server(self):
        scripts = str(Path(__file__).resolve().parents[1] / 'scripts')
        code = f'''import os,sys
sys.path.insert(0, {scripts!r})
import deepline
from tyche_tools import serve
class Session:
    def call(self, name, arguments):
        status, output, error = deepline._invoke([sys.executable, '-c',
            'import os; os.set_blocking(0, False); print("ok")'], 20)
        return {{'status': status, 'stdin_blocking': os.get_blocking(0)}}
serve(Session())
'''
        child = subprocess.Popen([sys.executable, '-u', '-c', code], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        selector = selectors.DefaultSelector()
        selector.register(child.stdout, selectors.EVENT_READ)
        try:
            for ident in range(3):
                child.stdin.write(json.dumps({'id': ident, 'method': 'tools/call',
                    'params': {'name': 'fixture', 'arguments': {}}}) + '\n')
                child.stdin.flush()
                self.assertTrue(selector.select(timeout=30), 'Server did not answer')
                response = json.loads(child.stdout.readline())
                self.assertEqual(response['id'], ident)
                self.assertFalse(response['result']['isError'])
                result = json.loads(response['result']['content'][0]['text'])
                self.assertTrue(result['stdin_blocking'], 'Subprocess changed the server control pipe')
                self.assertEqual(result['status'], 0)
                self.assertIsNone(child.poll())
        finally:
            child.stdin.close()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.terminate()
                child.wait(timeout=5)
            selector.close()
            child.stdout.close()
            child.stderr.close()


if __name__ == '__main__':
    unittest.main()
