# TYCHE output contract

This is the normative, machine-readable contract for one lead-sourcing run.
The JSON Schema is draft 2020-12. A run directory is
`reports/<run-id>/` and contains exactly `report.md`, `results.json`, and
`leads.csv`.

## Lifecycle invariants

1. Account processing comes first. As soon as a company passes its account
   evidence gate, a contact lookup input must contain that accepted canonical
   `company` and `domain`. Do not search people for rejected or unresolved
   accounts.
2. An accepted account has separate `account_fit` and `signal_evidence` objects
   with independent evidence, and passes every account evidence rule. An
   accepted contact has a
   current-role/company claim and passes every contact evidence rule. These are
   separate gates.
3. Each accepted result has exactly one `primary_contact` and two or fewer
   `backup_contacts`. The stored candidate count is 1 to 3. Target the
   requested count (default 3), but keep a company accepted when one valid
   primary is found and record `backup_shortfall`. If no primary passes, put the
   company in `unresolved` with `no_current_role_contact`.
4. `accepted`, `rejected`, and `unresolved` are output states. They are not
   provider statuses. A `no_results` provider response is not a rejection;
   `timeout`, quota, authentication, schema, and provider errors are
   unresolved outcomes.
5. Accepted companies are unique by lower-case canonical domain with a leading
   `www.` removed. A contact may appear once per accepted company. A backup is
   not a second primary.
6. `email` and `phone` are absent from JSON contact objects unless the input
   `contact_fields` requests them. In CSV they are blank unless requested. Do
   not perform the lookup before the identity/current-role gate.
7. Reserve and refill is adaptive: after observed account or contact attrition,
   source one replacement from a changed route when budget permits. Do not
   prefetch or refill by a fixed multiplier such as 5x.
8. All dates are ISO calendar dates. A relative provider date may be resolved
   from retrieval time only when the original wording is retained in
   `report.md`; never invent a date or a person.

## Input contract

The normalized request must validate against this schema. Defaults are noted in
the schema and must be applied before provider work.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "tyche://lead-sourcing/input-contract/v1",
  "title": "TYCHE lead-sourcing request",
  "type": "object",
  "additionalProperties": false,
  "required": ["target_count", "icp", "buying_signals", "requested_roles", "time_window", "budget"],
  "properties": {
    "target_count": {"type": "integer", "minimum": 1},
    "icp": {"$ref": "#/$defs/icp"},
    "buying_signals": {
      "type": "array",
      "minItems": 1,
      "items": {"$ref": "#/$defs/signal"}
    },
    "requested_roles": {
      "type": "array",
      "minItems": 1,
      "items": {"type": "string", "minLength": 1}
    },
    "contacts_per_company": {
      "type": "integer",
      "minimum": 1,
      "maximum": 3,
      "default": 3
    },
    "time_window": {"$ref": "#/$defs/time_window"},
    "contact_fields": {
      "type": "array",
      "uniqueItems": true,
      "items": {"enum": ["email", "phone"]},
      "default": []
    },
    "budget": {"$ref": "#/$defs/input_budget"},
    "run_id": {
      "type": "string",
      "pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$"
    },
    "as_of_date": {"$ref": "#/$defs/date"}
  },
  "$defs": {
    "date": {
      "type": "string",
      "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"
    },
    "icp": {
      "type": "object",
      "additionalProperties": false,
      "minProperties": 1,
      "properties": {
        "company_types": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
        "industries": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
        "geographies": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
        "company_size": {
          "type": "object",
          "additionalProperties": false,
          "minProperties": 1,
          "properties": {
            "min_employees": {"type": "integer", "minimum": 0},
            "max_employees": {"type": "integer", "minimum": 0}
          }
        },
        "required_attributes": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
        "exclusions": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
        "custom_criteria": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}}
      }
    },
    "signal": {
      "type": "object",
      "additionalProperties": false,
      "required": ["kind"],
      "properties": {
        "kind": {"type": "string", "minLength": 1},
        "query": {"type": "string", "minLength": 1},
        "max_age_days": {"type": "integer", "minimum": 1},
        "source_preferences": {"type": "array", "items": {"type": "string", "minLength": 1}}
      }
    },
    "time_window": {
      "type": "object",
      "additionalProperties": false,
      "required": ["max_age_days"],
      "properties": {
        "max_age_days": {"type": "integer", "minimum": 1},
        "as_of_date": {"$ref": "#/$defs/date"}
      }
    },
    "input_budget": {
      "type": "object",
      "additionalProperties": false,
      "required": ["hard_stop"],
      "properties": {
        "deepline_credits": {"type": "number", "minimum": 0},
        "scrapingdog_credits": {"type": "number", "minimum": 0},
        "max_paid_calls": {"type": "integer", "minimum": 0},
        "hard_stop": {"const": true}
      },
      "anyOf": [
        {"required": ["deepline_credits"]},
        {"required": ["scrapingdog_credits"]}
      ]
    }
  }
}
```

The account gate is per company: contact lookup starts as soon as that company
has passed the account evidence gate. `contacts_per_company` defaults to 3 and
may be set from 1 to 3. Provider credit caps are separate because Deepline and
ScrapingDog units are not interchangeable; a cap of 0 disables that provider.
At least one provider credit cap is required. `hard_stop` is mandatory and
true. `max_paid_calls` is an optional additional guard and may be 0, but it is
not a substitute for a provider credit cap. Catalog search/describe calls are
read-only, but every paid call counts against its provider cap and call guard.
If a provider does not expose usage, set its output `spent` and route
`cost_credits` to `null`; never use `0` to mean unknown.

## `results.json` schema

Write one JSON object that validates against this schema. Do not add a second
top-level result list or hide rejected/unresolved rows in a count.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "tyche://lead-sourcing/results/v1",
  "title": "TYCHE lead-sourcing results",
  "type": "object",
  "additionalProperties": false,
  "required": ["schema_version", "run_id", "retrieved_at", "request", "budget", "routes", "summary", "accepted", "rejected", "unresolved", "stop_reason"],
  "properties": {
    "schema_version": {"const": "1.0"},
    "run_id": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$"},
    "retrieved_at": {"type": "string", "format": "date-time"},
    "request": {"$ref": "#/$defs/request_snapshot"},
    "budget": {"$ref": "#/$defs/output_budget"},
    "routes": {"type": "array", "items": {"$ref": "#/$defs/route"}},
    "summary": {"$ref": "#/$defs/summary"},
    "accepted": {"type": "array", "items": {"$ref": "#/$defs/accepted_company"}},
    "rejected": {"type": "array", "items": {"$ref": "#/$defs/outcome_row"}},
    "unresolved": {"type": "array", "items": {"$ref": "#/$defs/outcome_row"}},
    "stop_reason": {
      "enum": ["target_met", "budget_exhausted", "no_productive_route", "provider_stop", "input_or_configuration_stop"]
    }
  },
  "$defs": {
    "date": {
      "type": "string",
      "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"
    },
    "url": {
      "type": "string",
      "pattern": "^https?://[^\\s]+$"
    },
    "provider_status": {
      "enum": ["ok", "no_results", "partial", "rate_limited", "auth_failed", "quota_exceeded", "timeout", "schema_error", "provider_error", "config_error"]
    },
    "company": {
      "type": "object",
      "additionalProperties": false,
      "required": ["canonical_name", "domain"],
      "properties": {
        "canonical_name": {"type": "string", "minLength": 1},
        "domain": {"type": "string", "minLength": 1}
      }
    },
    "source": {
      "type": "object",
      "additionalProperties": false,
      "required": ["provider", "operation", "route_id"],
      "properties": {
        "provider": {"type": "string", "minLength": 1},
        "operation": {"type": "string", "minLength": 1},
        "tool": {"type": "string", "minLength": 1},
        "route_id": {"type": "string", "minLength": 1}
      }
    },
    "account_fit": {
      "type": "object",
      "additionalProperties": false,
      "required": ["fit_claim", "evidence_url", "evidence_date", "evidence_date_basis", "evidence_text", "source"],
      "properties": {
        "fit_claim": {"type": "string", "minLength": 1},
        "evidence_url": {"$ref": "#/$defs/url"},
        "evidence_date": {"$ref": "#/$defs/date"},
        "evidence_date_basis": {"enum": ["published", "posted", "updated", "observed_current"]},
        "evidence_text": {"type": "string", "minLength": 1},
        "source": {"$ref": "#/$defs/source"}
      }
    },
    "signal_evidence": {
      "type": "object",
      "additionalProperties": false,
      "required": ["signal", "evidence_url", "evidence_date", "evidence_date_basis", "evidence_text", "source"],
      "properties": {
        "signal": {"type": "string", "minLength": 1},
        "evidence_url": {"$ref": "#/$defs/url"},
        "evidence_date": {"$ref": "#/$defs/date"},
        "evidence_date_basis": {"enum": ["published", "posted", "updated", "observed_current"]},
        "evidence_text": {"type": "string", "minLength": 1},
        "source": {"$ref": "#/$defs/source"}
      }
    },
    "contact": {
      "type": "object",
      "additionalProperties": false,
      "required": ["full_name", "current_title", "requested_role", "role_match", "company", "domain", "contact_url", "evidence_url", "evidence_date", "evidence_date_basis", "evidence_text", "source"],
      "properties": {
        "full_name": {"type": "string", "minLength": 1},
        "current_title": {"type": "string", "minLength": 1},
        "requested_role": {"type": "string", "minLength": 1},
        "role_match": {"enum": ["exact", "normalized", "approved_family"]},
        "company": {"type": "string", "minLength": 1},
        "domain": {"type": "string", "minLength": 1},
        "contact_url": {"$ref": "#/$defs/url"},
        "evidence_url": {"$ref": "#/$defs/url"},
        "evidence_date": {"$ref": "#/$defs/date"},
        "evidence_date_basis": {"enum": ["published", "posted", "updated", "observed_current"]},
        "evidence_text": {"type": "string", "minLength": 1},
        "source": {"$ref": "#/$defs/source"},
        "email": {"type": "string", "format": "email", "minLength": 3},
        "phone": {"type": "string", "minLength": 3}
      }
    },
    "accepted_company": {
      "type": "object",
      "additionalProperties": false,
      "required": ["company", "account_fit", "signal_evidence", "primary_contact", "backup_contacts", "contact_candidate_count", "backup_shortfall"],
      "properties": {
        "company": {"$ref": "#/$defs/company"},
        "account_fit": {"$ref": "#/$defs/account_fit"},
        "signal_evidence": {"$ref": "#/$defs/signal_evidence"},
        "primary_contact": {"$ref": "#/$defs/contact"},
        "backup_contacts": {"type": "array", "maxItems": 2, "items": {"$ref": "#/$defs/contact"}},
        "contact_candidate_count": {"type": "integer", "minimum": 1, "maximum": 3},
        "backup_shortfall": {"type": "integer", "minimum": 0, "maximum": 2}
      }
    },
    "candidate": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "company": {"type": ["string", "null"]},
        "domain": {"type": ["string", "null"]},
        "requested_role": {"type": ["string", "null"]},
        "full_name": {"type": ["string", "null"]},
        "current_title": {"type": ["string", "null"]},
        "signal": {"type": ["string", "null"]},
        "evidence_url": {"anyOf": [{"$ref": "#/$defs/url"}, {"type": "null"}]},
        "provider": {"type": ["string", "null"]},
        "operation": {"type": ["string", "null"]},
        "tool": {"type": ["string", "null"]}
      }
    },
    "reason_code": {
      "enum": ["not_icp_fit", "stale_signal", "missing_account_evidence", "invalid_evidence_url", "invalid_evidence_date", "search_result_only", "profile_only", "duplicate_domain", "identity_conflict", "missing_name", "missing_current_title", "role_mismatch", "current_role_unverified", "company_mismatch", "missing_contact_evidence", "stale_contact_evidence", "duplicate_contact", "no_current_role_contact", "contact_target_shortfall", "route_not_connected", "budget_exhausted", "timeout_unknown", "provider_status", "gate_not_reached"]
    },
    "outcome_row": {
      "type": "object",
      "additionalProperties": false,
      "required": ["stage", "reason_code", "reason_text", "candidate"],
      "properties": {
        "stage": {"enum": ["account", "contact", "route"]},
        "reason_code": {"$ref": "#/$defs/reason_code"},
        "reason_text": {"type": "string", "minLength": 1},
        "candidate": {"$ref": "#/$defs/candidate"},
        "provider_status": {"$ref": "#/$defs/provider_status"},
        "route_id": {"type": "string", "minLength": 1}
      }
    },
    "route": {
      "type": "object",
      "additionalProperties": false,
      "required": ["route_id", "phase", "hypothesis", "provider", "operation", "request_summary", "pilot_max_rows", "paid_calls", "rows_returned", "rows_usable", "provider_status", "cost_credits"],
      "properties": {
        "route_id": {"type": "string", "minLength": 1},
        "phase": {"enum": ["account_discovery", "account_verification", "contact_discovery", "contact_verification"]},
        "hypothesis": {"type": "string", "minLength": 1},
        "provider": {"enum": ["deepline", "scrapingdog"]},
        "operation": {"type": "string", "minLength": 1},
        "tool": {"type": "string", "minLength": 1},
        "request_summary": {"type": "string", "minLength": 1},
        "pilot_max_rows": {"type": "integer", "minimum": 1, "maximum": 10},
        "paid_calls": {"type": "integer", "minimum": 0},
        "rows_returned": {"type": "integer", "minimum": 0},
        "rows_usable": {"type": "integer", "minimum": 0},
        "provider_status": {"$ref": "#/$defs/provider_status"},
        "cost_credits": {"type": ["number", "null"], "minimum": 0},
        "error": {"type": "string", "minLength": 1}
      }
    },
    "icp": {
      "type": "object",
      "additionalProperties": false,
      "minProperties": 1,
      "properties": {
        "company_types": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
        "industries": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
        "geographies": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
        "company_size": {
          "type": "object",
          "additionalProperties": false,
          "minProperties": 1,
          "properties": {
            "min_employees": {"type": "integer", "minimum": 0},
            "max_employees": {"type": "integer", "minimum": 0}
          }
        },
        "required_attributes": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
        "exclusions": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
        "custom_criteria": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}}
      }
    },
    "signal": {
      "type": "object",
      "additionalProperties": false,
      "required": ["kind"],
      "properties": {
        "kind": {"type": "string", "minLength": 1},
        "query": {"type": "string", "minLength": 1},
        "max_age_days": {"type": "integer", "minimum": 1},
        "source_preferences": {"type": "array", "items": {"type": "string", "minLength": 1}}
      }
    },
    "time_window": {
      "type": "object",
      "additionalProperties": false,
      "required": ["max_age_days"],
      "properties": {
        "max_age_days": {"type": "integer", "minimum": 1},
        "as_of_date": {"$ref": "#/$defs/date"}
      }
    },
    "input_budget": {
      "type": "object",
      "additionalProperties": false,
      "required": ["hard_stop"],
      "properties": {
        "deepline_credits": {"type": "number", "minimum": 0},
        "scrapingdog_credits": {"type": "number", "minimum": 0},
        "max_paid_calls": {"type": "integer", "minimum": 0},
        "hard_stop": {"const": true}
      },
      "anyOf": [
        {"required": ["deepline_credits"]},
        {"required": ["scrapingdog_credits"]}
      ]
    },
    "request_snapshot": {
      "type": "object",
      "additionalProperties": false,
      "required": ["target_count", "icp", "buying_signals", "requested_roles", "contacts_per_company", "time_window", "contact_fields", "budget"],
      "properties": {
        "target_count": {"type": "integer", "minimum": 1},
        "icp": {"$ref": "#/$defs/icp"},
        "buying_signals": {"type": "array", "minItems": 1, "items": {"$ref": "#/$defs/signal"}},
        "requested_roles": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
        "contacts_per_company": {"type": "integer", "minimum": 1, "maximum": 3},
        "time_window": {"$ref": "#/$defs/time_window"},
        "contact_fields": {"type": "array", "uniqueItems": true, "items": {"enum": ["email", "phone"]}},
        "budget": {"$ref": "#/$defs/input_budget"},
        "run_id": {"type": "string"},
        "as_of_date": {"$ref": "#/$defs/date"}
      }
    },
    "output_budget": {
      "type": "object",
      "additionalProperties": false,
      "required": ["limits", "spent", "paid_calls", "status"],
      "properties": {
        "limits": {
          "type": "object",
          "additionalProperties": false,
          "required": ["deepline_credits", "scrapingdog_credits", "max_paid_calls"],
          "properties": {
            "deepline_credits": {"type": ["number", "null"], "minimum": 0},
            "scrapingdog_credits": {"type": ["number", "null"], "minimum": 0},
            "max_paid_calls": {"type": ["integer", "null"], "minimum": 0}
          }
        },
        "spent": {
          "type": "object",
          "additionalProperties": false,
          "required": ["deepline_credits", "scrapingdog_credits"],
          "properties": {
            "deepline_credits": {"type": ["number", "null"], "minimum": 0},
            "scrapingdog_credits": {"type": ["number", "null"], "minimum": 0}
          }
        },
        "paid_calls": {"type": "integer", "minimum": 0},
        "status": {"enum": ["within_budget", "exhausted", "unknown"]}
      }
    },
    "summary": {
      "type": "object",
      "additionalProperties": false,
      "required": ["target_count", "accepted_companies", "accepted_contacts", "backup_contacts", "rejected_rows", "unresolved_rows"],
      "properties": {
        "target_count": {"type": "integer", "minimum": 1},
        "accepted_companies": {"type": "integer", "minimum": 0},
        "accepted_contacts": {"type": "integer", "minimum": 0},
        "backup_contacts": {"type": "integer", "minimum": 0},
        "rejected_rows": {"type": "integer", "minimum": 0},
        "unresolved_rows": {"type": "integer", "minimum": 0}
      }
    }
  }
}
```

The following semantic checks supplement JSON Schema: every accepted contact's
`domain` must equal its accepted company domain; `contact_candidate_count` must
equal one plus the number of backups; `backup_shortfall` must equal
`max(0, request.contacts_per_company - contact_candidate_count)`; each accepted
account domain must be unique; `account_fit` must support ICP fit;
`signal_evidence` must support the current signal and fall within the input
signal window; their evidence URLs and sources may differ; contact evidence
must explicitly support a current role at that company; `approved_family` must
be within the full user-approved role family and never an unapproved adjacent
function; and optional contact fields must be absent unless requested.
`provider_status` belongs to a route or outcome receipt, never in place of
`state`.

## `leads.csv` contract

Write UTF-8 CSV using RFC 4180 quoting. The header is fixed and ordered:

```text
state,run_id,company,domain,fit_claim,fit_evidence_url,fit_evidence_date,fit_evidence_text,signal,evidence_url,evidence_date,evidence_text,requested_role,primary_contact_name,primary_contact_title,primary_contact_url,contact_evidence_url,contact_evidence_date,contact_evidence_text,backup_contacts_json,email,phone
```

`leads.csv` is the clean flattened deliverable. Write exactly one row for each
accepted primary company-contact pair and no rows for rejected, unresolved, or
route outcomes. `state` is always the literal `accepted`; uniqueness is by
canonical domain. `fit_claim`, `fit_evidence_url`, `fit_evidence_date`, and
`fit_evidence_text` come from `account_fit`; `signal`, `evidence_url`,
`evidence_date`, and `evidence_text` come from `signal_evidence`.
`backup_contacts_json` is a JSON array of zero to two backup contacts. Rejected,
unresolved, and provider receipts remain in `results.json` and `report.md`.
`email` and `phone` are blank unless requested in `contact_fields`.

## `report.md` minimum contents

The Markdown report is the human audit receipt. Include the normalized request,
assumptions and as-of date; signal hypotheses and why routes differ; every
capability search/describe and execute receipt (without secrets); pilot limits,
rows, duplicates, costs, and live provider statuses; account and contact gate
decisions; primary/backups selection; adaptive reserve/refill decisions; all
accepted, rejected, and unresolved rows with stable reasons; contact target
shortfalls; and the final stop reason. Do not claim a discovery-candidate
endpoint ran, and do not include raw provider payloads or credentials.
