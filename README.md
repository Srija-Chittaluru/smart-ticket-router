# Smart Ticket Router

A FastAPI service that classifies raw customer support messages into a fixed
routing taxonomy — category, priority, assigned team, and a one-sentence
justification — using the OpenAI API's structured output mode. Tickets that
are successfully classified and assigned to a real team are persisted to
PostgreSQL with a generated ticket ID; everything else (gibberish, off-topic
chat, failed classification) is returned to the caller but never written to
the database.

## Problem Statement

Manually triaging a support ticket means a human reads the message and
decides, by judgment, what category it belongs to, how urgent it is, and
which team should own it. That's slow, inconsistent between agents, and
easy to get wrong under an angry or vague message. It also doesn't produce
structured data — "someone read it and decided" doesn't give you a
`category` / `priority` / `assigned_team` you can route or report on.

Handing this to an LLM directly doesn't solve it either: a plain
prompt-and-parse approach can return prose instead of JSON, invent a
category that isn't in your taxonomy, or fail outright — and a triage
service that raises an exception on a malformed ticket has thrown the
ticket away. This project constrains the model to a fixed schema, checks
its own output rather than trusting the API, and always returns a usable
result even when classification fails.

## Features

- **Schema-constrained classification** — `category`, `priority`,
  `assigned_team`, `reasoning`, and `clarification_needed` are generated
  under an OpenAI Structured Outputs schema (`strict: true`), so the model
  can only emit values from the fixed enums defined in `app/schema.py`.
- **Independent re-validation** — Responses are independently validated
  against the JSON schema before being returned to the client.
- **Retry + deterministic fallback** — up to 3 attempts with linear
  backoff on a parse/validation/API failure; if every attempt fails, a
  fixed fallback record (`Tier1 Support`, `clarification_needed: true`) is
  returned instead of an exception.
- **Priority determined from business impact** — Priority is assigned solely
  from the described impact and urgency rather than emotional tone,
  capitalization, or punctuation.
- **Rule-based multi-issue routing** — Tickets containing multiple issues
  are mapped to a single category using a predefined internal precedence
  order while mentioning additional issues in the reasoning.
- **Out-of-scope / gibberish detection** — greetings, small talk, and
  unrelated requests (jokes, weather, translation) are recognized as not
  being support tickets at all and routed to `assigned_team: "None"`.
- **Selective persistence** — a ticket is only written to Postgres when
  classification succeeded *and* a real team was assigned; otherwise
  `ticket_id` comes back `null` and nothing is stored.
- **Two entry points, one core function** — an HTML form and a JSON API
  (`POST /api/route`) both call the same `route_ticket()`.
- **Offline dev mode** — `MOCK_MODE=1` swaps the OpenAI call for a
  deterministic keyword classifier, for developing/testing the form and
  running the test suite without an API key or cost.
- **Batch timing tooling** — `scripts/run_batch.py` routes a JSON file of
  tickets and records per-ticket timing; `scripts/compare_times.py` joins
  that against hand-timed manual routing to produce a manual-vs-AI speedup
  table.
- **Clarification-aware routing** — Instead of guessing when a ticket is
  incomplete or ambiguous, the system explicitly indicates that additional
  information is required before confident routing.

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

`app/schema.py` owns the taxonomy and the system prompt; `app/router.py`
owns the call/validate/retry/fallback logic and is the only place that
talks to OpenAI; `app/web.py` owns the HTTP layer and the persistence
decision; `app/db.py` owns the SQLAlchemy model and session lifecycle.

## Routing Rules

## Routing Rules

The routing system follows a fixed set of rules to ensure predictable and consistent classifications.

- Exactly one category is returned for every ticket.
- Priority is determined from the customer's described impact, not emotional tone.
- Ambiguous or incomplete tickets request clarification instead of guessing.
- Tickets containing multiple issues are routed to a single primary category while additional concerns are mentioned in the reasoning.
- Every successful response conforms to the predefined JSON schema.
- Out-of-scope requests are assigned to `None` and are not persisted.
  
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

| Path | What it's for |
|---|---|
| `app/schema.py` | The single source of truth for the routing taxonomy (`CATEGORIES`, `PRIORITIES`, `TEAMS`), the JSON Schema sent to OpenAI, and the system prompt — including the priority rubric, the category precedence order, and the out-of-scope guardrail. Changing what the model can output means changing this file. |
| `app/router.py` | `route_ticket(message)` — the only function that calls OpenAI. Handles the mock-mode branch, the retry loop, response validation, and the fallback path. Has no FastAPI or database dependency, so it's callable from the web app, the batch script, or a test, unchanged. |
| `app/db.py` | The `Ticket` ORM model (mirrors the `tickets` table 1:1 with `route_ticket()`'s output fields), `init_db()` (called on FastAPI startup, creates the table if missing), and `get_db()` (per-request session dependency). |
| `app/web.py` | FastAPI app: `GET /` and `POST /` for the HTML form, `POST /api/route` for JSON. `_route_and_persist()` is the shared logic that decides, after classification, whether the ticket gets written to Postgres. |
| `data/sample_tickets.json` | 20 tickets used for manual QA and the batch script — 3 are the required edge cases (angry tone, vague message, multi-category), 5 are tagged with an expected severity for spot-checking the model's priority calls. |
| `scripts/run_batch.py` | Calls `route_ticket()` directly (no HTTP) for every ticket in a JSON file, times each call, and writes a results file consumed by `compare_times.py`. |
| `scripts/compare_times.py` | Reads the batch results plus a manually-filled CSV of stopwatch times and produces `results/comparison.md` — a per-ticket manual-vs-AI table and average speedup. |

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# then edit .env — see Environment Variables below
```

Requires Python 3.9+ and an OpenAI API key with access to a model that
supports Structured Outputs.

**Database setup** — requires a running PostgreSQL server. On macOS,
[Postgres.app](https://postgresapp.com/) is the fastest way to get one
locally: install it, click **Initialize**, then:

```bash
createdb ticket_router
```

The `tickets` table itself is created automatically on app startup
(`init_db()` in `app/db.py`) — there's no separate migration step.

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

Open `http://127.0.0.1:8000`, submit a ticket, see the routing result.
Interactive API docs (auto-generated by FastAPI) are at
`http://127.0.0.1:8000/docs`.

**Batch run over the 20 demo tickets**

```bash
mkdir -p results
python scripts/run_batch.py data/sample_tickets.json --out results/ai_batch_results.json
```

Prints each ticket's decision and timing, then a summary
(count/total/avg/median seconds).

**Manual-vs-AI comparison**

Fill in `manual_seconds` for each row in `data/manual_timing_template.csv`
(a person timing themselves triaging each ticket by hand), then:

```bash
python scripts/compare_times.py results/ai_batch_results.json data/manual_timing_template.csv
```

Writes `results/comparison.md` with a per-ticket speedup table.

**Offline / no API key**

```bash
MOCK_MODE=1 uvicorn app.web:app --reload
```

Routes through `_mock_classify()` (keyword-based) instead of OpenAI — for
developing against the form without spending API credits. Not a
substitute for the real classification path.

**Tests**

```bash
pytest tests/ -v
```

Covers empty input, mock-mode short input, malformed/incomplete LLM JSON
(fallback path), and a well-formed response passing through — the LLM
call is mocked in every case, so no API key is needed to run the suite.

## API Endpoints

| Method | Path | Body | Response |
|---|---|---|---|
| `GET` | `/` | — | Renders the HTML form |
| `POST` | `/` | form-encoded `message` | Re-renders the form with the routing result |
| `POST` | `/api/route` | `{"message": "string"}` | JSON: `category`, `priority`, `assigned_team`, `reasoning`, `clarification_needed`, `ticket_id`, `seconds` |

`ticket_id` is a UUID string when the ticket was persisted, `null`
otherwise. `seconds` is the wall-clock time `route_ticket()` took,
included for the manual-vs-AI comparison.

## Output Guarantees

Every successful routing response follows the predefined JSON schema and always contains the following fields:

| Field | Description |
|--------|-------------|
| `category` | Ticket category selected from the predefined taxonomy. |
| `priority` | High, Medium, or Low based on business impact. |
| `assigned_team` | Team responsible for handling the request. |
| `reasoning` | One-sentence explanation describing why the ticket was routed. |
| `clarification_needed` | Indicates whether additional customer information is required before confident routing. |

The schema does not allow additional properties, ensuring a predictable response format for downstream systems.

## Example Input and Output

**Request**

```bash
curl -X POST http://127.0.0.1:8000/api/route \
  -H "Content-Type: application/json" \
  -d '{"message": "I was charged twice for my subscription this month and now I cannot even log in to check my account or dispute it."}'
```

**Response** (representative — `reasoning` is model-generated, so exact
wording varies between calls; `category`/`priority`/`assigned_team` are
stable at `temperature=0`)

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

This is the multi-category edge case: the message fits both "Billing &
Payments" and "Account Access". The system selects the most appropriate 
primary category while mentioning additional issues in the reasoning.

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

## Edge Cases

## Edge Case Handling

The routing system explicitly handles several non-ideal customer inputs to improve reliability and avoid incorrect classifications.

| Input Scenario | System Behavior |
|----------------|-----------------|
| Empty ticket | Returns `Unclassified`, assigns `Tier1 Support`, and requests additional information. |
| Greeting or casual conversation | Marks the request as out-of-scope, assigns no team, and does not persist the ticket. |
| Very short ticket (e.g. "Login") | Avoids guessing, requests clarification, and routes to Tier1 Support. |
| Angry or emotional language | Determines priority from business impact rather than tone. |
| Multiple issues in one ticket | Returns a single category while mentioning additional issues in the reasoning. |
| Gibberish or invalid input | Returns `Unclassified`, assigns no team, and requests a valid support request. |
| Invalid model response | Retries automatically before returning a deterministic fallback response. |

## Design Decisions

- **Structured Outputs over prompt-and-parse.** Asking the model to
  "return JSON" in plain text and regex-parsing the reply means any
  invented category or malformed field reaches the caller. `strict: true`
  with an enum-constrained schema (`app/schema.py`) makes the model unable
  to emit anything outside the fixed taxonomy in the first place.
- **Re-validating anyway.** `route_ticket()` runs `jsonschema.validate`
  and a required-field check on top of strict mode. Strict mode is a
  guarantee from the API, not from the caller's own code — re-checking is
  cheap insurance against a provider-side edge case the caller can't see.
- **Fallback instead of raising.** A support ticket has to end up
  somewhere. Letting a parse failure propagate as an exception means the
  ticket is lost; returning a fixed fallback (`Tier1 Support`,
  `clarification_needed: true`) after 3 retries keeps a human able to
  triage it manually.
- **Conditional persistence.** Only writing tickets that are both
  successfully classified and assigned a real team keeps the `tickets`
  table free of gibberish, off-topic chat, and failed-classification
  noise — so anything queried from it later is an actual, actionable
  ticket.
- **FastAPI.** Pydantic request validation (`RouteRequest`) and
  auto-generated `/docs` come for free, which matters for a service meant
  to be exercised both through a form and as a JSON API.
- **PostgreSQL + SQLAlchemy over SQLite.** UUID primary keys match the
  `ticket_id` already being generated and returned to the caller, and a
  real server process is closer to how this would actually be deployed
  than a file-based database.
- **`MOCK_MODE` as an explicit escape hatch, not a silent default.** It
  exists so the form and test suite don't require an API key, but it's
  off by default and called out in both `.env.example` and this README so
  it's never mistaken for the real classification path.

## Challenges & Trade-offs

- **Single category, multiple issues.** A ticket can legitimately need two
  teams (billing + account access). Forcing exactly one category via a
  hardcoded precedence order in the prompt is a routing simplification,
  not a routing engine — the second issue only exists as a sentence in
  `reasoning`, not as structured data a second team could act on.
- **Tone-based priority is a prompt-level rule, not a code-level one.**
  Nothing in `router.py` can verify the model actually ignored a message's
  ALL-CAPS tone when scoring priority — that's enforced entirely by the
  system prompt's explicit rubric. `data/sample_tickets.json` tags 5
  tickets with an expected severity specifically so this can be spot-checked
  by a human against the model's actual `reasoning`, rather than assumed.
- **The batch timing script is directional, not a benchmark.** Each call
  in `run_batch.py` runs sequentially and its timing is dominated by
  network latency to the OpenAI API — it's meant to produce one
  manual-vs-AI speedup number for a demo, not a controlled performance
  measurement.
- **No auth on `/api/route`.** Fine for a local demo; anyone who can reach
  the port can submit tickets and trigger persistence.
- **Deterministic categories, non-deterministic wording.** `temperature=0`
  makes `category`/`priority`/`assigned_team` stable for the same input,
  but `reasoning` is still free-text generation — two runs of the same
  message can produce differently worded (though similarly-reasoned)
  justifications.

## Future Improvements

- Authentication (API key or JWT) on `/api/route` before exposing it
  beyond a local demo.
- Parallelize `scripts/run_batch.py` instead of routing tickets
  sequentially.
- A read-only view over the `tickets` table instead of ad-hoc `psql`
  queries for inspecting stored data.
- Structured secondary-issue output (e.g. a list field) instead of
  folding multi-category tickets into free-text `reasoning`.
- Exponential backoff with jitter in the retry loop, instead of the
  current linear `0.5 * attempt` sleep, for handling API rate limits.
- `Dockerfile` / `docker-compose.yml` for the app + Postgres, in place of
  the manual Postgres.app + `createdb` setup step.
- A CI workflow running `pytest` on push.

## License

No license file is included in this repository. All rights are reserved
by the author; the code is shared for review purposes.
