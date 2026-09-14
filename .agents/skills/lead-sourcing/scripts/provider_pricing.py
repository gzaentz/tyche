"""Catalog prices first; measured planning prices only for tested profile modes.

Reservations are estimates, not provider guarantees or bills. Actual returned
billing settles the existing ledger; a higher charge blocks further spending.
"""

import budget_guard as budget


# Deepline receipt hashes from the authorized 2026-09-14 endpoint diagnostic.
# No profile email/About add-ons were tested. Keep their prices unresolved.
PROFILE_PRICES = {
    "main": {"credits": .03, "verified_at": "2026-09-14", "basis": "measured_planning_price",
             "receipt_sha256": "836663ebb049ac493f820c646f2411e096c023a9df04e014ca04228025797b59"},
    "full": {"credits": .05, "verified_at": "2026-09-14", "basis": "measured_planning_price",
             "receipt_sha256": "0e07fa89580385d42b3432c2760c3a1a2e59bf51837e33b3cdeaa2c8f6fb1925"},
}


def profile_price(contract, inputs):
    if contract.get("toolId", contract.get("id")) != "harvestapi_get_profile":
        return None
    if set(inputs) - {"url", "publicIdentifier", "profileId", "main"}:
        return None
    if "main" in inputs and inputs["main"] != "true":
        return None  # Only the exact measured modes qualify for fallback.
    return PROFILE_PRICES["main" if "main" in inputs else "full"]


def call_credits(contract, inputs, override=None):
    pricing = contract.get("pricing", {})
    rate, unit = pricing.get("creditsPerUnit"), pricing.get("unit")
    fields = {f["name"]: f for f in contract.get("inputSchema", {}).get("fields", [])}
    quantity = None
    if unit in ("call", "request"):
        quantity = 1
    elif unit == "page" and "page" in fields and not any(k in fields for k in ("pages", "maxPages", "max_pages")):
        quantity = 1
    elif unit == "result":
        if "limit" in fields:
            quantity = inputs.get("limit", fields["limit"].get("default"))
        elif contract.get("toolId", contract.get("id")) in {
                "zerobounce_validate", "bounceban_verify_single", "hunter_email_finder", "datagma_find_email"}:
            quantity = 1
    bound = None
    if rate is not None and type(quantity) is int and quantity > 0:
        bound = budget.amount(rate, "catalog price") * quantity
    # A published but unsupported pricing unit is not replaced by a guess.
    stored = profile_price(contract, inputs) if rate is None else None
    if bound is None and stored:
        bound = budget.amount(stored["credits"], "measured planning price")
    if override is not None:
        supplied = budget.amount(override, "whole-call price reservation")
        if bound is not None and supplied < bound:
            raise ValueError("Supplied price bound is below the catalog-derived or stored whole-call cost")
        return float(supplied)
    if bound is None:
        raise ValueError("No whole-call price is available for these options. Use a priced configuration or report the missing rate; do not guess max_cost_credits. No paid call was made.")
    return float(bound)
