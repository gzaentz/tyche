#!/usr/bin/env python3
"""Launch a fresh, project-only Codex test without changing global settings."""

import argparse
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time

from run_costs import UsageReceipt, execute_with_usage, save_report


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / '.agents' / 'skills'
MODEL = 'gpt-5.6-luna'
REASONING_EFFORT = 'xhigh'
SERVICE_TIER = 'fast'
ISOLATION_INSTRUCTIONS = (
    'You are already inside the isolated TYCHE runtime (TYCHE_ISOLATED_RUN=1). '
    'You are the sourcing worker, not the outer monitor. '
    'Launcher state, logs, model-usage receipts and monitor notes in your assigned '
    'run directory describe this invocation; their presence is not evidence of '
    'a separate worker. Do not wait for those artifacts to produce sourcing results. '
    'Execute sourcing requests directly with the local lead-sourcing skill. '
    'Never launch scripts/codex_tyche.py from this runtime. '
    'Use project-local instructions and skills. '
    'Do not read or use user-global AGENTS.md, skills, or memories. '
    'Normal system instructions, managed permissions, and execution rules still apply. '
    'Use the project lead-sourcing skill for sourcing requests. '
    'When TYCHE native tools are available, use them for all run setup, provider '
    'lookups, saving reviews, inspecting state and finalization. The tools are '
    'bound to this run. Read source code or use diagnostic CLIs only for a '
    'specific tool failure; ordinary research needs no shell bookkeeping.'
)
SMOKE_PROMPT = (
    'This is a read-only configuration smoke test, not a sourcing job. '
    'List the available skill names from your supplied skill catalog. '
    'Read .agents/skills/lead-sourcing/SKILL.md and state the required deliverable filenames. '
    'Do not read global instruction or skill files, call providers, search the web, '
    'delegate work, or modify files. Call tyche_inspect with no arguments twice '
    'in sequence to verify the native tool connection can be reused; both calls '
    'are read-only and make no provider calls.'
)


def inside(path, directory):
    try:
        Path(path).resolve().relative_to(directory.resolve())
        return True
    except ValueError:
        return False


def workspace_environment(env):
    """Use configured paths or the installed desktop bundle, without downloads."""
    env = dict(env)
    bundle = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies'
    defaults = {
        'TYCHE_WORKSPACE_NODE': bundle / 'node/bin/node',
        'TYCHE_WORKSPACE_NODE_MODULES': bundle / 'node/node_modules',
        'TYCHE_WORKSPACE_PYTHON': bundle / 'python/bin/python3',
    }
    for key, default in defaults.items():
        if not env.get(key) and default.exists():
            env[key] = str(default)
    if env.get('TYCHE_WORKSPACE_NODE'):
        env['PATH'] = str(Path(env['TYCHE_WORKSPACE_NODE']).parent) + os.pathsep + env.get('PATH', '')
    return env


def tool_configuration(run_file, *, readonly=False):
    """Bind the MCP process to this run and the existing command sandbox.

    The stdio relay obtains sandbox metadata from Codex on the first tool call.
    Research executes in its child with that exact filesystem/network policy.
    """
    args = [str(SKILL_ROOT / 'lead-sourcing/scripts/tyche_tools.py'),
            '--run-file', str(Path(run_file).resolve())]
    if readonly:
        args.append('--read-only')
    forwarded = ['CODEX_HOME', 'DEEPLINE_API_KEY', 'DEEPLINE_BIN', 'SCRAPINGDOG_API_KEY',
                 'DEEPLINE_NO_AUTO_UPDATE', 'DEEPLINE_SKIP_SKILLS_SYNC', 'TYCHE_WORKSPACE_NODE',
                 'TYCHE_WORKSPACE_NODE_MODULES', 'TYCHE_WORKSPACE_PYTHON', 'PYTHONDONTWRITEBYTECODE']
    return ('\n[mcp_servers.tyche]\ncommand = ' + json.dumps(sys.executable) + '\nargs = ' + json.dumps(args) + '\n'
            'env_vars = ' + json.dumps(forwarded) + '\n'
            'cwd = ' + json.dumps(str(ROOT)) + '\nrequired = true\n'
            'startup_timeout_sec = 40\ntool_timeout_sec = 900\n'
            'default_tools_approval_mode = "approve"\n')


def inspect_runtime(env, overrides, start_thread=False, native_tools=False):
    """Use the installed runtime's discovery results, not a filesystem guess."""
    messages = queue.Queue()
    with tempfile.TemporaryFile(mode='w+') as errors:
        proc = subprocess.Popen(
            ['codex', 'app-server', '--stdio', *overrides], cwd=ROOT, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors,
            text=True, bufsize=1,
        )

        def read_messages():
            for line in proc.stdout:
                try:
                    messages.put(json.loads(line))
                except ValueError:
                    pass

        reader = threading.Thread(target=read_messages, daemon=True)
        reader.start()

        def request(ident, method, params):
            proc.stdin.write(json.dumps({'id': ident, 'method': method, 'params': params}) + '\n')
            proc.stdin.flush()
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                try:
                    message = messages.get(timeout=1)
                except queue.Empty:
                    if proc.poll() is not None:
                        raise RuntimeError('Codex app-server could not start; run codex doctor.')
                    continue
                if message.get('id') == ident:
                    if 'error' in message:
                        raise RuntimeError(f'{method}: {message["error"]["message"]}')
                    return message['result']
            raise RuntimeError(f'Codex timed out during {method}; no test was launched.')

        try:
            request(1, 'initialize', {
                'clientInfo': {'name': 'tyche_isolation_check', 'version': '1.0'},
                'capabilities': {'experimentalApi': True},
            })
            proc.stdin.write('{"method":"initialized"}\n')
            proc.stdin.flush()
            listed = request(2, 'skills/list', {'cwds': [str(ROOT)], 'forceReload': True})
            if any(row.get('errors') for row in listed['data']):
                raise RuntimeError('Codex reported skill discovery errors; no test was launched.')
            skills = [
                {key: skill.get(key) for key in ('name', 'path', 'scope', 'enabled')}
                for row in listed['data'] for skill in row['skills']
            ]
            result = {'skills': skills}
            if start_thread:
                # Inherit the same project sandbox/network configuration as
                # the real execution. A read-only override hid proxy startup
                # failures that occurred only when sourcing enabled networking.
                started = request(3, 'thread/start', {
                    'cwd': str(ROOT), 'ephemeral': True,
                })
                if 'instructionSources' not in started:
                    raise RuntimeError('This Codex version cannot report loaded instruction sources.')
                result['instruction_sources'] = started['instructionSources']
                result['model'] = started.get('model')
                result['native_tools'] = []
                if native_tools:
                    servers = request(4, 'mcpServerStatus/list', {'limit': 100})
                    tyche = next((r for r in servers.get('data', []) if r.get('name') == 'tyche'), None)
                    if tyche is None or len(tyche.get('tools', {})) != 5:
                        raise RuntimeError('TYCHE native tools did not initialize; no model turn or provider call was started.')
                    result['native_tools'] = list(tyche['tools'])
            return result
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            reader.join(timeout=1)
            proc.stdin.close()
            proc.stdout.close()


def smoke(command, env):
    """A model exit code alone cannot prove the native tool actually worked."""
    completed = subprocess.run(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                               capture_output=True, text=True, timeout=120)
    print(completed.stdout, end='', flush=True)
    print(completed.stderr, end='', file=sys.stderr, flush=True)
    calls = []
    for line in completed.stdout.splitlines():
        try:
            event = json.loads(line)
            item = event.get('item', {}) if isinstance(event, dict) else {}
            if event.get('type') == 'item.completed' and item.get('type') == 'mcp_tool_call' and item.get('server') == 'tyche':
                calls.append(item)
        except (ValueError, AttributeError):
            continue
    if (completed.returncode or len(calls) != 2 or any(call.get('tool') != 'tyche_inspect'
            or call.get('status') != 'completed' for call in calls)):
        raise RuntimeError('Read-only native tool smoke test failed; no sourcing run was started.')
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true', help='Verify isolation and sourcing-session startup, including network setup; no model turn or provider calls.')
    mode.add_argument('--smoke', action='store_true', help='Run a read-only model test; no provider calls.')
    mode.add_argument('--exec', action='store_true', help='Run the supplied prompt noninteractively.')
    mode.add_argument('--exec-file', type=Path, help='Read the exact request from a UTF-8 file and run it noninteractively.')
    parser.add_argument('prompt', nargs='?', help='Sourcing request with explicit scope and budget.')
    args = parser.parse_args()
    if args.exec and not args.prompt:
        parser.error('--exec requires a prompt')
    if (args.check or args.smoke) and args.prompt:
        parser.error('--check and --smoke do not accept a prompt')
    if args.exec_file is not None:
        if args.prompt is not None:
            parser.error('--exec-file does not accept an additional prompt')
        args.prompt = args.exec_file.read_text(encoding='utf-8')
        if not args.prompt.strip():
            parser.error('the request file is empty')
    if os.environ.get('TYCHE_ISOLATED_RUN') == '1':
        raise RuntimeError('Already inside isolated TYCHE. Use the local lead-sourcing skill directly; nested launch refused.')

    source_home = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))).resolve()
    # Give CODEX_HOME its documented meaning only in the child process. The
    # parent environment and the user's global instruction/config files stay intact.
    with tempfile.TemporaryDirectory(prefix='tyche-codex-home-') as profile:
        tyche_codex_home = Path(profile)
        native_tools = args.exec_file is not None or args.check or args.smoke
        tool_run_file = (args.exec_file.resolve().parent / 'results.json' if args.exec_file is not None
                         else tyche_codex_home / 'tool-check/results.json')
        (tyche_codex_home / 'config.toml').write_text(
            '[projects.' + json.dumps(str(ROOT)) + ']\ntrust_level = "trusted"\n'
            + (tool_configuration(tool_run_file, readonly=args.check or args.smoke) if native_tools else '')
        )
        for name in ('auth.json', 'rules'):
            source = source_home / name
            if source.exists():
                (tyche_codex_home / name).symlink_to(source, target_is_directory=source.is_dir())
        # Research uses the installed CLI and project skill. CLI self-updates
        # and global skill sync can contact npm or alter context mid-run.
        env = dict(os.environ, CODEX_HOME=profile, TYCHE_ISOLATED_RUN='1',
                   DEEPLINE_NO_AUTO_UPDATE='1', DEEPLINE_SKIP_SKILLS_SYNC='1')
        env = workspace_environment(env)
        if args.exec_file is not None:
            for key in ('TYCHE_WORKSPACE_NODE', 'TYCHE_WORKSPACE_PYTHON'):
                if not Path(env.get(key, '')).is_file():
                    raise RuntimeError(f'Configure {key} before starting a sourcing run')
            if not (Path(env.get('TYCHE_WORKSPACE_NODE_MODULES', '')) / '@oai/artifact-tool/package.json').is_file():
                raise RuntimeError('Configure TYCHE_WORKSPACE_NODE_MODULES before starting a sourcing run')
        # Pin the isolated runner's model selection instead of inheriting the
        # user's current Codex default.  `xhigh` is the UI's Extra High effort;
        # `fast` selects the accelerated service tier when available.
        overrides = ['-c', 'model=' + json.dumps(MODEL),
                     '-c', 'model_reasoning_effort=' + json.dumps(REASONING_EFFORT),
                     '-c', 'service_tier=' + json.dumps(SERVICE_TIER)]
        for feature in ('plugins', 'apps', 'memories', 'hooks', 'shell_snapshot'):
            overrides.extend(['--disable', feature])
        overrides.extend(['-c', 'developer_instructions=' + json.dumps(ISOLATION_INSTRUCTIONS)])

        discovered = inspect_runtime(env, overrides)
        excluded = [skill['path'] for skill in discovered['skills']
                    if not inside(skill['path'], SKILL_ROOT)]
        overrides.extend(['-c', 'skills.config=[' + ','.join(
            '{path=' + json.dumps(path) + ',enabled=false}' for path in excluded
        ) + ']'])
        verified = inspect_runtime(env, overrides, start_thread=True, native_tools=native_tools)
        active = [skill for skill in verified['skills'] if skill['enabled']]
        if not active or any(not inside(skill['path'], SKILL_ROOT) for skill in active):
            raise RuntimeError('Skill isolation failed; no test was launched.')
        if any(not inside(path, ROOT) for path in verified['instruction_sources']):
            raise RuntimeError('External instructions were loaded; no test was launched.')
        summary = {
            'isolation_passed': True, 'cwd': str(ROOT),
            'instruction_sources': verified['instruction_sources'],
            'enabled_skills': active, 'disabled_external_skills': len(excluded),
            'plugins_enabled': False, 'apps_enabled': False, 'memories_enabled': False,
            'model': verified['model'], 'reasoning_effort': REASONING_EFFORT,
            'service_tier': SERVICE_TIER,
            'native_tools': verified['native_tools'],
        }
        print(json.dumps(summary, indent=2), flush=True)
        if args.check:
            return 0
        if not (source_home / 'auth.json').is_file():
            raise RuntimeError('No reusable file-based Codex login. Run codex login first, or use a separately authenticated profile.')

        if args.smoke or args.exec or args.exec_file is not None:
            # File-based runs retain a journal only inside this temporary
            # profile, long enough to capture numeric per-response usage.
            command = ['codex', 'exec', *([] if args.exec_file is not None else ['--ephemeral']), *overrides]
            if args.exec_file is not None or args.smoke:
                command.append('--json')
            if args.smoke:
                command.extend(['--sandbox', 'read-only', '-c', 'web_search="disabled"',
                                '-c', 'sandbox_workspace_write.network_access=false',
                                '--disable', 'unbounded_connection_retries'])
            command.append(SMOKE_PROMPT if args.smoke else args.prompt)
        else:
            command = ['codex', *overrides]
            if args.prompt:
                command.append(args.prompt)
        try:
            if args.smoke:
                return smoke(command, env)
            if args.exec_file is not None:
                receipt = UsageReceipt(args.exec_file, MODEL, REASONING_EFFORT, SERVICE_TIER)
                print(json.dumps({'model_usage_receipt': str(receipt.path)}), flush=True)
                try:
                    return execute_with_usage(command, ROOT, env, receipt, profile=tyche_codex_home)
                finally:
                    print(json.dumps({'run_cost_report': str(save_report(args.exec_file.resolve().parent))}), flush=True)
            return subprocess.call(
                command, cwd=ROOT, env=env,
                stdin=subprocess.DEVNULL if args.smoke or args.exec or args.exec_file is not None else None,
                timeout=120 if args.smoke else None,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError('The read-only smoke test exceeded 120 seconds and was stopped.')


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, OSError) as exc:
        print(f'TYCHE isolation: {exc}', file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
