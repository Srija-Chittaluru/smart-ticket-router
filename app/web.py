from __future__ import annotations

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

MANUAL_ROUTING_SECONDS = 50

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


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
        "index.html", {"request": request, "result": None, "message": "", "elapsed": None}
    )


@app.post("/", response_class=HTMLResponse)
def submit(request: Request, message: str = Form(""), db: Session = Depends(get_db)):
    result, elapsed = _route_and_persist(message, db)
    speedup = round(MANUAL_ROUTING_SECONDS / elapsed, 1) if elapsed > 0 else None
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "result": result,
            "message": message,
            "elapsed": round(elapsed, 2),
            "manual_seconds": MANUAL_ROUTING_SECONDS,
            "speedup": speedup,
        },
    )


@app.post("/api/route")
def api_route(payload: RouteRequest, db: Session = Depends(get_db)):
    result, elapsed = _route_and_persist(payload.message, db)
    return {**result, "seconds": round(elapsed, 3)}
