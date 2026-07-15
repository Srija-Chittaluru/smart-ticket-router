from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import Ticket, get_db, init_db
from app.router import route_ticket

load_dotenv()

app = FastAPI()

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

DEMO_TICKETS_PATH = Path(__file__).parent.parent / "data" / "sample_tickets.json"
with open(DEMO_TICKETS_PATH) as f:
    DEMO_TICKETS = json.load(f)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


class RouteRequest(BaseModel):
    message: str = ""


def _route_and_persist(message: str, db: Session) -> tuple[dict, float]:
    """Classify the ticket, then save it to Postgres unless the AI
    classification failed (is_fallback) or no team could be assigned
    (assigned_team == "None"). Returns (result, elapsed_seconds).
    """
    start = time.perf_counter()
    result = route_ticket(message)
    elapsed = time.perf_counter() - start

    is_fallback = result.pop("is_fallback", False)
    unassigned = result["assigned_team"] == "None"
    ticket_id = None if unassigned else uuid.uuid4()

    if not is_fallback and not unassigned:
        db.add(
            Ticket(
                ticket_id=ticket_id,
                ticket_text=message,
                category=result["category"],
                priority=result["priority"],
                assigned_team=result["assigned_team"],
                reasoning=result["reasoning"],
            )
        )
        db.commit()

    result["ticket_id"] = str(ticket_id) if ticket_id else None
    return result, elapsed


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "result": None, "message": "", "elapsed": None, "demo_tickets": DEMO_TICKETS},
    )


@app.post("/", response_class=HTMLResponse)
def submit(request: Request, message: str = Form(""), db: Session = Depends(get_db)):
    if not message.strip():
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "result": None,
                "message": message,
                "error": "Please enter a ticket description before submitting.",
                "demo_tickets": DEMO_TICKETS,
            },
        )

    result, elapsed = _route_and_persist(message, db)
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "result": result,
            "message": message,
            "elapsed": round(elapsed, 2),
            "demo_tickets": DEMO_TICKETS,
        },
    )


@app.post("/run-demo", response_class=HTMLResponse)
async def run_demo(request: Request):
    """Route a hand-picked subset of the 20 demo tickets directly through
    route_ticket() (no DB persistence, matching scripts/run_batch.py) and
    render the results as a table alongside the sidebar."""
    form = await request.form()
    selected_ids = {int(tid) for tid in form.getlist("ticket_ids")}
    selected_tickets = [t for t in DEMO_TICKETS if t["id"] in selected_ids]

    demo_results = []
    for ticket in selected_tickets:
        start = time.perf_counter()
        result = route_ticket(ticket["message"])
        elapsed = time.perf_counter() - start
        demo_results.append(
            {
                "id": ticket["id"],
                "message": ticket["message"],
                "category": result["category"],
                "priority": result["priority"],
                "assigned_team": result["assigned_team"],
                "clarification_needed": result["clarification_needed"],
                "seconds": round(elapsed, 2),
            }
        )

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "result": None,
            "message": "",
            "demo_tickets": DEMO_TICKETS,
            "demo_results": demo_results,
            "selected_ids": selected_ids,
        },
    )


@app.post("/api/route")
def api_route(payload: RouteRequest, db: Session = Depends(get_db)):
    result, elapsed = _route_and_persist(payload.message, db)
    return {**result, "seconds": round(elapsed, 3)}
