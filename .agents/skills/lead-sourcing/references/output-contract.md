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
   contact is found and record `backup_shortfall`. If no approved role passes,
   put the company in `unresolved` with `no_current_role_contact`. When role
   groups are present, a secondary-role contact is a valid fallback for that
   output slot.
4. `requested_roles` is always required. When `contact_role_groups` is present,
   it contains the deduplicated primary and secondary role lists whose union is
   `requested_roles`. Search and rank primary roles first; secondary roles are
   valid fallbacks and must not be rejected only because they are secondary. A
   selected contact may therefore be the output `primary_contact` with
   `role_group: "secondary"`.
5. `accepted`, `rejected`, and `unresolved` are output states. They are not
   provider statuses. A `no_results` provider response is not a rejection;
   `timeout`, quota, authentication, schema, and provider errors are
   unresolved outcomes.
6. Accepted companies are unique by lower-case canonical domain with a leading
   `www.` removed. A contact may appear once per accepted company. A backup is
   not a second primary.
7. `email` and `phone` are absent from JSON contact objects unless the input
   `contact_fields` requests them. When requested, every accepted primary
   contact must contain a non-empty valid value; otherwise the company remains
   unresolved. In CSV they are blank unless requested. Do not perform the
   lookup before the identity/current-role gate.
8. `target_count` is the completion condition. After account or contact
   attrition, source one replacement from a changed route while the accepted
   count is short and the route frontier is actionable. Do not prefetch or
   refill by a fixed multiplier such as 5x.
9. The route frontier contains paid-provider and public-web paths. Each path is
   `untried`, `continuable`, `exhausted`, or `blocked`. A final shortfall is
   invalid while any path is untried or continuable. A failed or uncertain paid
   call is not retried automatically, but it does not exhaust other paths. The
   frontier is append-only: paths may be added and states updated, but a planned
   or discovered path must not be removed.
10. All dates are ISO calendar dates. A relative provider date may be resolved
   from retrieval time only when the original wording is retained in
   `report.md`; never invent a date or a person.
11. `signal_match_mode` defaults to `any`. It applies across the entries in
   `buying_signals`; facts joined inside one signal query remain conjunctive.
   A signal's `min_age_days` and `max_age_days` are measured backwards from
   the effective as-of date. `min_age_days` is optional and defaults to zero;
   when both bounds are present, the minimum must not exceed the maximum.
12. A `qualification_check` uses `pass`, `fail`, or `unknown`. `unknown`
   means that public evidence is missing or ambiguous; it is never a
   substitute for `fail`. A required check that fails rejects the account. A
   required check that is unknown is unresolved. Preferred checks affect
   ranking and explanation but do not reject an otherwise qualified account.

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
    "signal_match_mode": {
      "enum": ["any", "all"],
      "default": "any"
    },
    "requested_roles": {
      "type": "array",
      "minItems": 1,
      "items": {"type": "string", "minLength": 1}
    },
    "contact_role_groups": {"$ref": "#/$defs/contact_role_groups"},
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
        "min_age_days": {"type": "integer", "minimum": 0},
        "max_age_days": {"type": "integer", "minimum": 1},
        "source_preferences": {"type": "array", "items": {"type": "string", "minLength": 1}}
      }
    },
    "contact_role_groups": {
      "type": "object",
      "additionalProperties": false,
      "required": ["primary", "secondary"],
      "properties": {
        "primary": {
          "type": "array",
          "minItems": 1,
          "uniqueItems": true,
          "items": {"type": "string", "minLength": 1}
        },
        "secondary": {
          "type": "array",
          "uniqueItems": true,
          "items": {"type": "string", "minLength": 1}
        }
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
    "stop_audit": {"$ref": "#/$defs/stop_audit"},
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
        "domain": {"type": "string", "minLength": 1},
        "website": {"$ref": "#/$defs/url"},
        "linkedin_url": {"$ref": "#/$defs/url"},
        "industry": {"type": "string", "minLength": 1},
        "sub_industry": {"type": "string", "minLength": 1},
        "hq_state": {"type": "string", "minLength": 1},
        "hq_country": {"type": "string", "minLength": 1},
        "employee_count": {"type": "integer", "minimum": 0},
        "description": {"type": "string", "minLength": 1}
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
    "evidence": {
      "type": "object",
      "additionalProperties": false,
      "required": ["url", "date", "date_basis", "text", "source"],
      "properties": {
        "url": {"$ref": "#/$defs/url"},
        "date": {"$ref": "#/$defs/date"},
        "date_basis": {"enum": ["published", "posted", "updated", "observed_current"]},
        "text": {"type": "string", "minLength": 1},
        "source": {"$ref": "#/$defs/source"}
      }
    },
    "qualification_check": {
      "type": "object",
      "additionalProperties": false,
      "required": ["criterion", "importance", "status", "claim", "evidence"],
      "properties": {
        "criterion": {"type": "string", "minLength": 1},
        "importance": {"enum": ["required", "preferred"]},
        "status": {"enum": ["pass", "fail", "unknown"]},
        "claim": {"type": "string", "minLength": 1},
        "evidence": {"type": "array", "items": {"$ref": "#/$defs/evidence"}}
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
        "role_group": {"enum": ["primary", "secondary"]},
        "company": {"type": "string", "minLength": 1},
        "domain": {"type": "string", "minLength": 1},
        "contact_url": {"$ref": "#/$defs/url"},
        "linkedin_url": {"$ref": "#/$defs/url"},
        "city": {"type": "string", "minLength": 1},
        "state": {"type": "string", "minLength": 1},
        "country": {"type": "string", "minLength": 1},
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
        "qualification_checks": {"type": "array", "items": {"$ref": "#/$defs/qualification_check"}},
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
      "enum": ["explicit_exclusion", "not_icp_fit", "stale_signal", "missing_account_evidence", "invalid_evidence_url", "invalid_evidence_date", "search_result_only", "profile_only", "duplicate_domain", "identity_conflict", "missing_name", "missing_current_title", "role_mismatch", "current_role_unverified", "company_mismatch", "missing_contact_evidence", "stale_contact_evidence", "duplicate_contact", "no_current_role_contact", "contact_target_shortfall", "route_not_connected", "budget_exhausted", "timeout_unknown", "provider_status", "gate_not_reached"]
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
        "qualification_checks": {"type": "array", "items": {"$ref": "#/$defs/qualification_check"}},
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
        "provider": {"enum": ["deepline", "scrapingdog", "public_web"]},
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
    "route_frontier_item": {
      "type": "object",
      "additionalProperties": false,
      "required": ["route_id", "phase", "provider", "operation", "request_summary", "state", "reason"],
      "properties": {
        "route_id": {"type": "string", "minLength": 1},
        "phase": {"enum": ["account_discovery", "account_verification", "contact_discovery", "contact_verification"]},
        "provider": {"enum": ["deepline", "scrapingdog", "public_web"]},
        "operation": {"type": "string", "minLength": 1},
        "request_summary": {"type": "string", "minLength": 1},
        "state": {"enum": ["untried", "continuable", "exhausted", "blocked"]},
        "exhaustion_basis": {"enum": ["no_results", "continuation_exhausted", "no_new_unique_candidates", "query_family_exhausted"]},
        "reason": {"type": "string", "minLength": 1}
      },
      "allOf": [
        {
          "if": {"properties": {"state": {"const": "exhausted"}}, "required": ["state"]},
          "then": {"required": ["exhaustion_basis"]}
        }
      ]
    },
    "provider_call_capacity": {
      "type": "object",
      "additionalProperties": false,
      "required": ["deepline", "scrapingdog", "paid_calls_remaining"],
      "properties": {
        "deepline": {"enum": ["available", "unavailable", "unknown"]},
        "scrapingdog": {"enum": ["available", "unavailable", "unknown"]},
        "paid_calls_remaining": {"type": ["integer", "null"], "minimum": 0}
      }
    },
    "stop_audit": {
      "type": "object",
      "additionalProperties": false,
      "required": ["target_shortfall", "candidate_companies_reviewed", "substantive_account_reviews", "exclusion_only_rejections", "duplicate_candidates", "frontier_complete", "provider_call_capacity", "route_frontier"],
      "properties": {
        "target_shortfall": {"type": "integer", "minimum": 0},
        "candidate_companies_reviewed": {"type": "integer", "minimum": 0},
        "substantive_account_reviews": {"type": "integer", "minimum": 0},
        "exclusion_only_rejections": {"type": "integer", "minimum": 0},
        "duplicate_candidates": {"type": "integer", "minimum": 0},
        "frontier_complete": {"const": true},
        "provider_call_capacity": {"$ref": "#/$defs/provider_call_capacity"},
        "route_frontier": {"type": "array", "items": {"$ref": "#/$defs/route_frontier_item"}}
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
        "min_age_days": {"type": "integer", "minimum": 0},
        "max_age_days": {"type": "integer", "minimum": 1},
        "source_preferences": {"type": "array", "items": {"type": "string", "minLength": 1}}
      }
    },
    "contact_role_groups": {
      "type": "object",
      "additionalProperties": false,
      "required": ["primary", "secondary"],
      "properties": {
        "primary": {
          "type": "array",
          "minItems": 1,
          "uniqueItems": true,
          "items": {"type": "string", "minLength": 1}
        },
        "secondary": {
          "type": "array",
          "uniqueItems": true,
          "items": {"type": "string", "minLength": 1}
        }
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
        "signal_match_mode": {"enum": ["any", "all"], "default": "any"},
        "requested_roles": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
        "contact_role_groups": {"$ref": "#/$defs/contact_role_groups"},
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

The following semantic checks supplement JSON Schema: every signal's
`min_age_days` must be no greater than its `max_age_days` when both are
present; every accepted contact's
`domain` must equal its accepted company domain; `contact_candidate_count` must
equal one plus the number of backups; `backup_shortfall` must equal
`max(0, request.contacts_per_company - contact_candidate_count)`; each accepted
account domain must be unique; `account_fit` must support ICP fit;
`signal_evidence` must support a signal selected by `signal_match_mode` and
fall within that signal's bounds (or the input time window when a signal bound
is absent); their evidence URLs and sources may differ; contact evidence must
explicitly support a current role at that company; `approved_family` must be
within the full user-approved role family and never an unapproved adjacent
function; every accepted contact's `requested_role` must be in
`request.requested_roles`; and optional contact fields must be absent unless
requested. Validate `primary_contact` and every item in `backup_contacts` with
the same role and role-group rules. When
`request.contact_role_groups` is present, its `primary` and
`secondary` arrays must have `request.requested_roles` as their deduplicated
union. Search and rank primary roles before secondary roles, but a valid
secondary-role contact remains eligible when no primary-role contact passes;
it must not create a false negative. If `role_group` is present, it must match
the group containing `requested_role`; omit it when the group is unknown or
the legacy request has no role groups. When
`qualification_checks` is present, required checks with `fail` reject the
account, required checks with `unknown` make it unresolved, and preferred
checks do not reject an account. A check with `unknown` must not be rewritten
as `fail` merely because no source was found.
`provider_status` belongs to a route or outcome receipt, never in place of
`state`. When accepted companies are below `target_count`, `stop_audit` is
required and every route-frontier item must be `exhausted` or `blocked` before
the run may end, and `frontier_complete` must be true. Every `exhausted`
frontier item must have a determinate `ok`, `partial`, or `no_results` attempt
receipt in `routes` and an `exhaustion_basis`. A rate limit, authentication,
quota, timeout, schema, provider, or configuration error makes a route
`blocked`, never `exhausted`. Every `blocked` item must have either a blocking
attempt receipt or an unresolved route-outcome receipt;
this records routes that cannot start because of budget, connection, or
configuration. Every item in `routes`, including a no-cost `public_web` query,
must have the same `route_id` in the frontier. `route_id` identifies one route path:
it may appear once in `routes`, once among stage=`route` outcomes, and once in
`route_frontier`. A route receipt and a route outcome may share an ID only when
the receipt itself has a blocking provider status and the outcome records that
same failed attempt. A determinate `ok`, `partial`, or `no_results` receipt must
not share its ID with a later blocked continuation; that continuation needs a
new route ID. Reuse for separate attempts is invalid. `target_shortfall`
equals
`max(0, target_count - accepted_companies)`. Reviewed-company counts use unique
canonical domains, or normalized company names when a domain is not yet known;
`explicit_exclusion` is the only exclusion-only reason. `budget_exhausted` is
valid only when both paid providers have `unavailable` call capacity and no
public-web route remains actionable. `provider_stop` requires at least one
blocked route and is invalid while either paid provider can make another
bounded call. `no_productive_route` requires at least one exhausted route; use
`provider_stop` when every route is blocked. Run `scripts/validate_run.py` against the
completed `results.json` to enforce these completion rules. This completion
validator supplements rather than replaces validation against the JSON Schema
and the other semantic checks above.

Budget accounting uses actual provider usage, not planning estimates. Output
`paid_calls` must equal the sum of route `paid_calls`. For each provider, a
numeric `spent` value must equal the sum of its numeric route `cost_credits`.
If any paid route has unknown cost, that provider's `spent` value, its call
capacity, and the overall budget status must be `unknown`. `within_budget`
requires known actual spend for both providers. Known spend or paid calls above
a hard limit are invalid. Put planning estimates in the report or route
explanation, not in actual-spend fields.

## `leads.csv` contract

Write UTF-8 CSV using RFC 4180 quoting. The header is fixed and ordered:

```text
Name,Email,Role,Company,LinkedIn,Website,Company LinkedIn,Industry,Sub Industry,City,State,Country,HQ State,HQ Country,Employee Count,Description,Intent Details,Phone
```

`leads.csv` is the clean flattened deliverable. Write exactly one row for each
accepted primary company-contact pair and no rows for rejected, unresolved, or
route outcomes. Uniqueness is by canonical domain. Use these exact mappings:

| CSV column | `results.json` source |
|---|---|
| `Name` | `primary_contact.full_name` |
| `Email` | `primary_contact.email`, otherwise blank |
| `Role` | `primary_contact.current_title` |
| `Company` | `company.canonical_name` |
| `LinkedIn` | `primary_contact.linkedin_url`; use `contact_url` only when it is a LinkedIn URL |
| `Website` | `company.website`; otherwise `https://` plus the canonical `company.domain` |
| `Company LinkedIn` | `company.linkedin_url`, otherwise blank |
| `Industry` | `company.industry`, otherwise blank |
| `Sub Industry` | `company.sub_industry`, otherwise blank |
| `City` | `primary_contact.city`, otherwise blank |
| `State` | `primary_contact.state`, otherwise blank |
| `Country` | `primary_contact.country`, otherwise blank |
| `HQ State` | `company.hq_state`, otherwise blank |
| `HQ Country` | `company.hq_country`, otherwise blank |
| `Employee Count` | `company.employee_count`, otherwise blank; never turn a range into an exact count |
| `Description` | `company.description`, otherwise blank |
| `Intent Details` | signal name, date, evidence text, and source URL from `signal_evidence` |
| `Phone` | `primary_contact.phone`, otherwise blank |

Rejected, unresolved, backup contacts, provider receipts, fit evidence, and the
full signal-evidence structure remain in `results.json` and `report.md` instead
of widening the sales-ready CSV. `Email` and `Phone` are blank unless the input
requests them and a verified value is available. Generate the file with
`scripts/export_csv.py` so the spelling, order, and quoting stay deterministic.

## `report.md` minimum contents

The Markdown report is the human audit receipt. Include the normalized request,
assumptions and as-of date; signal hypotheses and why routes differ; every
capability search/describe and execute receipt (without secrets); pilot limits,
rows, duplicates, costs, and live provider statuses; account and contact gate
decisions; primary/backups selection; adaptive reserve/refill decisions; all
accepted, rejected, and unresolved rows with stable reasons; contact target
shortfalls; the route frontier and call capacity; reviewed-company counts; and
the final stop reason. Do not claim a discovery-candidate endpoint ran, and do
not include raw provider payloads or credentials.
