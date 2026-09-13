"""Captured email-response fixtures; no network or provider calls."""
import hashlib
import json
from pathlib import Path


def write_email_receipts(run_file, document):
    from budget_guard import run_fingerprint
    directory = Path(run_file).parent / "receipts"
    directory.mkdir(exist_ok=True)
    for row in document.get("accepted", []):
        for contact in [row.get("primary_contact", {}), *row.get("backup_contacts", [])]:
            receipt = contact.get("email_validation")
            if not isinstance(receipt, dict):
                continue
            for item in [receipt, *([receipt['fallback']] if 'fallback' in receipt else [])]:
                source = item['source'];rid = source['route_id']
                route = next(r for r in document['routes'] if r['route_id'] == rid)
                fingerprint = hashlib.sha256(rid.encode()).hexdigest()
                route['request_fingerprint'] = fingerprint
                raw = {k:v for k,v in item.items() if k in ('email','status','result','sub_status','score')}
                if item.get('provider_status'):
                    raw = {'status':item['provider_status'],'error':{'message':'Upstream unavailable'}}
                saved = {**source,'receipt_status':'complete','status':route['provider_status'],
                         'run_fingerprint':run_fingerprint(run_file),'request_fingerprint':fingerprint,
                         'attempt':{'request':{'payload':{'email':item['email']}}},
                         'provider_response':{'exit_code':0,'body':{'status':'ok','element':raw},'stderr':''}}
                (directory/(rid+'.json')).write_text(json.dumps(saved))
