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


def now():
    return datetime.now(timezone.utc).isoformat()


def estimate(usage, model):
    if model != 'gpt-5.6-luna':
        raise ValueError('No verified pricing for ' + str(model))
    required = ('input_tokens', 'cached_input_tokens', 'output_tokens')
    if any(type(usage.get(k)) is not int or usage[k] < 0 for k in required):
        raise ValueError('Missing or invalid input/cache/output token breakdown')
    inputs, cached, output = (usage[k] for k in required)
    if cached > inputs:
        raise ValueError('Cached tokens exceed input tokens')
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
    low = price(writes or 0, False)
    high = price(writes if writes is not None else inputs - cached, inputs > 272_000)
    return {'minimum': float(low), 'maximum': float(high)}


class UsageReceipt:
    def __init__(self, request_file, model, effort, service_tier):
        request_file = Path(request_file).resolve(strict=True)
        directory = request_file.parent / 'model-usage'
        directory.mkdir(exist_ok=True)
        self.path = directory / (str(uuid.uuid4()) + '.json')
        self.data = {'version': 1, 'invocation_id': self.path.stem, 'request_file': str(request_file),
            'started_at': now(), 'finished_at': None, 'model': model, 'reasoning_effort': effort,
            'requested_service_tier': service_tier, 'thread_id': None, 'status': 'running',
            'usage': None, 'standard_api_equivalent_usd': None, 'actual_model_billed_usd': None,
            'pricing_source': PRICING_SOURCE, 'pricing_checked_on': '2026-09-13',
            'pricing_basis': 'standard_api_equivalent_not_actual_billing',
            'limitations': ['Standard rates exclude Fast/priority premiums and hosted-tool charges.',
                            'Missing per-request context/cache-write details produce a price range.']}
        with self.path.open('x', encoding='utf-8') as stream:
            json.dump(self.data, stream)

    def save(self):
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.data, indent=2) + '\n', encoding='utf-8')
        temporary.replace(self.path)

    def observe(self, event):
        if event.get('type') == 'thread.started':
            self.data['thread_id'] = event.get('thread_id')
            self.save()
        elif event.get('type') == 'turn.completed':
            if self.data['usage'] is not None:
                raise ValueError('Unexpected duplicate completed turn; preserve receipt for reconciliation')
            usage = event.get('usage') or {}
            fields = ('input_tokens', 'cached_input_tokens', 'cache_write_input_tokens', 'output_tokens', 'reasoning_output_tokens')
            self.data['usage'] = {k: usage[k] for k in fields if k in usage}
            try:
                self.data['standard_api_equivalent_usd'] = estimate(self.data['usage'], self.data['model'])
            except ValueError as exc:
                self.data['limitations'].append(str(exc))
            self.save()

    def finish(self, exit_code):
        self.data.update(exit_code=exit_code, finished_at=now(),
                         status='complete' if exit_code == 0 and self.data['standard_api_equivalent_usd'] is not None
                         and not self.data.get('capture_errors') else 'incomplete')
        self.save()


def execute_with_usage(command, cwd, env, receipt):
    """Forward the CLI event stream, retaining only numeric usage in the receipt."""
    code = None
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
                            receipt.data.setdefault('capture_errors', []).append(str(exc)[:300])
            code = child.wait()
        return code or (2 if receipt.data.get('capture_errors') else 0)
    finally:
        receipt.finish(code)


def report(results, receipt_paths, monitoring=None, run_directory=None):
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
    monitor_cost = monitoring.get('estimated_usd') if monitoring else None
    if monitor_cost is None:
        missing.append('Monitoring/rework model usage not supplied or incomplete')
    subtotal_low = Decimal(str(low or 0)) + worker_low + Decimal(str((monitor_cost or {}).get('minimum', 0)))
    subtotal_high = Decimal(str(high or low or 0)) + worker_high + Decimal(str((monitor_cost or {}).get('maximum', 0)))
    subtotal = {'minimum': float(subtotal_low), 'maximum': float(subtotal_high)}
    count = len(results.get('accepted', []))
    return {'status': 'incomplete' if missing else 'estimated',
        'basis': 'provider_charges_plus_standard_api_equivalent_models_not_actual_invoice',
        'provider_usd': {'confirmed': low, 'maximum': high}, 'worker_invocations': workers,
        'monitoring': monitoring, 'known_subtotal_standard_equivalent_usd': subtotal,
        'combined_standard_equivalent_usd': None if missing else subtotal,
        'cost_per_accepted_lead_standard_equivalent_usd': None if missing or not count else
            {k: float(Decimal(str(v)) / count) for k, v in subtotal.items()},
        'accepted_leads': count, 'missing': missing,
        'limitations': ['Not an actual full-cost invoice: model Fast/priority premiums, hosted-tool charges and subscription allocation are not priced.',
                        'Supply every invocation from this run, including interrupted attempts; never omit an unpriced attempt.']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results', type=Path)
    parser.add_argument('--model-receipts', type=Path, nargs='*', default=None)
    parser.add_argument('--monitoring-cost', type=Path)
    args = parser.parse_args()
    try:
        receipts = args.model_receipts if args.model_receipts is not None else sorted((args.results.parent / 'model-usage').glob('*.json'))
        output = report(json.loads(args.results.read_text()), receipts,
                        json.loads(args.monitoring_cost.read_text()) if args.monitoring_cost else None, args.results.parent)
        print(json.dumps(output, indent=2))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, str(exc) + '\n')
