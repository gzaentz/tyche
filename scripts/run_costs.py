#!/usr/bin/env python3
"""Persist numeric Codex usage and report complete or explicitly partial run costs."""

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys
import subprocess
import uuid

# Standard API-equivalent USD per million tokens, checked 2026-09-13.
# This is a comparison estimate, never a ChatGPT subscription/credit invoice.
PRICING_SOURCE = 'https://developers.openai.com/api/docs/models/gpt-5.6-luna'
LUNA_RATES = {'input': '0.20', 'cached_input': '0.02', 'cache_write': '0.25', 'output': '1.20'}
USAGE_FIELDS = ('input_tokens', 'cached_input_tokens', 'cache_write_input_tokens',
                'output_tokens', 'reasoning_output_tokens', 'total_tokens')


def now():
    return datetime.now(timezone.utc).isoformat()


def estimate(usage, model, *, per_request=False):
    if model != 'gpt-5.6-luna':
        raise ValueError('No verified pricing for ' + str(model))
    required = ('input_tokens', 'cached_input_tokens', 'output_tokens')
    if any(type(usage.get(k)) is not int or usage[k] < 0 for k in required):
        raise ValueError('Missing or invalid input/cache/output token breakdown')
    inputs, cached, output = (usage[k] for k in required)
    if cached > inputs:
        raise ValueError('Cached tokens exceed input tokens')
    if 'total_tokens' in usage and usage['total_tokens'] != inputs + output:
        raise ValueError('Total tokens do not match input plus output')
    writes = usage.get('cache_write_input_tokens')
    if writes is not None and (type(writes) is not int or not 0 <= writes <= inputs - cached):
        raise ValueError('Invalid cache-write tokens')
    r = {k: Decimal(v) for k, v in LUNA_RATES.items()}
    def price(write_count, long_context):
        prompt = (inputs - cached - write_count) * r['input'] + cached * r['cached_input'] + write_count * r['cache_write']
        return (prompt * (2 if long_context else 1) + output * r['output'] * (Decimal('1.5') if long_context else 1)) / 1_000_000
    # The CLI reports totals for a turn, not individual request sizes/cache writes.
    # Bound missing details instead of applying long-context pricing to a total
    # as if it were one request, or pretending all input was uncached.
    low = price(writes or 0, per_request and inputs > 272_000)
    high = price(writes if writes is not None else inputs - cached, inputs > 272_000)
    return {'minimum': float(low), 'maximum': float(high)}


class UsageReceipt:
    def __init__(self, request_file, model, effort, service_tier):
        request_file = Path(request_file).resolve(strict=True)
        directory = request_file.parent / 'model-usage'
        directory.mkdir(exist_ok=True)
        self.path = directory / (str(uuid.uuid4()) + '.json')
        self.data = {'version': 2, 'invocation_id': self.path.stem, 'request_file': str(request_file),
            'started_at': now(), 'finished_at': None, 'model': model, 'reasoning_effort': effort,
            'requested_service_tier': service_tier, 'thread_id': None, 'status': 'running',
            'usage': None, 'standard_api_equivalent_usd': None, 'actual_model_billed_usd': None,
            'responses': [], 'usage_reconciled': False,
            'pricing_source': PRICING_SOURCE, 'pricing_checked_on': '2026-09-13',
            'pricing_basis': 'standard_api_equivalent_not_actual_billing',
            'limitations': ['Standard rates exclude Fast/priority premiums and hosted-tool charges.',
                            'Actual subscription charges require billing data; token prices are API-equivalent.']}
        with self.path.open('x', encoding='utf-8') as stream:
            json.dump(self.data, stream)

    def save(self):
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.data, indent=2) + '\n', encoding='utf-8')
        temporary.replace(self.path)

    def observe(self, event):
        if event.get('type') == 'thread.started':
            if self.data['thread_id'] is not None and self.data['thread_id'] != event.get('thread_id'):
                raise ValueError('Worker thread identity changed')
            self.data['thread_id'] = event.get('thread_id')
            self.save()
        elif event.get('type') == 'turn.completed':
            if self.data['usage'] is not None:
                raise ValueError('Unexpected duplicate completed turn; preserve receipt for reconciliation')
            usage = event.get('usage') or {}
            self.data['usage'] = {k: usage[k] for k in USAGE_FIELDS if k in usage}
            try:
                self.data['standard_api_equivalent_usd'] = estimate(self.data['usage'], self.data['model'])
            except ValueError as exc:
                self.capture_error(exc)
            self.save()

    def observe_response(self, payload, timestamp, model):
        if payload.get('thread_id') != self.data['thread_id']:
            raise ValueError('Usage record belongs to another worker')
        response_id = payload.get('response_id')
        turn_id = payload.get('turn_id')
        if not isinstance(response_id, str) or not response_id or not turn_id:
            raise ValueError('Usage record lacks response/turn identity')
        usage = {k: payload.get('usage', {})[k] for k in USAGE_FIELDS if k in payload.get('usage', {})}
        if any(type(usage.get(k)) is not int or usage[k] < 0 for k in USAGE_FIELDS):
            raise ValueError('Per-response usage lacks the complete numeric breakdown')
        if usage['reasoning_output_tokens'] > usage['output_tokens']:
            raise ValueError('Reasoning output exceeds total output')
        record = {'response_id': response_id, 'turn_id': turn_id, 'model': model,
                  'usage': usage, 'recorded_at': timestamp}
        for previous in self.data['responses']:
            if previous['response_id'] == response_id:
                if any(previous[k] != record[k] for k in ('turn_id', 'model', 'usage')):
                    raise ValueError('Conflicting usage for the same response')
                return
        record['standard_api_equivalent_usd'] = estimate(usage, model, per_request=True)
        self.data['responses'].append(record)
        self.save()

    def capture_error(self, exc):
        message = str(exc)[:300]
        errors = self.data.setdefault('capture_errors', [])
        if message not in errors:
            errors.append(message)
            self.save()

    def finish(self, exit_code):
        responses = self.data['responses']
        totals = {k: sum(r['usage'][k] for r in responses) for k in USAGE_FIELDS}
        self.data['response_usage_totals'] = totals if responses else None
        final = self.data['usage']
        self.data['usage_reconciled'] = bool(responses and final
            and all(k in final for k in ('input_tokens', 'cached_input_tokens', 'output_tokens')) and all(
            totals[k] == v for k, v in final.items()))
        if responses:
            self.data['standard_api_equivalent_usd'] = {
                k: float(sum((Decimal(str(r['standard_api_equivalent_usd'][k])) for r in responses), Decimal(0)))
                for k in ('minimum', 'maximum')}
        if not self.data['usage_reconciled']:
            self.capture_error('Per-response journal is missing or does not reconcile with the final usage totals')
        self.data.update(exit_code=exit_code, finished_at=now(),
                         status='complete' if exit_code == 0 and self.data['standard_api_equivalent_usd'] is not None
                         and self.data['usage_reconciled'] and not self.data.get('capture_errors') else 'incomplete')
        self.save()


class UsageJournal:
    """Read only this worker's temporary journal; retain no prompts or tool data."""

    def __init__(self, profile, receipt):
        self.profile, self.receipt = Path(profile), receipt
        self.path, self.offset, self.discarding = None, 0, False
        self.model = receipt.data['model']

    def poll(self):
        thread_id = self.receipt.data['thread_id']
        if not thread_id:
            return
        uuid.UUID(thread_id)  # Never interpolate arbitrary event data into a path glob.
        if self.path is None:
            paths = list((self.profile / 'sessions').glob('*/*/*/*' + thread_id + '.jsonl'))
            if not paths:
                return
            if len(paths) != 1:
                raise ValueError('Multiple journals found for the worker')
            self.path = paths[0].resolve()
            self.path.relative_to(self.profile.resolve())
        with self.path.open('rb') as stream:
            stream.seek(self.offset)
            while True:
                line = stream.readline(65537)
                if not line:
                    break
                if not line.endswith(b'\n') and len(line) < 65537:
                    break  # The journal writer has not finished this record yet.
                self.offset = stream.tell()
                if self.discarding or len(line) > 65536:
                    if not self.discarding and b'"token_usage_record"' in line[:1024]:
                        raise ValueError('Usage record exceeded the metadata size limit')
                    self.discarding = not line.endswith(b'\n')
                    continue
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError('Invalid worker journal record')
                kind, payload = record.get('type'), record.get('payload', {})
                if not isinstance(payload, dict):
                    raise ValueError('Invalid worker journal payload')
                if kind == 'turn_context':
                    self.model = payload.get('model') or self.model
                elif kind == 'token_usage_record':
                    self.receipt.observe_response(payload, record.get('timestamp'), self.model)
                elif kind == 'event_msg' and payload.get('type') in ('model_reroute', 'model_rerouted'):
                    raise ValueError('Model rerouted; billing needs the actual response model')


def execute_with_usage(command, cwd, env, receipt, *, profile=None):
    """Forward the CLI event stream, retaining only numeric usage in the receipt."""
    code = None
    journal = UsageJournal(profile, receipt) if profile is not None else None
    def capture_journal():
        if journal:
            try:
                journal.poll()
            except (ValueError, OSError, TypeError, KeyError) as exc:
                receipt.capture_error(exc)
    try:
        with subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, text=True, encoding='utf-8') as child:
            for line in child.stdout:
                sys.stdout.write(line)
                sys.stdout.flush()
                # Tool/file output can be large; only small metadata events
                # can contain the usage/thread information we retain.
                if len(line) <= 65536:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(event, dict):
                        try:
                            receipt.observe(event)
                        except (ValueError, OSError, TypeError) as exc:
                            # A telemetry problem must not interrupt a sourcing
                            # call. Finish the worker, then expose incomplete cost.
                            receipt.capture_error(exc)
                capture_journal()
            code = child.wait()
    finally:
        capture_journal()  # The worker has flushed its journal before profile cleanup.
        receipt.finish(code)
    return code or (0 if receipt.data['status'] == 'complete' else 2)


def report(results, receipt_paths, run_directory=None):
    providers = results.get('cost_summary', {})
    deepline = providers.get('deepline', {})
    scraping = providers.get('scrapingdog', {})
    low, high = deepline.get('confirmed_usd'), deepline.get('maximum_usd')
    missing = []
    # ScrapingDog credits have a plan-specific conversion. Do not treat them as USD.
    if scraping.get('maximum_credits') != 0:
        missing.append('ScrapingDog USD cost needs the run plan conversion')
    if not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (low, high)):
        missing.append('Provider cost is not fully known')
    worker_low = worker_high = Decimal(0)
    seen, workers = set(), []
    for path in receipt_paths:
        receipt = json.loads(Path(path).read_text())
        if run_directory is not None and Path(receipt.get('request_file', '')).parent.resolve() != Path(run_directory).resolve():
            raise ValueError('Model receipt belongs to a different run')
        identity = receipt.get('invocation_id')
        if not identity or identity in seen:
            raise ValueError('Missing or duplicate model invocation identity')
        seen.add(identity)
        workers.append(receipt)
        cost = receipt.get('standard_api_equivalent_usd')
        if receipt.get('status') != 'complete' or not cost:
            missing.append('Incomplete model usage: ' + identity)
        if cost:
            worker_low += Decimal(str(cost['minimum']))
            worker_high += Decimal(str(cost['maximum']))
    if not workers:
        missing.append('Sourcing model usage was not captured')
    subtotal_low = Decimal(str(low or 0)) + worker_low
    subtotal_high = Decimal(str(high or low or 0)) + worker_high
    subtotal = {'minimum': float(subtotal_low), 'maximum': float(subtotal_high)}
    count = len(results.get('accepted', []))
    return {'status': 'incomplete' if missing else 'calculated' if subtotal_low == subtotal_high else 'estimated_range',
        'scope': 'tyche_run_only',
        'basis': 'provider_charges_plus_standard_api_equivalent_models_not_actual_invoice',
        'provider_usd': {'confirmed': low, 'maximum': high}, 'worker_invocations': workers,
        'worker_standard_api_equivalent_usd': {'minimum': float(worker_low), 'maximum': float(worker_high)} if workers else None,
        'actual_model_billed_usd': None,
        'known_subtotal_standard_equivalent_usd': subtotal,
        'combined_standard_equivalent_usd': None if missing else subtotal,
        'cost_per_accepted_lead_standard_equivalent_usd': None if missing or not count else
            {k: float(Decimal(str(v)) / count) for k, v in subtotal.items()},
        'accepted_leads': count, 'missing': missing,
        'limitations': ['Outer chat, monitoring and development costs are outside this run cost.',
                        'Not an actual full-cost invoice: model Fast/priority premiums, hosted-tool charges and subscription allocation are not priced.',
                        'Supply every invocation from this run, including interrupted attempts; never omit an unpriced attempt.']}


def save_report(run_directory, results_path=None):
    """Refresh the saved report from all attempts; no caller-supplied subset."""
    directory = Path(run_directory).resolve()
    results_path = Path(results_path) if results_path is not None else directory / 'results.json'
    results = json.loads(results_path.read_text()) if results_path.exists() else {}
    output = report(results, sorted((directory / 'model-usage').glob('*.json')), directory)
    path = directory / 'run-costs.json'
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(output, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)
    return path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results', type=Path)
    args = parser.parse_args()
    try:
        args.results.resolve(strict=True)
        print(save_report(args.results.parent, args.results).read_text(), end='')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, str(exc) + '\n')
