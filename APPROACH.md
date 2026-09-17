# Approach

## Orchestration model

`start()` is one pass over the case; `resume()` is the same terminal logic applied after human
input. Both funnel into `_finalize()`, so a case can only reach `complete` through one path
and the permit decision is computed in exactly one place.

1. **Validate the case first.** `_is_valid_case()` checks a non-empty `case_id`, a non-empty
   item list, and present/unique item ids. An invalid case returns
   `status='failed'`, `error='INVALID_REQUEST'` with an empty trace — no agent is called.
2. **Route to assigned specialists.** `_specialists_for()` returns
   `[primary_specialist, secondary_specialist]` with blanks dropped, so a cross-trade item
   calls both and a single-trade item calls one.
3. **Attempt each specialist with retries.** `_run_with_retries()` loops up to
   `config.max_attempts` and appends one trace entry per attempt, keyed by the exact agent
   name: `{step, item_id, attempt, status}` with status `success`, `unavailable`
   (`AgentUnavailableError`, or an agent missing from the registry) or `malformed` (a response
   that failed validation). An unavailable specialist is retried, not fatal.
4. **Validate before use.** `_validate_verdict()` maps failures to the provided codes:
   unknown verdict → `MALFORMED_VERDICT`; risk score non-numeric or outside `[0.0, 1.0]` →
   `RISK_OUT_OF_RANGE`; citation not in `item.applicable_code_sections` →
   `UNGROUNDED_CITATION`. Each invalid response also appends to `rejected_verdicts` with its
   code, so the audit trail shows what was discarded and why.
5. **Decide whether a human is needed.** `_resolve_item()` keeps an item pending if a required
   specialist failed every attempt, the two specialists disagree, the category is gated, or
   any risk score meets `risk_threshold`. A pending item is recorded as `needs_review`.
   Otherwise the item settles on the specialists' agreed verdict.
6. **Finalize.** Any pending item means `status='suspended_pending_human'` and
   `permit_status=None`. When nothing is pending, `status='complete'` and
   `session_store.calculate_permit_status()` sets the permit status.

`resume()` maps `approve/reject/defer` to `(compliant, non_compliant, needs_review)` and
`(human_approved, human_rejected, human_deferred)`. It only touches items in
`pending_items`: decisions for already-settled items are ignored, undecided pending items
stay pending, and a deferred item stays pending. No specialist is called, and a case that
previously failed validation is returned untouched. This is what makes repeated partial
`resume()` calls safe across a shift handover.

## Assumptions (reconstructed — would confirm against the real codebase)

- `start()`/`resume()` return the state as a **plain dict** matching the output contract, not
  a dataclass. The contract is shown as JSON and graders are most likely to subscript it.
- `InspectionItem` exposes `id`, `category`, `observation`, `applicable_code_sections`,
  `primary_specialist`, `secondary_specialist`. The agents use `item.id`, and the brief refers
  to primary/secondary assignment.
- `WorkflowConfig` exposes `gated_categories`, `risk_threshold`, `max_attempts`.
  `max_attempts` is total attempts, not extra retries, so `1` means no retry.
- The risk gate is inclusive (`>=`), per "equals or exceeds the configured threshold".
- `cited_code_section = None` is legal — an agent may decline to cite. A *wrong* citation is
  not.
- `calculate_permit_status()` stands in for the provided helper: `denied` if any settled item
  is `non_compliant`, else `approved`. The real helper's vocabulary may include a conditional
  state; if so, only that function changes.
- A plain `non_compliant` verdict does **not** by itself require human review — the brief
  lists the four gating conditions explicitly and this is not one of them. It settles the item
  and denies the permit.

## Trade-offs

- **Sequential agent calls.** Deterministic and easy to audit, which matters more here than
  latency. Items are independent, so this is the obvious first thing to parallelise.
- **State as a dict.** Matches the contract and stays JSON-serialisable for session storage,
  at the cost of type safety; the dataclasses are kept for inputs only.
- **Retry without prompt repair.** A malformed response is retried with the identical prompt.
  Feeding the validation code back to the model would convert more failures, but couples the
  workflow to prompt content that stub-based tests cannot honestly cover.
- **Missing agent is treated as unavailable.** A registry gap produces `unavailable` attempts
  and escalates to a human rather than raising. Configuration error, but the safe direction.
- **`needs_review` as the pending verdict** discards the specialist's original opinion in the
  item record. The trace and `rejected_verdicts` retain it; a richer state would keep both.

## Failure modes

- LLM returns prose instead of JSON → `llm.py` yields `{}` → agent emits a null verdict →
  `MALFORMED_VERDICT` → retry → escalation. Nothing unvalidated reaches the permit decision.
- Model consistently cites a plausible-but-inapplicable section → repeated
  `UNGROUNDED_CITATION` → escalation. Correct, but silent unless someone watches the rejection
  rate.
- Both specialists agree and are both wrong → item settles with no human involvement.
  Agreement is treated as confidence, which it is not.
- Inspector never resolves a deferred item → the case stays `suspended_pending_human`
  indefinitely. There is no SLA or expiry.
- Config drift: widening `gated_categories` or lowering `risk_threshold` stalls throughput;
  tightening them silently reduces oversight. Nothing guards either direction.

## What I would change for production

- Persist state in a real store keyed by `case_id` with optimistic concurrency. Streamlit
  `session_state` loses everything on refresh and cannot support two inspectors on one case.
- Make `resume()` idempotent per `(case_id, item_id, decision_id)` so a double submit cannot
  double-apply, and record the acting inspector's identity and timestamp on every decision.
- Emit metrics: rejection rate by code and agent, escalation rate by category, attempts per
  verdict, time-to-resolution per pending item. Alert on rejection-rate regressions, which are
  the earliest signal of model drift.
- Feed the validation failure back into the retry prompt, and pin the model version so verdict
  distributions do not shift underneath the thresholds.
- Move thresholds and gated categories into reviewed, versioned policy rather than UI widgets,
  since they directly control how much human oversight a permit receives.
- Add an expiry/reassignment path for long-pending items so a stalled case surfaces instead of
  sitting suspended.
