# Permit Inspection Sign-Off Workflow

Ridgeview County building department simulation: field inspection observations are reviewed
by specialist agents, gated for human sign-off where required, and turned into a permit
decision.

## Layout

| Path | Purpose |
| --- | --- |
| `app.py` | Streamlit UI: case selection, config, run, inspector decision panel |
| `src/workflow.py` | `InspectionWorkflow.start()` / `.resume()` orchestration |
| `src/agents/` | `StructuralCheckAgent`, `SystemsCheckAgent` |
| `src/llm.py` | `OpenAIInspectionClient` — prompt rendering + JSON parsing |
| `src/models.py` | Enums and dataclasses (`Verdict`, `WorkflowState`, `ItemVerdict`, ...) |
| `src/session_store.py` | Suspension/state helpers (`new_suspension`, `pending_items`) |
| `data/` | `cases.json` (inspection cases), `code_sections.json` (citable sections) |
| `prompts/` | Per-agent prompt templates |
| `tests/` | Pytest suite for the workflow contract |

## Run

```bash
./install.sh
source .venv/bin/activate
export OPENAI_API_KEY=...          # only needed for the real LLM path
streamlit run app.py
```

## Test

```bash
./tests.sh
```

The test suite uses stub agents, so it runs without an API key.
