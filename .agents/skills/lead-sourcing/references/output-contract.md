# TYCHE output contract

This is the normative, machine-readable contract for one lead-sourcing run.
The JSON Schema is draft 2020-12. A run directory is
`reports/<run-id>/` and delivers `report.md`, `results.json`, and `leads.xlsx`.
Keep provider receipts and the internal `results.json.budget.json` execution
ledger alongside these deliverables; they do not change the result schema.

## Read by phase

This file stays the single normative contract. Do not load it all upfront:
locate the relevant heading and read its section. Schemas are unchanged;
inspect the definitions needed for the record being written, then validate the
complete artifact. All applicable semantic rules still apply.

| When | Sections |
|---|---|
| Before discovery | [Lifecycle invariants](#lifecycle-invariants), [Input contract](#input-contract), [Timing](#timing). |
| Before the first route receipt | [Semantic checks](#semantic-checks), [Accepted-lead sources](#accepted-lead-sources), relevant [result schema](#resultsjson-schema) fields. |
| Before accepting and describing a company | [Client writing and taxonomy](#client-writing-and-taxonomy-version-12). |
| Before export and final delivery | [Workbook](#leadsxlsx-contract), [Report minimum contents](#reportmd-minimum-contents), [Final-response checklist](#final-response-checklist); finish [Timing](#timing). |

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
7. `contact_fields` defaults to `["email"]`. An explicit empty array opts out
   of contact data, and an explicit `["phone"]` requests only a phone number.
   Fields outside the effective request are absent from JSON contact objects.
   Every accepted primary contact must contain each requested field; otherwise
   the company remains unresolved. Every stored email must have a matching
   Deepline ZeroBounce validation receipt. Only an explicit ZeroBounce status
   of `valid` passes directly (trimmed, case-insensitive). For catch-all/unknown
   or a recorded ZeroBounce service failure, one successful BounceBan deliverable fallback may pass with both
   receipts preserved. Reject risky
   statuses (`invalid`, `do_not_mail`, `spamtrap`, `abuse`); other statuses
   remain unresolved. Use `email_invalid` for rejected email outcomes. A
   missing status, missing receipt, failed call, or uncertain provider outcome
   cannot pass by itself. In the workbook unrequested fields are blank. Do not perform
   contact-data lookup or email validation before the identity/current-role
   gate.
8. `target_count` is the completion condition. After account or contact
   attrition, source one replacement from a changed route while the accepted
   count is short and the route frontier is actionable. Do not prefetch or
   refill by a fixed multiplier such as 5x.
9. The route frontier contains paid-provider and public-web paths. Each path is
   `untried`, `continuable`, `exhausted`, or `blocked`. A final shortfall is
   invalid while any path is actionable unless the strict stop check verifies
   an explicit time limit or that no next action fits the budget. Keep unfinished
   routes actionable in those cases; do not relabel them exhausted. A failed or uncertain paid
   call is not retried automatically, but it does not exhaust other paths. The
   frontier is append-only: paths may be added and states updated, but a planned
   or discovered path must not be removed.
10. All dates are ISO calendar dates. A relative provider date may be resolved
   from retrieval time only when the original wording is retained in
   `report.md`; never invent a date or a person.
11. `signal_match_mode` defaults to `any`. For required intent, apply it
   across required entries in `buying_signals`; facts within a query stay
   conjunctive. Apply the [qualification policy](workflow-rules.md#qualification-policy).
   Preserve optional hypotheses in the request and mark their existing
   qualification checks preferred; do not silently promote them to must-haves.
   When none are supplied, record a preferred use-case hypothesis in
   `buying_signals`, clearly identified as inferred rather than a user requirement.
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
Before schema validation, apply the [default run budget](workflow-rules.md#default-run-budget)
when the user omits a spending budget: USD 0.50 times the requested lead count,
shared across providers. Convert a conservative allocation to the existing
credit-cap fields; record its USD basis in the report. Explicit budgets override
this default. No new JSON fields are required.

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
    "max_duration_seconds": {"type": ["integer", "null"], "minimum": 1},
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
      "default": ["email"]
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
        "max_paid_calls": {"type": "integer", "minimum": 0, "deprecated": true, "description": "Legacy metadata only; ignored. Omit in new runs."},
        "max_deepline_credits_per_next_lead": {"type": "number", "minimum": 0},
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
true. Stop limits are monetary budgets and explicit time limits, never call
counts. New runs omit `max_paid_calls` and `paid_calls_remaining`; these legacy
fields remain accepted but are ignored. Catalog search/describe calls are
read-only. Charge each paid call against its spending caps and record the count
in `paid_calls` for audit. Resuming an old ledger preserves every call, charge,
reservation, and monetary limit; its old `max_paid_calls` value has no effect.
If a provider does not expose usage, set its output `spent` and route
`cost_credits` to `null`; never use `0` to mean unknown. New runs use result
schema version `1.2`. Versions `1.0` and `1.1` remain valid for existing artifacts.

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
    "schema_version": {"enum": ["1.0", "1.1", "1.2"]},
    "run_id": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$"},
    "retrieved_at": {"type": "string", "format": "date-time"},
    "request": {"$ref": "#/$defs/request_snapshot"},
    "budget": {"$ref": "#/$defs/output_budget"},
    "routes": {"type": "array", "items": {"$ref": "#/$defs/route"}},
    "cost_summary": {"$ref": "#/$defs/cost_summary"},
    "summary": {"$ref": "#/$defs/summary"},
    "accepted": {"type": "array", "items": {"$ref": "#/$defs/accepted_company"}},
    "rejected": {"type": "array", "items": {"$ref": "#/$defs/outcome_row"}},
    "unresolved": {"type": "array", "items": {"$ref": "#/$defs/outcome_row"}},
    "stop_audit": {"$ref": "#/$defs/stop_audit"},
    "stop_check": {"$ref": "#/$defs/stop_check"},
    "stop_reason": {
      "enum": ["target_met", "budget_exhausted", "time_limit_reached", "no_productive_route", "provider_stop", "input_or_configuration_stop"]
    }
  },
  "allOf": [
    {
      "if": {
        "properties": {"schema_version": {"enum": ["1.1", "1.2"]}},
        "required": ["schema_version"]
      },
      "then": {
        "required": ["cost_summary"],
        "properties": {
          "routes": {
            "items": {
              "required": ["cost_credits", "cost_upper_bound_credits", "cost_basis"]
            }
          }
        }
      }
    },
    {
      "if": {
        "properties": {"schema_version": {"const": "1.2"}},
        "required": ["schema_version"]
      },
      "then": {
        "properties": {
          "accepted": {
            "items": {
              "required": ["intent_details"],
              "properties": {
                "company": {
                  "required": ["description"],
                  "anyOf": [
                    {"required": ["industry", "sub_industry"]},
                    {
                      "required": ["classification_note"],
                      "not": {"anyOf": [{"required": ["industry"]}, {"required": ["sub_industry"]}]}
                    }
                  ]
                }
              }
            }
          }
        }
      }
    }
  ],
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
      "required": ["canonical_name", "domain", "employee_range", "employee_range_evidence"],
      "properties": {
        "canonical_name": {"type": "string", "minLength": 1},
        "domain": {"type": "string", "minLength": 1},
        "website": {"$ref": "#/$defs/url"},
        "linkedin_url": {"$ref": "#/$defs/url"},
        "industry": {"type": "string", "minLength": 1},
        "sub_industry": {"type": "string", "minLength": 1},
        "classification_note": {"type": "string", "pattern": "\\S"},
        "hq_state": {"type": "string", "minLength": 1},
        "hq_country": {"type": "string", "minLength": 1},
        "employee_count": {"type": "integer", "minimum": 0},
        "employee_range": {"type": "string", "minLength": 3},
        "employee_range_evidence": {"$ref": "#/$defs/linkedin_field_evidence"},
        "owner_group": {"type": "string", "minLength": 1},
        "aliases": {"type": "array", "items": {"type": "string", "minLength": 1}},
        "description": {"type": "string", "minLength": 1}
      }
    },
    "linkedin_field_evidence": {
      "type": "object",
      "additionalProperties": false,
      "required": ["evidence_url", "evidence_date", "evidence_date_basis", "evidence_text", "source"],
      "properties": {
        "evidence_url": {"$ref": "#/$defs/url"},
        "evidence_date": {"$ref": "#/$defs/date"},
        "evidence_date_basis": {"const": "observed_current"},
        "evidence_text": {"type": "string", "minLength": 1},
        "source": {"$ref": "#/$defs/source"}
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
        "signal": {"type": "string", "pattern": "\\S"},
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
    "bounceban_validation": {
      "type": "object",
      "additionalProperties": false,
      "required": ["email", "status", "result", "source"],
      "properties": {
        "email": {"type": "string", "format": "email", "minLength": 3},
        "status": {"type": "string", "minLength": 1},
        "result": {"type": "string", "minLength": 1},
        "score": {"type": ["number", "null"]},
        "processed_at": {"type": ["string", "null"]},
        "source": {
          "type": "object",
          "additionalProperties": false,
          "required": ["provider", "validator", "operation", "tool", "route_id"],
          "properties": {
            "provider": {"const": "deepline"},
            "validator": {"const": "bounceban"},
            "operation": {"const": "execute"},
            "tool": {"type": "string", "minLength": 1},
            "route_id": {"type": "string", "minLength": 1}
          }
        }
      }
    },
    "email_validation": {
      "type": "object",
      "additionalProperties": false,
      "required": ["email", "status", "source"],
      "allOf": [
        {"if": {"properties": {"status": {"type": "null"}}, "required": ["status"]}, "then": {"required": ["provider_status"]}},
        {"if": {"required": ["provider_status"]}, "then": {"properties": {"status": {"type": "null"}}}}
      ],
      "properties": {
        "email": {"type": "string", "format": "email", "minLength": 3},
        "status": {"type": ["string", "null"], "minLength": 1},
        "provider_status": {"enum": ["provider_error", "timeout", "rate_limited", "auth_failed", "quota_exceeded"]},
        "sub_status": {"type": ["string", "null"]},
        "fallback": {"$ref": "#/$defs/bounceban_validation"},
        "processed_at": {"type": ["string", "null"]},
        "source": {
          "type": "object",
          "additionalProperties": false,
          "required": ["provider", "validator", "operation", "tool", "route_id"],
          "properties": {
            "provider": {"const": "deepline"},
            "validator": {"const": "zerobounce"},
            "operation": {"const": "execute"},
            "tool": {"type": "string", "minLength": 1},
            "route_id": {"type": "string", "minLength": 1}
          }
        }
      }
    },
    "contact": {
      "type": "object",
      "additionalProperties": false,
      "required": ["full_name", "current_title", "requested_role", "role_match", "company", "domain", "contact_url", "country", "location_evidence", "evidence_url", "evidence_date", "evidence_date_basis", "evidence_text", "source"],
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
        "location_evidence": {"$ref": "#/$defs/linkedin_field_evidence"},
        "evidence_url": {"$ref": "#/$defs/url"},
        "evidence_date": {"$ref": "#/$defs/date"},
        "evidence_date_basis": {"enum": ["published", "posted", "updated", "observed_current"]},
        "evidence_text": {"type": "string", "minLength": 1},
        "source": {"$ref": "#/$defs/source"},
        "email": {"type": "string", "format": "email", "minLength": 3},
        "email_validation": {"$ref": "#/$defs/email_validation"},
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
        "intent_details": {"type": "string", "pattern": "\\S"},
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
        "email": {"type": ["string", "null"]},
        "email_validation": {"anyOf": [{"$ref": "#/$defs/email_validation"}, {"type": "null"}]},
        "signal": {"type": ["string", "null"]},
        "evidence_url": {"anyOf": [{"$ref": "#/$defs/url"}, {"type": "null"}]},
        "provider": {"type": ["string", "null"]},
        "operation": {"type": ["string", "null"]},
        "tool": {"type": ["string", "null"]}
      }
    },
    "reason_code": {
      "enum": ["explicit_exclusion", "not_icp_fit", "stale_signal", "missing_account_evidence", "invalid_evidence_url", "invalid_evidence_date", "search_result_only", "profile_only", "duplicate_domain", "identity_conflict", "missing_name", "missing_current_title", "role_mismatch", "current_role_unverified", "company_mismatch", "missing_contact_evidence", "stale_contact_evidence", "duplicate_contact", "missing_email", "email_invalid", "email_validation_unresolved", "no_current_role_contact", "contact_target_shortfall", "route_not_connected", "budget_exhausted", "timeout_unknown", "provider_status", "gate_not_reached"]
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
        "route_id": {"type": "string", "minLength": 1},
        "scope": {"type": "string", "minLength": 1},
        "provider": {"enum": ["deepline", "scrapingdog", "public_web"]}
      }
    },
    "route": {
      "type": "object",
      "additionalProperties": false,
      "required": ["route_id", "phase", "hypothesis", "provider", "operation", "request_summary", "pilot_max_rows", "paid_calls", "rows_returned", "rows_usable", "provider_status", "cost_credits"],
      "properties": {
        "route_id": {"type": "string", "minLength": 1},
        "phase": {"enum": ["account_discovery", "account_verification", "contact_discovery", "contact_verification", "email_validation"]},
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
        "cost_upper_bound_credits": {"type": ["number", "null"], "minimum": 0},
        "cost_basis": {"enum": ["actual", "estimated", "unknown"]},
        "accepted_leads_before_call": {"type": "integer", "minimum": 0},
        "scope": {"type": "string", "minLength": 1},
        "approach": {"type": "string", "minLength": 1},
        "request_fingerprint": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
        "progress_before": {"type": "array", "items": {"type": "string"}, "uniqueItems": true},
        "status_read": {"type": "boolean"},
        "entity_type": {"type": "string", "minLength": 1},
        "error": {"type": "string", "minLength": 1}
      }
    },
    "stop_check": {
      "type": "object",
      "additionalProperties": false,
      "required": ["started_at", "next_actions"],
      "properties": {
        "started_at": {"type": "string", "format": "date-time"},
        "catalog_review_route_ids": {"type": "array", "items": {"type": "string", "minLength": 1}, "uniqueItems": true},
        "next_actions": {"type": "array", "items": {"$ref": "#/$defs/next_action"}}
      }
    },
    "next_action": {
      "type": "object",
      "additionalProperties": false,
      "required": ["id", "scope", "description", "provider", "paid_calls", "cost_upper_bound_credits"],
      "properties": {
        "id": {"type": "string", "minLength": 1},
        "phase": {"enum": ["account_discovery", "account_verification", "contact_discovery", "contact_verification", "email_validation"]},
        "approach": {"type": "string", "minLength": 1},
        "status_read": {"type": "boolean"},
        "operation": {"type": "string", "minLength": 1},
        "tool": {"type": "string", "minLength": 1},
        "request_fingerprint": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
        "scope": {"type": "string", "minLength": 1},
        "description": {"type": "string", "minLength": 1},
        "provider": {"enum": ["deepline", "scrapingdog", "public_web"]},
        "paid_calls": {"type": "integer", "minimum": 0},
        "cost_upper_bound_credits": {"type": ["number", "null"], "minimum": 0},
        "entity_type": {"type": "string", "minLength": 1},
        "blocker": {
          "type": "object",
          "additionalProperties": false,
          "required": ["kind", "reason", "evidence_route_id"],
          "properties": {
            "kind": {"enum": ["approval_required", "access_unavailable", "required_input"]},
            "reason": {"type": "string", "minLength": 1},
            "evidence_route_id": {"type": "string", "minLength": 1}
          }
        }
      }
    },
    "route_frontier_item": {
      "type": "object",
      "additionalProperties": false,
      "required": ["route_id", "phase", "provider", "operation", "request_summary", "state", "reason"],
      "properties": {
        "status_read": {"type": "boolean"},
        "scope": {"type": "string", "minLength": 1},
        "approach": {"type": "string", "minLength": 1},
        "request_fingerprint": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
        "entity_type": {"type": "string", "minLength": 1},
        "route_id": {"type": "string", "minLength": 1},
        "phase": {"enum": ["account_discovery", "account_verification", "contact_discovery", "contact_verification", "email_validation"]},
        "provider": {"enum": ["deepline", "scrapingdog", "public_web"]},
        "operation": {"type": "string", "minLength": 1},
        "request_summary": {"type": "string", "minLength": 1},
        "state": {"enum": ["untried", "continuable", "exhausted", "blocked"]},
        "exhaustion_basis": {"enum": ["no_results", "continuation_exhausted", "no_new_unique_candidates", "query_family_exhausted"]},
        "continuation_route_ids": {"type": "array", "items": {"type": "string", "minLength": 1}, "uniqueItems": true},
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
      "required": ["deepline", "scrapingdog"],
      "properties": {
        "deepline": {"enum": ["available", "unavailable", "unknown"]},
        "scrapingdog": {"enum": ["available", "unavailable", "unknown"]},
        "paid_calls_remaining": {"type": ["integer", "null"], "minimum": 0, "deprecated": true, "description": "Legacy metadata only; ignored. Omit in new runs."}
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
        "max_paid_calls": {"type": "integer", "minimum": 0, "deprecated": true, "description": "Legacy metadata only; ignored. Omit in new runs."},
        "max_deepline_credits_per_next_lead": {"type": "number", "minimum": 0},
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
        "max_duration_seconds": {"type": ["integer", "null"], "minimum": 1},
        "icp": {"$ref": "#/$defs/icp"},
        "buying_signals": {"type": "array", "minItems": 1, "items": {"$ref": "#/$defs/signal"}},
        "signal_match_mode": {"enum": ["any", "all"], "default": "any"},
        "requested_roles": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
        "contact_role_groups": {"$ref": "#/$defs/contact_role_groups"},
        "contacts_per_company": {"type": "integer", "minimum": 1, "maximum": 3},
        "time_window": {"$ref": "#/$defs/time_window"},
        "contact_fields": {"type": "array", "uniqueItems": true, "items": {"enum": ["email", "phone"]}, "default": ["email"]},
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
          "required": ["deepline_credits", "scrapingdog_credits"],
          "properties": {
            "deepline_credits": {"type": ["number", "null"], "minimum": 0},
            "scrapingdog_credits": {"type": ["number", "null"], "minimum": 0},
            "max_paid_calls": {"type": ["integer", "null"], "minimum": 0, "deprecated": true, "description": "Legacy metadata only; ignored. Omit in new runs."},
            "max_deepline_credits_per_next_lead": {"type": "number", "minimum": 0}
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
    "provider_credit_cost": {
      "type": "object",
      "additionalProperties": false,
      "required": ["confirmed_credits", "maximum_credits"],
      "properties": {
        "confirmed_credits": {"type": "number", "minimum": 0},
        "maximum_credits": {"type": ["number", "null"], "minimum": 0}
      }
    },
    "deepline_cost": {
      "type": "object",
      "additionalProperties": false,
      "required": ["usd_per_credit", "confirmed_credits", "maximum_credits", "confirmed_usd", "maximum_usd"],
      "properties": {
        "usd_per_credit": {"const": 0.1},
        "confirmed_credits": {"type": "number", "minimum": 0},
        "maximum_credits": {"type": ["number", "null"], "minimum": 0},
        "confirmed_usd": {"type": "number", "minimum": 0},
        "maximum_usd": {"type": ["number", "null"], "minimum": 0}
      }
    },
    "cost_per_lead": {
      "type": "object",
      "additionalProperties": false,
      "required": ["minimum", "maximum"],
      "properties": {
        "minimum": {"type": ["number", "null"], "minimum": 0},
        "maximum": {"type": ["number", "null"], "minimum": 0}
      }
    },
    "cost_summary": {
      "type": "object",
      "additionalProperties": false,
      "required": ["status", "accepted_leads", "deepline", "scrapingdog", "deepline_cost_per_lead_usd"],
      "properties": {
        "status": {"enum": ["exact", "estimated_range", "unknown"]},
        "accepted_leads": {"type": "integer", "minimum": 0},
        "deepline": {"$ref": "#/$defs/deepline_cost"},
        "scrapingdog": {"$ref": "#/$defs/provider_credit_cost"},
        "deepline_cost_per_lead_usd": {"$ref": "#/$defs/cost_per_lead"}
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

### Semantic checks

### Stopping check

For every current run, persist `stop_check.started_at` before discovery and keep
it unchanged on resume. Set `request.max_duration_seconds` only from an explicit
user time limit; absent/null means no time limit. The limit includes discovery,
retries and verification, not just paid tool execution. At expiry, stop sourcing
and finish the necessary persistence and delivery checks. Never promise that an
already dispatched provider call can be cancelled; retain its reservation/result.

Maintain `stop_check.next_actions` separately from historical attempt receipts.
Each entry names a concrete, useful next test, with a unique `id`, `description`,
`scope`, `provider`, `paid_calls` and conservative `cost_upper_bound_credits`.
For an email-validation action, also set `entity_type: "email_validation"`,
matching its adapter request so the protected verification balance is usable.
Use `scope: "discovery"` for finding additional companies and the canonical
domain for each unresolved account/contact (or its normalized `name:` key when
no domain exists). Cover discovery and active company checks. A company can be
parked without another action when its latest substantive attempt is `ok` or
`no_results`, its frontier entry is `exhausted` with a reason and exhaustion
basis, and every unresolved `reason_text` for it records the remaining gaps.
Account gaps require an account discovery/verification review; contact gaps
require a contact discovery/verification or email-validation review. Retain all
receipts and qualification checks. Parking does not qualify or reject a company,
hide an actionable frontier entry, or establish whole-run exhaustion.
Remove speculative follow-ups; reopen only for a concrete new source or
capability with a different stable approach, explained in the action description.

The attempt helper adds optional audit metadata without changing client output:
`scope`, stable `approach`, `request_fingerprint`, and verified `progress_before`
milestones. Two completed non-catalog attempts without new verified milestones
require a different approach. A provider switch alone is not a strategy change.
Version labels such as `-v117` do not change the approach. Parked-company actions
cannot repeat a completed approach or request fingerprint; discovering new
evidence requires an actual source/target change, not a rephrased label.
Actionable frontier entries must retain a matching next action or continuation;
covering only the generic discovery scope cannot hide an untried research path.

Before a shortfall, refresh live tool discovery for both new companies and
missing evidence. Include public/free alternatives, not only paid providers.
Do not repeat a failed identical lookup, spend solely to consume the cap, or
invent a blocker from a missing headcount, rejected company, or failed email.
Try another relevant identifier, source family, buyer, or company as appropriate.

An action that cannot run may carry `blocker` with `kind` (`approval_required`,
`access_unavailable`, or `required_input`), the concrete `reason`, and an
`evidence_route_id` referencing a blocking attempt or route-outcome receipt.
That blocks only that action. A whole-run blocker requires coverage of all
remaining discovery/recovery scopes after alternative-source review. Unknown
prices require a free price-discovery action, not an invented budget failure.
Provider budget allocations may be changed only under the existing shared-cap
rules; lack of allocation to an otherwise useful provider is not tool exhaustion.

Blocker receipts must match the action's provider, scope and tool when specified.
A successful later call on that same route/tool or its continuation invalidates
the old error as a stopping reason. Before a budget/provider/input shortfall,
`catalog_review_route_ids` must reference live catalog search attempt receipts
from this run; reuse an applicable saved review. One discovery review can cover
all remaining evidence gaps; do not repeat identical catalog queries per company.
Recovery actions and blockers still need company-specific coverage.
The helper tags catalog calls `entity_type: "tool_catalog"`. Review the returned
capabilities and add useful alternatives; a catalog receipt is not itself proof
that alternatives were exhausted. If catalog access fails, preserve the failure
and review other accessible sources; an outage must not force endless refreshes
or block another provider. Target/time stops do not require another search.

Company `owner_group` and `aliases` are optional, evidence-backed identities for
exclusions and deduplication, not inferred corporate relationships. Required
qualification checks marked unknown remain account-unresolved. A current
`not_icp_fit` rejection needs an evidenced failed required check.

Before recording `approval_required`, apply the
[authorization rules](../SKILL.md#authorization) and check the current request,
prior user approvals, and trusted application job context. A contact email or
change of provider is not itself a missing approval. Cite the exact applicable
instruction or actual runtime denial and preserve its source in the report;
an agent-authored blocker is not proof of a platform refusal. On resume, remove
resolved blockers from next actions while retaining historical receipts. An
email-validation denial does not block company discovery or other permitted
work. The validator checks the recorded blocker structure and references; it
cannot independently verify user authorization or a runtime denial.

The attempt and review helpers return the current stop decision; use that result.
Run `python3 scripts/validate_run.py <results.json> --check-stop` only for work
outside those helpers or recovery. Draft results are allowed; budget/cost receipts must reconcile. The
decision uses qualified accepted rows, the actual current UTC time, confirmed
charges plus uncertain reservations, and each next action's maximum cost/calls.
When an execution ledger is present, this uses the same shared-USD and
verification-allowance calculation as dispatch. Eligibility is a snapshot;
the adapter must still reserve atomically before sending the call. Decisions:

- `target_met`: requested qualified company-contact count reached.
- `time_limit_reached`: the user's explicit duration expired. Target takes
  precedence if already reached. Report any shortfall and unfinished routes.
- `continue`: at least one action fits, discovery/recovery coverage is missing,
  or pricing needs resolution. Dispatch only an `eligible_actions` entry. With
  missing coverage/prices, add the concrete free planning/research action first.
- `budget_exhausted`: all covered, unblocked next actions exceed an applicable
  cap. Never exceed a cap first. A free available action prevents this stop.
- `provider_stop` or `input_or_configuration_stop`: all covered next actions
  have evidenced concrete blockers; request only what is needed to unblock them.
- `no_productive_route`: an explicit, reviewed partial outcome. No useful next
  actions remain and every frontier item is resolved. The last two substantive
  discovery attempts used different documented approaches and distinct saved
  request fingerprints, have completed `ok`/`no_results` receipts and evidenced
  exhausted frontier entries, and added no qualified account or accepted lead.
  Renaming the same query is not another approach. This is a minimum check, not
  automatic permission to stop after any two searches: review the requested
  countries and ICP segments and finish promising paths before closing.
  Each unresolved company needs a completed, exhausted substantive review and
  `reason_text` explaining its missing evidence and why no useful action remains.
  A review may add facts without resolving the buyer; do not demand two extra
  empty calls per company. Reuse this run's catalog review while its capabilities
  still apply; ordinary research does not expire it. Refresh for changed needs
  or availability, and still verify the live price/schema before spending.
  Preserve existing receipts and snapshots. This reports the reviewed search's
  shortfall, never proof that the market contains no other matching companies.
- `repair_state`: invalid/missing state. Repair it; this is not a sourcing outcome.

After choosing an eligible paid action, persist its reservation before dispatch.
Evaluate again after its result and on resume. Do not reset caps or start times.
Final `validate_run.py` is strict by default: it requires `stop_check` and a
matching `stop_reason`, in addition to all evidence and budget validation.
The schema keeps `stop_check` optional solely for old reports;
`--legacy-stop-policy` is for read-only historical audits, never current delivery.
An empty next-action list alone never establishes `no_productive_route`.
Checks validate recorded actions and receipts; they cannot prove completeness of
an open-ended market search. The agent must still honestly discover alternatives
and substantiate blockers, rather than manipulate labels to obtain a passing result.

#### Turn completion and recovery

Treat `continue` as an instruction to execute the next useful eligible action within
the current turn, or complete the evidenced exhaustion review when no useful
next action remains. Use commentary for intermediate results, including a saved
partial workbook; do not end the turn with a partial delivery or an offer to
continue. When the decision is `repair_state`, reconcile the reported errors
and run the check again. A correct explanation of a failed stopping check does
not satisfy it. Context checkpoints preserve work so execution can resume;
they do not create a new start time, budget, or stop reason.

Dispatch validates the affected company's account gate before contact work;
an unrelated draft row must not block discovery, catalog reads or another
qualified company. Full delivery validation still checks every row, all accepted
lead requirements, exclusions, receipts and financial accounting. Do not remove
missing evidence merely to make a draft pass.

Full validator output includes `stop_decision` (the computed decision and eligible
actions) and `delivery_allowed`. These are read-only CLI outputs, not new fields
to copy over the saved `stop_check`. Only a passing strict validation may set
`delivery_allowed: true`. `--check-stop` alone does not authorize final delivery;
its zero exit code means the decision was computed successfully, including
when that decision is `continue`. Legacy validation never authorizes delivery.

For client jobs, the host must run the full strict validator after every agent
turn and before publishing artifacts. If `delivery_allowed` is false, retain
the job and resume the same session with the validator decision, errors, and
saved next actions. Reconcile missing or invalid state before further spending.
Do not interpret the model's final message, an exported workbook, or a successful
process exit as job completion. Preserve the ledger and uncertain calls when
recovering a process crash or usage limit; use the host's interruption or error
state when it cannot resume. The skill cannot itself restart a terminated host.
Honor explicit user cancellation and platform limits independently of sourcing.

### Result semantics

The following semantic checks supplement JSON Schema: every signal's
`min_age_days` must be no greater than its `max_age_days` when both are
present; every accepted contact's
`domain` must equal its accepted company domain; `contact_candidate_count` must
equal one plus the number of backups; `backup_shortfall` must equal
`max(0, request.contacts_per_company - contact_candidate_count)`; each accepted
account domain must be unique; `account_fit` must support ICP fit;
when the request requires intent, `signal_evidence` must support a requested
signal under `signal_match_mode` and its applicable bounds. When intent is
optional, missing intent does not block qualification: the existing signal
fields may instead explain a conditional use case grounded in sourced business
facts, explicitly labeled `Inferred use case`, not observed buying intent.
Keep optional signal checks preferred, document the request interpretation,
and never use this fallback for a required signal. Evidence URLs and sources
may differ. Follow the [qualification policy](workflow-rules.md#qualification-policy)
to corroborate the same project across sources: use the dated activity in
`signal_evidence`, retain technical corroboration in the relevant
`qualification_checks[].evidence` arrays, and explain the linkage in
`intent_details`. Keep each source's own date and attribution. Contact evidence must
explicitly support a current role at that company; `approved_family` must be
within the full user-approved role family and never an unapproved adjacent
function; every accepted contact's `requested_role` must be in
`request.requested_roles`; and contact fields must be absent unless requested
by the effective field set, which defaults to email. Every accepted primary
must contain every requested field. Every stored email, including an email on
a backup, must have an `email_validation` receipt for the same address. Its
source must identify Deepline and ZeroBounce, link to the matching
`email_validation` route, use the same dynamically discovered tool,
and record explicit `valid`, or `catch-all`/`unknown` with one nested `fallback`
receipt for the same address. A service failure instead records `status: null`
and `provider_status` equal to the failed execution route: `provider_error`,
`timeout`, `rate_limited`, `auth_failed`, or `quota_exceeded`. It may use the
same single fallback. `schema_error`, `config_error`, missing receipts, and
unrecognized verdicts are ineligible. Non-failure ZeroBounce receipts still
require an `ok` or `partial` route. The fallback requires Deepline/BounceBan, API
`status: success` and `result: deliverable` after trimming/case normalization.
It must link to a distinct later successful paid email-validation route with
exactly one paid call. Preserve the original ZeroBounce status. Never allow
fallback for invalid, do_not_mail, spamtrap, abuse, or unfamiliar verdicts;
never chain fallbacks. A risky/unknown fallback stays unresolved;
an undeliverable fallback is rejected. Keep the candidate and both receipts
in unresolved/rejected outcomes, outside the verified workbook and target
count. The failed ZeroBounce route and its cost reservation remain in the
audit after successful fallback; a failure alone never accepts an email.
Validate `primary_contact` and every item in `backup_contacts` with
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
required and `frontier_complete` must be true. The strict stopping check must
permit the stop; a time/budget limit may leave unfinished routes actionable.
Every `exhausted`
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
`explicit_exclusion` is the only exclusion-only reason. Budget availability
alone is not authority to call a blocked tool; one tool's blocker is not a reason
to end other research. Run `scripts/validate_run.py` against the completed
`results.json` to enforce the stopping check and completion rules. This completion
validator supplements rather than replaces validation against the JSON Schema
and the other semantic checks above.

Use `continuation_route_ids` to link an attempt to separately planned follow-ups.
`continuation_exhausted` and `query_family_exhausted` require nonempty links to
terminal routes; references must exist, be unique and contain no cycles.
An exhausted parent cannot have an actionable follow-up. `no_results` requires
an actual empty `no_results` receipt, not a generic review note. These checks
establish consistency, not market exhaustion. Review the evidence and remaining
promising companies before declaring the frontier complete.

Budget accounting uses actual provider usage, not planning estimates. Output
`paid_calls` must equal the sum of route `paid_calls`. For each provider, a
numeric `spent` value must equal the sum of its numeric route `cost_credits`.
If any paid route has unknown actual cost, that provider's `spent` value, its
call capacity, and the overall budget status must be `unknown`. This remains
true when a conservative upper bound is available. `within_budget` requires
known actual spend for both providers. Known spend or paid calls above a hard
limit are invalid.

The optional `max_deepline_credits_per_next_lead` is a hard cap only when explicitly
requested. Do not insert it by default or change saved caps. When active, every paid Deepline route must
record the non-negative `accepted_leads_before_call` count. Sum each route's
actual `cost_credits`, or its `cost_upper_bound_credits` when
`cost_basis` is `estimated`, by that count. A paid Deepline route with unknown
cost cannot prove the allowance and is invalid while the guard is active. The
sum at each dispatch must include all earlier costs at that count or higher
and must not exceed the configured allowance. Route changes,
rejections, and failed lookups do not change the count; only a fully accepted
lead advances it. Acceptance uses the requested contact fields and preserves
explicit email opt-outs; any stored email must pass the email gate. Before a paid
Deepline execution, the agent must add its conservative cost upper bound to
the amount already charged at the current count or higher and must not run the call if
the sum would exceed the allowance. The shared
[paid-call ledger](adapter-io.md#paid-call-budget) enforces this cap before
dispatch; post-run validation independently checks the recorded charges.
Review may reduce the accepted count below historical snapshots. Preserve
those snapshots and all costs; neither demotion nor reacceptance erases spend.
Recompute the remaining allowance without retroactively invalidating calls
that were affordable when made. The output
limit, when present, must match the request limit. Artifacts without this
optional field remain valid for backward compatibility.

For new runs, record `accepted_leads_before_call` on every paid Deepline route
even without that cap. At 5 credits spent or conservatively reserved since the
last complete lead, review the strategy; this is a nonblocking warning. Total
provider and shared dollar budgets remain hard. `--show-progress` on
`validate_run.py` derives this warning without modifying the run or its verdict.
Unmarked or unbounded costs are reported as incomplete, never zero.

The same progress output separates unresolved account evidence, contact
completion, and provider/route failures using the existing stage and reason
fields. It deduplicates companies and excludes accepted-company backup shortfalls.
Contact-stage rows must retain their passing account `qualification_checks`;
their `reason_text` must say what contact evidence is missing and the next action
or concrete blocker. Do the same for account-stage evidence gaps. The agent uses
these groups in the report; only complete accepted contacts enter `leads.xlsx`.

Every version `1.1` or `1.2` route has `cost_credits`,
`cost_upper_bound_credits`, and `cost_basis`. Use these combinations:

- `actual`: actual and upper-bound credits are numeric, non-negative, and
  equal. This means billed usage observed from the provider, not a planned
  price; the route is the usage receipt.
- `estimated`: actual credits are `null`; upper-bound credits are a numeric,
  non-negative conservative estimate for every paid call recorded on that
  route.
- `unknown`: actual and upper-bound credits are both `null`.
- No paid call, including a public-web route: use `actual` with both values set
  to `0`.

The version `1.1` and `1.2` `cost_summary` is derived only from route fields. Confirmed
credits sum `actual` routes. Maximum credits sum actual costs and estimated
upper bounds; the maximum is `null` for a provider with any `unknown` paid
route. The overall status is `unknown` if any paid route is unknown,
`estimated_range` if at least one paid route is estimated, and `exact`
otherwise. Convert Deepline credits at the configured fixed rate of `$0.10`
per credit. Keep ScrapingDog in credits because no dollar rate is configured.
`accepted_leads` equals `summary.accepted_contacts`; divide Deepline dollars by
that value for cost per lead, or use `null` when it is zero. Round derived
dollar values to four decimal places. OpenRouter is not used by this skill, and
Codex model cost is not part of direct provider cost.

Route cost values are totals, not per-call rates. When `paid_calls` is greater
than one, both cost fields cover all paid calls represented by that route.
A typical, midpoint, or unconfirmed price is not an upper bound; use `unknown`
when the full route cannot be conservatively bounded. In versions `1.1` and `1.2`, a
known provider `maximum_credits` must not exceed the matching
`budget.limits.<provider>_credits`; an unknown maximum remains allowed under
the existing unknown-spend rules.

To calculate the expected block while drafting, run
`scripts/validate_run.py <results.json> --show-cost-summary`, copy
`calculated_cost_summary` to the top-level `cost_summary`, and run the validator
again without the flag. The final report must use those same values and label
them as exact, estimated range, or unknown.

For a legacy version `1.0` route without `cost_basis`, `--show-cost-summary`
keeps any paid cost unclassified and unknown. It does not promote a numeric
legacy `cost_credits` value to confirmed actual usage. Migrate the route to
version `1.1` cost fields before reporting confirmed or estimated cost.

## `report.md` and final response

For every new run, include the following in `report.md`. These are reporting
requirements, not new `results.json` fields or workbook columns.

### Timing

- Persist `started_at` from the system clock before the first discovery or
  provider call. Use an ISO 8601 timestamp with a timezone. Preserve it across
  resumptions; do not infer it from file creation/modification or `retrieved_at`.
- Record `leads_ready_at` when the target's final fully qualified lead is saved,
  including requested contact fields and validation. Leave it unavailable for
  a shortfall. This is distinct from total run completion.
- Record `completed_at` after result validation, workbook export and checks.
  Calculate `elapsed_seconds = completed_at - started_at` and display total
  elapsed time in minutes/seconds. Also show time to `leads_ready_at` when known.
  Elapsed time is wall-clock time, including waits and pauses, not CPU time.
- A resumed run keeps its original start. If the start was not recorded, say
  runtime is unavailable or explicitly label a known-interval estimate with its
  boundaries. Never present a reconstructed interval as full runtime.

### Accepted-lead sources

Maintain one report row per final accepted company, keyed by canonical domain:

| Company / domain | Company discovery | Fit evidence | Intent evidence | Buyer role | Email lookup | Email validation |
| --- | --- | --- | --- | --- | --- | --- |

For each stage, identify the provider, underlying tool when returned, and route
ID linking to the saved receipt. Include the original evidence URL for public
sources. Distinguish the discovery channel (for example ScrapingDog Google
search) from the publisher that proves intent (for example a company release).
Deepline is the gateway; name the actual finder or validator when known. A
publicly published email is sourced to that page, not to ZeroBounce. Preserve
both validation tools when fallback was used. Mark missing attribution unknown;
do not invent it or make another paid call solely to label it.

Summarize the number of accepted companies first discovered by each channel/tool
and accepted emails supplied by each finder or public source, plus validation
counts. Assign one original discovery source and one selected email source per
lead so these two totals each reconcile to the accepted count (use unknown or
not requested where needed). Evidence and validation counts may overlap and
must be labeled accordingly. Keep failed/unused routes and their costs separate
from accepted-lead contribution. Counts alone do not establish provider accuracy
or comparative yield without the corresponding attempted-candidate denominator.

### Final-response checklist

Enter this checklist only after the latest full strict validation returns
`delivery_allowed: true`. If it returns false, act on the computed decision and
keep intermediate updates in commentary. Do not turn validation failure into
a final-answer caveat.

Before sending the final chat response, verify it contains all four items:

1. **Result and files:** accepted count versus target, workbook delivery and a
   report link for per-company attribution.
2. **Sources Used:** name the company-discovery tools, fit/signal and buyer-role
   evidence sources, email finder and email validator directly in chat. Include
   accepted-company discovery counts, selected-email source counts and validation
   counts. Name underlying tools, not just their gateway. Distinguish evidence
   publishers from discovery tools; mark unknown or not-requested stages explicitly.
3. **Runtime and full cost:** total elapsed time, provider spend, LLM cost,
   combined total and cost per accepted lead, following the skill's
   [full-cost rule](../SKILL.md#full-cost). Label actual, estimated and unknown
   amounts explicitly; provider-only spend is not the full sourcing cost.
4. **Caveats:** material limitations and any target/contact shortfall; state none
   when there are none.

A report link alone does not satisfy Sources Used. Check timing arithmetic and
source-count reconciliation against the saved receipts. This is a final-answer
self-check, not an additional sourcing step or a JSON-validator guarantee.

## LinkedIn location and company size

Retrieve the exact matched company and person LinkedIn profiles through
HarvestAPI via Deepline. Discover/describe the current company/profile getters,
execute through the existing budgeted attempt helper, and reuse matching saved
receipts. Search results and another provider's estimates cannot replace these
field sources.

- Save LinkedIn `employeeCountRange` as `company.employee_range`, e.g. `11-50`
  or `10001+`. LinkedIn is authoritative for company size. Do not use
  `employeeCount` (associated member profiles), an exact headcount, or a range
  endpoint for qualification or export. Compare the whole range with the saved
  ICP: full containment passes, no overlap fails, partial overlap stays unknown.
- Use the person's own `location.parsed.countryFull`/`country`, `state`, and
  `city`; retain `location.linkedinText` and country codes from the response.
  Use the response's country code when its parsed country is missing; leave
  city/state blank when the response has no structured values for them.
  Never substitute employment location, company headquarters, profile language,
  or a guessed city for the person's location. Country is required for every
  accepted primary/backup contact; populate city and state whenever supported.
- Save `company.employee_range_evidence` and each contact's `location_evidence`
  using `evidence_url`, `evidence_date`, `evidence_date_basis: observed_current`,
  `evidence_text`, and `source: {provider: deepline, operation: execute, tool,
  route_id}`. Keep the original range/location wording in the text. The URL must
  match the corresponding LinkedIn entity and the route must be a successful
  HarvestAPI company/profile getter. Missing facts or evidence stay unresolved.

These fields are required independently of email/phone opt-outs.
Review acceptance fills missing range/location values from the matching saved
HarvestAPI response. Supplied values must agree; city/state stay blank when the
response does not support them. The same receipt check runs during strict
validation and workbook export, reading the captured provider body rather than
reviewer-written evidence text. It verifies the entity, run and request identity
without another provider call. Keep `receipts/<route_id>.json` with the run.
Old saved runs may need enrichment or receipt reconciliation before re-export;
do not invent evidence, change their requests, or overwrite historical files to
make them pass. Library-only structural checks without a run path are not a
delivery gate; use the full strict validator CLI before delivery.

## `leads.xlsx` contract

For version `1.2`, write a workbook with `Leads` and `Sources` worksheets.
The first row of `Leads` is the fixed, ordered header:

```text
Name,Email,Role,Company,LinkedIn,Website,Company LinkedIn,Industry,Sub Industry,Contact City,Contact State,Contact Country,HQ State,HQ Country,Company Employee Range,Description,Intent Signal,Signals,Intent Details,Phone
```

Versions `1.0` and `1.1` keep their single `Leads` worksheet, 18-column
layout (without `Intent Signal` or `Signals`), and labelled signal/date/details/source text
in `Intent Details`, with the same clarified location/range headers on new exports.
Do not silently migrate or overwrite historical runs.

`leads.xlsx` is the clean flattened deliverable. Write exactly one row for each
accepted primary company-contact pair and no rows for rejected, unresolved, or
route outcomes. Uniqueness is by canonical domain. Use these exact mappings:

| Workbook column | `results.json` source |
|---|---|
| `Name` | `primary_contact.full_name` |
| `Email` | validated `primary_contact.email`, otherwise blank |
| `Role` | `primary_contact.current_title` |
| `Company` | `company.canonical_name` |
| `LinkedIn` | `primary_contact.linkedin_url`; use `contact_url` only when it is a LinkedIn URL |
| `Website` | `company.website`; otherwise `https://` plus the canonical `company.domain` |
| `Company LinkedIn` | `company.linkedin_url`, otherwise blank |
| `Industry` | `company.industry`, otherwise blank |
| `Sub Industry` | `company.sub_industry`, otherwise blank |
| `Contact City` | `primary_contact.city`, otherwise blank |
| `Contact State` | `primary_contact.state`, otherwise blank |
| `Contact Country` | required `primary_contact.country` from LinkedIn through HarvestAPI |
| `HQ State` | `company.hq_state`, otherwise blank |
| `HQ Country` | `company.hq_country`, otherwise blank |
| `Company Employee Range` | required `company.employee_range` from LinkedIn through HarvestAPI |
| `Description` | required `company.description`, exactly two factual sentences |
| `Intent Signal` | short signal label from `signal_evidence.signal` |
| `Signals` | `signal_evidence` plus passed `qualification_checks` explicitly tagged with `signal`; facts, original source dates and available source URLs |
| `Intent Details` | `intent_details`, a natural paragraph explaining the activity, its context and why the company matters now |
| `Phone` | `primary_contact.phone`, otherwise blank |

Rejected, unresolved, backup contacts and provider receipts remain in
`results.json` and `report.md`. `Sources` contains the accepted company's fit,
signal, primary-role, contact-location, employee-range and qualification-check evidence, preserving source text
and URLs. Its columns are `Company,Domain,Field,Signal,Evidence Date,Date Basis,
Observed On,Source URL,Evidence Text`. `Evidence Date` is the stored published,
posted or updated date, not necessarily the event date. For `observed_current`,
leave `Evidence Date` blank and put the original evidence date in `Observed On`.
Otherwise use the run's retrieval date for `Observed On`. Export dates as typed
Excel dates. An unresolved classification note gets an `Industry` source row
with blank URL and dates; it is a limitation, not a verification receipt.
The `Signals` cell uses one block per signal/source, labels observation dates
`Observed on` and other evidence dates `Source date`, and omits missing values.
It does not infer event dates. Store additional signals in the existing
`qualification_checks` with an optional short `signal` label and supporting
evidence; only `pass` checks enter `Signals`. Unknown/failed checks remain in
the audit and must not be presented as verified activity. No duplicate prose
field is needed for this column. The `Sources` rows for verified signals use
`Field: Signals`; they also support the factual claims in `Intent Details`.

`Email` and `Phone` are blank unless the
input requests them and a verified value is available. Generate the file with
`scripts/export_xlsx.mjs` so the spelling, order, types, and layout stay
deterministic. Use the harness-provided `@oai/artifact-tool`; it is not a TYCHE
project dependency and TYCHE does not install or pin it. When accepted rows
exist, format the range as an Excel table with filters, hide gridlines, and wrap
long description and intent text.
Preserve exact employee counts as numbers, keep ranges as text, and leave
unverified optional values as empty cells rather than placeholder text.

### Client writing and taxonomy (version `1.2`)

- Write `company.description` as exactly two factual sentences explaining the
  business naturally from verified information. Sentence one describes what it
  does or sells, typically "[Company name] provides...". Sentence two adds its
  customers, specialization or another useful business detail, typically
  "It serves...". These openings are examples, not fixed templates. Support both
  sentences with account-fit or qualification evidence. Keep signals, inferred
  needs, contact-validation warnings, scoring and internal diagnostics out of it.
- Write `intent_details` as one natural paragraph. Describe what the company
  did with specific facts and supported dates. Explain what that activity shows
  about its needs and why it is relevant. Close with a sentence tying the evidence
  together to explain why the company matters now in terms of its own situation.
  Focus on the company's activity, not a pitch for our product. Keep inferred
  needs conditional and material uncertainty clear; do not invent urgency,
  purchasing intent or an event date from an observation date. Use `Signals` for
  source URLs and evidence details, not labels such as "Required:" or "Bonus:"
  in the paragraph. Unknown optional hiring is not affirmative hiring intent.
  A new leader is not proof of layoffs, an existing service is not unmet demand,
  and an old opening is not newly dated intent.
- Keep `signal_evidence.signal` short and consistent within the request, such as
  `New HR leader`, `Announced layoffs` or `Housing expansion`. Use
  `Inferred use case` when intent is optional and only a grounded hypothesis
  is available; disclose what is unknown in `intent_details`. Preserve detailed
  claims in evidence and prose. Every factual clause in the narrative must be
  supported by the saved signal or qualification evidence for that company.
  Save additional supporting sources as existing qualification-check evidence;
  do not combine unsupported events into a more persuasive story.
- Before export, review both authored fields against their saved evidence and
  check the two-sentence description and paragraph structure. The helpers require
  these fields and preserve the authored text; factual accuracy and natural prose
  are sourcing-agent review responsibilities, not regex or extra model-call gates.
- Use `assets/leadpoet_industry_taxonomy.json`, a versioned PP snapshot with
  pinned provenance. Select the company's business activity from evidence, not
  the customer's industry or the technology merely mentioned in a job posting.
  Every populated `industry`/`sub_industry` pair must use exact canonical labels
  and a permitted parent-child relationship. Some children have multiple parents;
  choose the evidence-supported one. Membership alone does not prove accuracy.
- When classification cannot be supported, omit both fields and supply
  `company.classification_note` explaining the gap. Leave the workbook cells
  blank. Do not invent a default pair or reject an otherwise qualified account
  merely for this metadata gap. Required ICP industry evidence still applies.
  Raw provider classifications stay in the saved receipts. Do not import PP's
  heuristic fallback rules or add another AI call to format the output.

The semantic validator enforces these new required fields and taxonomy pairs
only for version `1.2`; billing and all existing eligibility checks still apply.
The exporter does not write narratives, classify companies, or infer dates.

## `report.md` minimum contents

The Markdown report is the human audit receipt. Include the normalized request,
assumptions and as-of date; signal hypotheses and why routes differ; every
capability search/describe and execute receipt (without secrets); pilot limits,
rows, duplicates, route cost bases, confirmed and maximum provider credits,
Deepline dollars and cost per accepted lead, and live provider statuses;
account and contact gate decisions; primary/backups selection; adaptive
reserve/refill decisions; all
accepted, rejected, and unresolved rows with stable reasons; contact target
shortfalls; the route frontier and call capacity; reviewed-company counts; and
the final stop reason. Do not claim a discovery-candidate endpoint ran, and do
not include raw provider payloads or credentials.
