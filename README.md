# Smart Ticket Router

A FastAPI service that classifies raw customer support messages into a fixed routing taxonomy — category, priority, assigned team, and a one-sentence justification — using the OpenAI API's structured output mode. Tickets that are successfully classified and assigned to a real team are persisted to PostgreSQL with a generated ticket ID; everything else (gibberish, off-topic chat, failed classification) is returned to the caller but never written to the database.

## Problem Statement

Manually triaging a support ticket means a human reads the message and decides, by judgment, what category it belongs to, how urgent it is, and which team should own it. That's slow, inconsistent between agents, and easy to get wrong under an angry or vague message — and it produces no structured `category` / `priority` / `assigned_team` data to route or report on.

Handing this to an LLM directly doesn't solve it either: a plain prompt-and-parse approach can return prose instead of JSON, invent a category outside your taxonomy, or fail outright — and a service that raises on a malformed reply has thrown the ticket away. This project constrains the model to a fixed schema, re-checks its own output rather than trusting the API, and always returns a usable result even when classification fails.

## Features

- **Schema-constrained classification** — `category`, `priority`, `assigned_team`, `reasoning`, and `clarification_needed` are generated under an OpenAI Structured Outputs schema (`strict: true`), so the model can only emit values from the fixed enums defined in `app/schema.py`.
- **Independent re-validation** — Responses are independently validated against the JSON schema before being returned to the client.
- **Retry + deterministic fallback** — up to 3 attempts with linear backoff on a parse/validation/API failure; if every attempt fails, a fixed fallback record (`Tier1 Support`, `clarification_needed: true`) is returned instead of an exception.
- **Priority determined from business impact** — Priority is assigned solely from the described impact and urgency rather than emotional tone, capitalization, or punctuation.
- **Rule-based multi-issue routing** — Tickets containing multiple issues are mapped to a single category using a predefined internal precedence order while mentioning additional issues in the reasoning.
- **Out-of-scope / gibberish detection** — greetings, small talk, and unrelated requests (jokes, weather, translation) are recognized as not being support tickets at all and routed to `assigned_team: "None"`.
- **Selective persistence** — a ticket is only written to Postgres when classification succeeded *and* a real team was assigned; otherwise `ticket_id` comes back `null` and nothing is stored.
- **Two entry points, one core function** — an HTML form and a JSON API (`POST /api/route`) both call the same `route_ticket()`.
- **Offline dev mode** — `MOCK_MODE=1` swaps the OpenAI call for a deterministic keyword classifier, for developing/testing the form and running the test suite without an API key or cost.
- **Batch timing tooling** — `scripts/run_batch.py` routes a JSON file of tickets and records per-ticket timing; `scripts/compare_times.py` joins that against hand-timed manual routing to produce a manual-vs-AI speedup table.
- **Clarification-aware routing** — Instead of guessing when a ticket is incomplete or ambiguous, the system explicitly indicates that additional information is required before confident routing.
- **Vagueness isn't the same as low severity** — a short message can still describe a security breach, outage, or data loss; the system prompt carves out an explicit exception so these get `priority: "High"` per the rubric instead of being downgraded just because detail is missing.
- **Empty-ticket guard on the web form** — submitting a blank or whitespace-only message shows an inline "please enter a ticket" error instead of a routing result; `route_ticket()` itself still returns a structured fallback for empty input (used by `/api/route`, the batch script, and the test suite).
- **Manual-vs-AI comparison in the web UI** — the result view shows the AI's elapsed time next to an illustrative manual-routing baseline and the resulting speedup multiplier, alongside the more rigorous batch comparison tooling below.

## Architecture / Workflow

```mermaid
flowchart TD
    A["Client\n(web form or POST /api/route)"] --> B["route_ticket(message)\napp/router.py"]
    B --> C{"MOCK_MODE=1?"}
    C -- yes --> D["_mock_classify()\nkeyword rules"]
    C -- no --> E["OpenAI Chat Completions\nresponse_format=json_schema, strict"]
    E --> F["json.loads +\njsonschema.validate +\nrequired-field check"]
    F -- valid --> G["classification dict\nis_fallback=False"]
    F -- invalid / API error --> H{"attempt < 3?"}
    H -- yes --> I["sleep(backoff), retry"] --> E
    H -- no --> J["FALLBACK_RESPONSE\nis_fallback=True"]
    D --> G
    G --> K["web.py: _route_and_persist()"]
    J --> K
    K --> L{"is_fallback OR\nassigned_team == 'None'?"}
    L -- no --> M["uuid4() ticket_id\nINSERT into tickets (Postgres)"]
    L -- yes --> N["ticket_id = null\nnot persisted"]
    M --> O["JSON / HTML response"]
    N --> O
```

`app/schema.py` owns the taxonomy and the system prompt; `app/router.py` owns the call/validate/retry/fallback logic and is the only place that talks to OpenAI; `app/web.py` owns the HTTP layer and the persistence decision; `app/db.py` owns the SQLAlchemy model and session lifecycle. Every routing rule (single category per ticket, tone-independent priority, clarification over guessing, schema conformance, non-persistence of out-of-scope tickets) is enforced either by the schema itself or by the system prompt in `app/schema.py`, not by ad-hoc logic elsewhere.

One exception to this diagram: on the HTML form (`POST /`) only, a blank/whitespace-only message never reaches `route_ticket()` at all — `app/web.py` intercepts it first and renders an inline error. `POST /api/route` has no such guard; it always calls `route_ticket()`, so empty input there still flows through the diagram above and returns the structured fallback.

## Tech Stack

**Backend**

| Technology | Role |
|---|---|
| Python 3.9+ | Runtime |
| FastAPI | HTTP layer — HTML form routes + `/api/route` JSON endpoint, request validation via Pydantic |
| Uvicorn | ASGI server |
| Jinja2 | Renders `app/templates/index.html` |
| python-multipart | Parses the form POST body |
| python-dotenv | Loads `.env` in `app/db.py`, `app/web.py`, `scripts/run_batch.py` |

**AI / LLM**

| Technology | Role |
|---|---|
| OpenAI Chat Completions API | Ticket classification (default model: `gpt-4o-mini`, set via `OPENAI_MODEL`) |
| Structured Outputs (`response_format: json_schema`, `strict: true`) | Constrains the model to the schema in `app/schema.py` |
| jsonschema | Independent re-validation of the parsed response |

**Database**

| Technology | Role |
|---|---|
| PostgreSQL | Stores routed tickets |
| SQLAlchemy 2.0 | ORM (`Ticket` model), engine/session management |
| psycopg2-binary | PostgreSQL driver |

**Tools**

| Technology | Role |
|---|---|
| pytest | Test suite; LLM calls mocked via `monkeypatch` at the `_call_llm` boundary |
| argparse | CLI for `scripts/run_batch.py` and `scripts/compare_times.py` |
| csv / statistics (stdlib) | Manual-vs-AI timing comparison |

## Project Structure

```
app/
  schema.py            taxonomy, JSON schema, system prompt
  router.py             route_ticket() — the classification core
  db.py                 SQLAlchemy engine/session, Ticket model, init_db(), get_db()
  web.py                 FastAPI routes, persistence decision
  templates/index.html  server-rendered form + result view
data/
  sample_tickets.json          20 demo tickets, incl. required edge cases
  manual_timing_template.csv   hand-filled stopwatch times for the speedup comparison
scripts/
  run_batch.py         batch-routes a ticket file, records timing, writes results JSON
  compare_times.py     joins AI + manual timings into a comparison table
tests/
  test_router.py       tests route_ticket() with the LLM call mocked
```

Notes on a few non-obvious details:

- `app/schema.py` is the single source of truth for the taxonomy (`CATEGORIES`, `PRIORITIES`, `TEAMS`); changing what the model can output means changing this file.
- `app/router.py` has no FastAPI or database dependency, so `route_ticket()` is callable unchanged from the web app, the batch script, or a test.
- `app/db.py`'s `init_db()` runs on FastAPI startup (creates the table if missing); `get_db()` is the per-request session dependency.
- `data/sample_tickets.json`'s 20 tickets include the 3 required edge cases (angry tone, vague message, multi-category) plus 5 tagged with an expected severity for spot-checking the model's priority calls.
- `scripts/compare_times.py` reads `run_batch.py`'s output plus a manually-filled CSV of stopwatch times to produce `results/comparison.md`.

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# then edit .env — see Environment Variables below
```

Requires Python 3.9+ and an OpenAI API key with access to a model that supports Structured Outputs.

**Database setup** — requires a running PostgreSQL server. On macOS, [Postgres.app](https://postgresapp.com/) is the fastest way to get one locally: install it, click **Initialize**, then:

```bash
createdb ticket_router
```

The `tickets` table itself is created automatically on app startup (`init_db()` in `app/db.py`) — there's no separate migration step.

## Environment Variables

Copy `.env.example` to `.env` and fill in:

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | — | Required unless `MOCK_MODE=1`. |
| `OPENAI_MODEL` | `gpt-4o-mini` | Any OpenAI model that supports Structured Outputs. |
| `MOCK_MODE` | `0` | Set to `1` to route through a deterministic keyword classifier instead of calling OpenAI (offline dev/testing only). |
| `DATABASE_URL` | `postgresql://postgres:postgres@localhost:5432/ticket_router` | SQLAlchemy connection string for the `tickets` table. |

```
OPENAI_API_KEY=sk-your-key-here
OPENAI_MODEL=gpt-4o-mini
MOCK_MODE=0
DATABASE_URL=postgresql://<your-username>@localhost:5432/ticket_router
```

## Running the Project

**Web form**

```bash
uvicorn app.web:app --reload
```

Open `http://127.0.0.1:8000`, submit a ticket, see the routing result. Interactive API docs (auto-generated by FastAPI) are at `http://127.0.0.1:8000/docs`.

**Batch run over the 20 demo tickets**

```bash
mkdir -p results
python scripts/run_batch.py data/sample_tickets.json --out results/ai_batch_results.json
```

Prints each ticket's decision and timing, then a summary (count/total/avg/median seconds).

**Manual-vs-AI comparison**

Fill in `manual_seconds` for each row in `data/manual_timing_template.csv` (a person timing themselves triaging each ticket by hand), then:

```bash
python scripts/compare_times.py results/ai_batch_results.json data/manual_timing_template.csv
```

Writes `results/comparison.md` with a per-ticket speedup table.

**Offline / no API key**

```bash
MOCK_MODE=1 uvicorn app.web:app --reload
```

Routes through `_mock_classify()` (keyword-based) instead of OpenAI — for developing against the form without spending API credits. Not a substitute for the real classification path.

**Tests**

```bash
pytest tests/ -v
```

Covers empty input, mock-mode short input, malformed/incomplete LLM JSON (fallback path), and a well-formed response passing through — the LLM call is mocked in every case, so no API key is needed to run the suite.

## API Endpoints

| Method | Path | Body | Response |
|---|---|---|---|
| `GET` | `/` | — | Renders the HTML form |
| `POST` | `/` | form-encoded `message` | Re-renders the form with the routing result, plus elapsed time, a manual-routing baseline, and the speedup multiplier — or an inline error if `message` is blank |
| `POST` | `/api/route` | `{"message": "string"}` | JSON: `category`, `priority`, `assigned_team`, `reasoning`, `clarification_needed`, `ticket_id`, `seconds` |

`ticket_id` is a UUID string when the ticket was persisted, `null` otherwise. `seconds` is the wall-clock time `route_ticket()` took, included for the manual-vs-AI comparison. Unlike the form, `/api/route` has no empty-message guard — a blank `message` is passed straight to `route_ticket()`, which returns its usual structured fallback rather than an error.

**Response fields** — every successful response follows the schema in `app/schema.py` (no additional properties allowed) and always contains:

| Field | Description |
|--------|-------------|
| `category` | Ticket category selected from the predefined taxonomy. |
| `priority` | High, Medium, or Low based on business impact. |
| `assigned_team` | Team responsible for handling the request. |
| `reasoning` | One-sentence explanation describing why the ticket was routed. |
| `clarification_needed` | Whether additional customer information is required before confident routing. |

## Example Input and Output

**Request**

```bash
curl -X POST http://127.0.0.1:8000/api/route \
  -H "Content-Type: application/json" \
  -d '{"message": "I was charged twice for my subscription this month and now I cannot even log in to check my account or dispute it."}'
```

**Response** (representative — `reasoning` is model-generated, so exact wording varies between calls; `category`/`priority`/`assigned_team` are stable at `temperature=0`)

```json
{
  "category": "Billing & Payments",
  "priority": "Medium",
  "assigned_team": "Billing Team",
  "reasoning": "The customer was charged twice this month for their subscription; a related account-access issue was also mentioned and may need separate follow-up.",
  "clarification_needed": false,
  "ticket_id": "3f1a9c2e-2b7e-4c1e-9a3b-7e6a2c9d4f10",
  "seconds": 1.42
}
```

This is the multi-category edge case: the message fits both "Billing & Payments" and "Account Access". The system selects the most appropriate primary category while mentioning additional issues in the reasoning.

**Out-of-scope input** — nothing is persisted, `ticket_id` is `null`:

```json
{
  "category": "Unclassified",
  "priority": "Low",
  "assigned_team": "None",
  "reasoning": "This message does not describe a support issue; please submit a specific problem you're experiencing with the product.",
  "clarification_needed": true,
  "ticket_id": null,
  "seconds": 0.91
}
```

## Edge Case Handling

The routing system explicitly handles several non-ideal customer inputs to improve reliability and avoid incorrect classifications.

| Input Scenario | System Behavior |
|----------------|-----------------|
| Empty ticket (web form) | Shows an inline "please enter a ticket" error; `route_ticket()` is never called. |
| Empty ticket (`/api/route`) | Returns `Unclassified`, assigns `Tier1 Support`, and requests additional information. |
| Greeting or casual conversation | Marks the request as out-of-scope, assigns no team, and does not persist the ticket. |
| Very short ticket (e.g. "Login") | Avoids guessing, requests clarification, and routes to Tier1 Support. |
| Brief but severe report (e.g. "my system is hacked") | Still routed as `priority: "High"` — missing detail doesn't downgrade a security/outage report. |
| Angry or emotional language | Determines priority from business impact rather than tone. |
| Multiple issues in one ticket | Returns a single category while mentioning additional issues in the reasoning. |
| Gibberish or invalid input | Returns `Unclassified`, assigns no team, and requests a valid support request. |
| Invalid model response | Retries automatically before returning a deterministic fallback response. |

## Design Decisions

- **Structured Outputs, then re-validate anyway.** A plain "return JSON" prompt with regex-parsing lets an invented category or malformed field reach the caller. `strict: true` with an enum-constrained schema (`app/schema.py`) stops the model from emitting anything outside the taxonomy; `route_ticket()` still runs `jsonschema.validate` plus a required-field check on top, since strict mode is a guarantee from the API, not from the caller's own code.
- **Fallback instead of raising.** A ticket has to end up somewhere. Letting a parse failure propagate as an exception loses it; returning a fixed fallback (`Tier1 Support`, `clarification_needed: true`) after 3 retries keeps a human able to triage it manually.
- **Conditional persistence.** Writing only tickets that are both successfully classified and assigned a real team keeps the `tickets` table free of gibberish, off-topic chat, and failed-classification noise.
- **FastAPI.** Pydantic request validation (`RouteRequest`) and auto-generated `/docs` come for free, useful for a service exercised both through a form and as a JSON API.
- **PostgreSQL + SQLAlchemy over SQLite.** UUID primary keys match the `ticket_id` already generated and returned to the caller, and a real server process is closer to an actual deployment than a file-based DB.
- **`MOCK_MODE` as an explicit escape hatch, not a silent default.** It lets the form and test suite run without an API key, but stays off by default and is called out in `.env.example` and this README so it's never mistaken for the real classification path.
- **Web-form manual-routing baseline is illustrative, not measured.** `MANUAL_ROUTING_SECONDS = 50` in `app/web.py` is a fixed placeholder used only to render a speedup line in the UI. The methodologically real manual-vs-AI comparison — hand-timed per ticket — is `scripts/compare_times.py` / `results/comparison.md`, not this constant.

## Challenges & Trade-offs

- **Single category, multiple issues.** A ticket can legitimately need two teams (billing + account access). Forcing exactly one category via a hardcoded precedence order is a routing simplification — the second issue only exists as a sentence in `reasoning`, not as structured data a second team could act on.
- **Tone-based priority is a prompt-level rule, not a code-level one.** Nothing in `router.py` verifies the model ignored a message's ALL-CAPS tone when scoring priority — that's enforced by the system prompt's rubric alone. `data/sample_tickets.json` tags 5 tickets with an expected severity so this can be spot-checked against the model's `reasoning`.
- **The batch timing script is directional, not a benchmark.** Calls in `run_batch.py` run sequentially and are dominated by OpenAI API latency — it produces one manual-vs-AI speedup number for a demo, not a controlled performance measurement.
- **No auth on `/api/route`.** Fine for a local demo; anyone who can reach the port can submit tickets and trigger persistence.
- **Deterministic categories, non-deterministic wording.** `temperature=0` keeps `category`/`priority`/`assigned_team` stable for the same input, but `reasoning` is free-text — two runs can produce differently worded, similarly-reasoned justifications.

## Future Improvements

- Authentication (API key or JWT) on `/api/route` before exposing it beyond a local demo.
- Parallelize `scripts/run_batch.py` instead of routing tickets sequentially.
- A read-only view over the `tickets` table instead of ad-hoc `psql` queries for inspecting stored data.
- Structured secondary-issue output (e.g. a list field) instead of folding multi-category tickets into free-text `reasoning`.
- Exponential backoff with jitter in the retry loop, instead of the current linear `0.5 * attempt` sleep, for handling API rate limits.
- `Dockerfile` / `docker-compose.yml` for the app + Postgres, in place of the manual Postgres.app + `createdb` setup step.
- A CI workflow running `pytest` on push.

## License

No license file is included in this repository. All rights are reserved by the author; the code is shared for review purposes.
