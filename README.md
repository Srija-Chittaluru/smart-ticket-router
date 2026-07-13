# Smart Ticket Router

An LLM-powered support ticket triage service. Given a raw customer support
message, it returns structured JSON: `category`, `priority` (High/Medium/Low),
`assigned_team`, and a one-line `reasoning` for the decision — plus a
`clarification_needed` flag for messages too vague to confidently route.

Built for the "Smart Ticket Router" mission: prompt design for structured
output, JSON schema enforcement, handling AI unreliability, and building a
reusable classification service behind a web form. Every successfully
routed ticket is also persisted to PostgreSQL with a unique ticket ID.

## How it works

- `app/schema.py` — the taxonomy (categories/priorities/teams), the strict
  JSON Schema sent to the model, and the system prompt with explicit rubrics
  for priority (impact-based, not tone-based) and edge-case handling.
- `app/router.py` — `route_ticket(message)`, the reusable core function.
  Calls the OpenAI Chat Completions API with `response_format:
  json_schema` (strict mode) so the model is constrained to emit only valid,
  schema-conforming JSON. On top of that, it independently re-validates the
  parsed JSON against the schema and required fields, retries up to 3 times
  with backoff on a parse/validation/API failure, and — if every attempt
  still fails — returns a safe, clearly-labeled fallback classification
  instead of raising, so a malformed AI response can never crash the caller.
- `app/web.py` + `app/templates/index.html` — a minimal FastAPI web form
  (and a `/api/route` JSON endpoint) for live demoing. Generates a fresh
  UUID per request, saves successfully classified tickets to PostgreSQL,
  and returns the `ticket_id` alongside the classification.
- `app/db.py` — SQLAlchemy engine/session setup, the `Ticket` ORM model
  (mirrors the `tickets` table), `init_db()` (creates the table on
  startup), and `get_db()` (per-request database session for FastAPI).
- `scripts/run_batch.py` — batch-routes the demo tickets by calling
  `route_ticket()` directly and records timing, for the manual-vs-AI
  comparison below.
- `data/sample_tickets.json` — 20 demo tickets, including the 3 required
  edge cases and 5 tickets with known/obvious severity for priority
  sanity-checking.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env and set OPENAI_API_KEY=sk-...
```

Requires Python 3.9+ and an OpenAI API key with access to a model that
supports Structured Outputs (default: `gpt-4o-mini`, configurable via
`OPENAI_MODEL` in `.env`).

### Database

Requires a running PostgreSQL server. Easiest local option on Mac is
[Postgres.app](https://postgresapp.com/) — install it, click **Initialize**,
then create the database:

```bash
createdb ticket_router
```

Set `DATABASE_URL` in `.env` to match your setup, e.g.:

```
DATABASE_URL=postgresql://<your-username>@localhost:5432/ticket_router
```

The `tickets` table is created automatically on app startup (`init_db()`
in `app/db.py`) — no manual migration step needed.

## Running it

### Web form

Make sure PostgreSQL is running first (see Database setup above), then:

```bash
uvicorn app.web:app --reload
```

Open `http://127.0.0.1:8000`, paste a ticket, click **Route Ticket**. There's
also a JSON API at `POST /api/route` with body `{"message": "..."}`, for
scripted testing — and interactive API docs at `http://127.0.0.1:8000/docs`.

### Batch (the 20 demo tickets)

```bash
mkdir -p results
python scripts/run_batch.py data/sample_tickets.json --out results/ai_batch_results.json
```

Prints each ticket's routing decision and timing, then a timing summary
(count/total/avg/median seconds), and writes full results to
`results/ai_batch_results.json`.

### Offline/dev mode (no API key)

Set `MOCK_MODE=1` (in `.env` or the shell) to route tickets through a
deterministic keyword-based stand-in instead of calling OpenAI. This is
only for developing/testing the web form without burning API credits —
**do not use it for the mentor demo**, since the mission is about the
LLM's structured output behavior specifically.

### Tests

```bash
pytest tests/ -v
```

Covers: empty input, very short input, malformed/incomplete LLM JSON
(fallback path), and a well-formed response passing through — all without
needing a live API key (LLM calls are mocked).

## Handling AI unreliability

Three layers, from strongest to last-resort:

1. **Structured Outputs (strict schema)** at the API level — the model is
   constrained token-by-token to only produce JSON matching the schema in
   `app/schema.py` (fixed enums for category/priority/team, all 5 fields
   required, no extra fields).
2. **Independent re-validation** in `route_ticket` — `json.loads` +
   `jsonschema.validate` + an explicit required-field check, even though
   strict mode should already guarantee this. Defense in depth against API
   version differences or client bugs.
3. **Retry with backoff, then safe fallback** — up to 3 attempts; if all
   fail, returns a fixed fallback response (`category: General Inquiry`,
   `priority: Medium`, `assigned_team: Tier 1 Support`,
   `clarification_needed: true`, and a `reasoning` that says classification
   failed and it was routed for manual triage). The caller never sees an
   exception or a crash for a well-formed input string.

`route_ticket()` also tags its return value with `is_fallback` (`True` only
on that last-resort path). `app/web.py` uses this to skip persistence
entirely on failed classifications — see below.

## Persisting tickets to PostgreSQL

Every request gets a fresh `uuid.uuid4()` ticket ID, generated in
`app/web.py`, even if the exact same message is submitted twice. That ID
is saved to the `tickets` table (via the `Ticket` model in `app/db.py`)
**only when classification succeeded** — i.e. `is_fallback` is `False`.
If the AI response was invalid/unparseable after all retries, nothing is
written to the database, per the fallback handling above.

The saved row (`ticket_id`, `ticket_text`, `category`, `priority`,
`assigned_team`, `reasoning`, `created_at`) and the `ticket_id` are
returned alongside the existing JSON response, and shown in the web UI.

To inspect what's been stored:

```bash
psql -d ticket_router -c "SELECT ticket_id, ticket_text, category, priority, assigned_team, created_at FROM tickets ORDER BY created_at DESC;"
```

## Edge cases (required deliverable)

| # | Case | Input | Handling |
|---|---|---|---|
| 1 | Angry / emotional tone | `"This is RIDICULOUS, nothing works and I've been waiting 3 days!!!"` | Prompt explicitly instructs the model to base priority only on described impact ("nothing works" + 3-day duration = High), not on tone/punctuation/caps. Reasoning cites the outage duration, not the anger. |
| 2 | Very short / vague message | `"broken"` | Prompt instructs the model to set `clarification_needed: true`, default to `Medium` priority (unknown severity — don't guess High or Low), route to `Tier 1 Support` for triage, and explain in `reasoning` that more detail is needed. No crash. |
| 3 | Ambiguous ticket (fits 2 categories) | `"I was charged twice for my subscription this month and now I can't even log in to check my account or dispute it."` (Billing & Payments vs. Account Access) | Prompt instructs the model to pick the category tied to the *primary blocking problem* and name the rejected alternative in `reasoning` (e.g. picks Account Access because login is the blocker preventing the customer from resolving the billing issue themselves). |

All three are ticket `id: 1, 2, 3` in `data/sample_tickets.json`.

## Before/after: manual vs. AI routing time

**Manual routing** (a support agent reading a ticket and deciding
category/priority/team by hand) realistically takes anywhere from ~30-90+
seconds per ticket depending on complexity/ambiguity, plus queueing delay
before an agent even picks it up. **AI routing** via this service takes
~1-3 seconds end-to-end (dominated by the API call), with no queueing
delay since it runs synchronously on ticket arrival.

To produce real numbers for the demo instead of just estimates:

1. Run the AI side and capture timing automatically:
   ```bash
   python scripts/run_batch.py data/sample_tickets.json --out results/ai_batch_results.json
   ```
2. Fill in `data/manual_timing_template.csv` by having a person (you, or a
   teammate acting as a support agent) read each of the 20 tickets and
   time, with a stopwatch, how long it takes them to decide the
   category/priority/team by hand.
3. Generate the comparison table:
   ```bash
   python scripts/compare_times.py results/ai_batch_results.json data/manual_timing_template.csv
   ```
   This prints and writes `results/comparison.md` — a per-ticket
   manual-vs-AI table plus average speedup, ready to show the mentor.

## Priority defensibility

Sample tickets 4, 5, 6, 7, 8 in `data/sample_tickets.json` are tagged with
their expected severity (`note` field) so the mentor can spot-check the
model's `reasoning` against an independent, obvious ground truth:

- #4 (total outage, business-critical) → expect **High**
- #5 (feature request, nothing broken) → expect **Low**
- #6 (bug with a workaround) → expect **Medium**
- #7 (billing error, no urgency) → expect **Medium**
- #8 (suspected account compromise) → expect **High**

## Project structure

```
app/
  schema.py       taxonomy + JSON schema + system prompt
  router.py       route_ticket() — the reusable core service
  db.py           SQLAlchemy engine/session, Ticket model, init_db(), get_db()
  web.py          FastAPI web form + JSON API + ticket persistence
  templates/
    index.html
data/
  sample_tickets.json          20 demo tickets (incl. edge cases)
  manual_timing_template.csv   fill in by hand for the before/after comparison
scripts/
  run_batch.py                 batch-routes demo tickets via route_ticket(), records timing
  compare_times.py             builds the before/after table
tests/
  test_router.py
```