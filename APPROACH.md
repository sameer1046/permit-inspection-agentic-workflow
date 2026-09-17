# Approach

## Reconstruction note

This repo was rebuilt from photographs of the original editor window. Files that were fully
visible (`structural_check_agent.py`, `systems_check_agent.py`, `llm.py`, `session_store.py`,
the enums in `models.py`, `app.py`) are faithful; the dataclasses in `models.py`, the data
files, the prompts and the tests were never visible and are reconstructed to be consistent
with how the visible code uses them. Anything reconstructed is called out below.

## Orchestration model

`start()` is a single pass over the case; `resume()` is the same terminal logic applied after
human input. Both funnel into `_finalize()`, so a case can only reach `complete` through one
code path and the permit decision is computed in exactly one place.

1. **Validate the case** — `_is_valid_case()` (provided). A malformed case returns
   `status='failed'`, `error=INVALID_REQUEST` and no agent calls are made.
2. **Route to specialists** — `_specialists_for()` reads `item.specialists`, so a cross-trade
   item can name both agents. Unknown names are dropped; an empty list falls back to
   `structural_check` rather than silently skipping the item.
3. **Call each agent with retries** — `_run_with_retries()` retries up to `config.max_retries`
   extra attempts. Every invalid verdict is appended to `rejected_verdicts` with the failing
   code, so the audit trail shows what was thrown away and why.
4. **Validate each verdict** — `_validate_verdict()` maps failures to the provided codes:
   unknown verdict → `MALFORMED_VERDICT`; risk score non-numeric or outside `[0.0, 1.0]` →
   `RISK_OUT_OF_RANGE`; citation not in `item.applicable_code_sections` →
   `UNGROUNDED_CITATION`.
5. **Resolve the item** — `_resolve_item()` takes the most severe verdict across agents and
   the maximum risk score. An item goes pending if it is not compliant, its category is
   gated, or its risk meets `risk_gate_threshold`. If no valid verdict survived retries the
   item becomes `needs_review` and pending — never silently compliant.
6. **Finalize** — any pending item means `suspended_pending_human` with `permit_status=None`.
   Otherwise `_compute_permit_status()` runs: `denied` if a non-compliant item is in a
   hard-block category, `conditional` if any other non-compliant item remains, else
   `approved`.

`resume()` maps `approve/reject/defer` to `(compliant, non_compliant, needs_review)` and
`(HUMAN_APPROVED, HUMAN_REJECTED, HUMAN_DEFERRED)`. Undecided and deferred items stay
pending, which is what makes multi-round shift handover work: calling `resume()` repeatedly
with partial decisions is safe and converges.

## Assumptions (reconstructed, would confirm against the real brief)

- `item.specialists` is the routing field. The original `InspectionItem` was never visible;
  if it is really `primary_specialist`/`secondary_specialist`, only `_specialists_for()`
  changes.
- Risk scores are bounded to `[0.0, 1.0]` inclusive, and `risk_gate_threshold` is inclusive
  (`>=`), so a threshold of 1.0 still gates a maximum-risk item.
- `cited_code_section = None` is legal (an agent may decline to cite); a *wrong* citation is
  not.
- Human sign-off on a hard-block category still denies the permit — a human can clear an
  item but not override the hard block. This is the conservative reading.
- Permit statuses are `approved` / `conditional` / `denied`.

## Trade-offs

- **Sequential agent calls.** Simple and deterministic, which matters for the audit trail.
  Items are independent, so this is the obvious first thing to parallelise.
- **State as plain dicts inside `WorkflowState`.** Matches `new_suspension()` and keeps the
  state JSON-serialisable for Streamlit session storage, at the cost of type safety.
- **Retry without prompt repair.** A rejected verdict is retried with the identical prompt.
  Feeding the validation code back to the model would likely convert more failures, but adds
  a prompt-coupling that the stub-based tests could not honestly cover.
- **`AgentUnavailableError` fails the whole case** rather than degrading to per-item review.
  A model outage is an infrastructure fault, not an inspection finding, and should not look
  like one.

## Failure modes

- LLM returns prose instead of JSON → `llm.py` returns `{}` → agent emits a null verdict →
  `MALFORMED_VERDICT` → retry → escalation. Nothing reaches the permit decision unvalidated.
- Model consistently hallucinates a plausible-but-inapplicable code section → repeated
  `UNGROUNDED_CITATION` → item escalates to a human. Correct, but silent if nobody watches
  `rejected_verdicts`; in production this needs an alert on rejection rate.
- Inspector never resolves a deferred item → the case stays `suspended_pending_human`
  forever. There is no SLA or expiry.
- Config drift: widening `gated_categories` pushes more items to humans and can stall
  throughput; narrowing it silently reduces oversight. There is no guard on either.

## What I would change for production

- Persist `WorkflowState` in a real store keyed by `case_id`, with optimistic concurrency —
  Streamlit `session_state` loses everything on refresh and cannot support two inspectors.
- Make `resume()` idempotent per `(case_id, item_id, decision_id)` so a double-submit cannot
  double-apply, and record the acting inspector's identity on every human decision.
- Add structured metrics: verdict rejection rate by code and agent, escalation rate by
  category, time-to-resolution per pending item.
- Feed validation failures back into the retry prompt, and pin the model version so verdict
  distributions do not shift underneath the thresholds.
- Move thresholds and category lists into reviewed, versioned policy rather than UI widgets,
  since they directly control how much human oversight a permit receives.
