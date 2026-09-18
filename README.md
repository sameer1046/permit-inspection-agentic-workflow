# Permit Inspection Sign-Off Workflow

Ridgeview County building department simulation: field inspection observations are reviewed
by specialist agents, gated for human sign-off where required, and turned into a permit
decision.

The interesting part is not calling an LLM — it is that the LLM is untrusted. Every specialist
response is validated before use, invalid ones are retried and recorded, and anything that
needs licensed human judgment suspends the case until an inspector decides.

## Layout

| Path | Purpose |
| --- | --- |
| `app.py` | Streamlit UI: case selection, config, run, inspector decision panel |
| `src/workflow.py` | `InspectionWorkflow.start()` / `.resume()` orchestration |
| `src/agents/` | `StructuralCheckAgent`, `SystemsCheckAgent` |
| `src/llm.py` | `OpenAIInspectionClient` — prompt rendering + JSON parsing |
| `src/models.py` | Enums and dataclasses (`Verdict`, `ItemVerdict`, `WorkflowConfig`, ...) |
| `src/session_store.py` | State helpers (`new_suspension`, `pending_items`, permit status) |
| `data/` | `cases.json` (inspection cases), `code_sections.json` (citable sections) |
| `prompts/` | Per-agent prompt templates |
| `tests/` | Pytest suite for the workflow contract |

See `APPROACH.md` for the orchestration model, assumptions, trade-offs and failure modes.

## Run

```bash
./install.sh
source .venv/bin/activate
cp .env.example .env
# edit .env with your key (OPENAI_* will be loaded via python-dotenv)
streamlit run app.py
```

## Test

```bash
./tests.sh
```

The suite uses stub agents, so it runs without an API key.
