"""Batch-route the demo tickets and record timing, for the manual-vs-AI
comparison in scripts/compare_times.py.

Usage:
    python scripts/run_batch.py data/sample_tickets.json --out results/ai_batch_results.json
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.router import route_ticket

load_dotenv()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", help="Path to JSON file: list of strings or {id, message} objects")
    parser.add_argument("--out", dest="out_path", default=None, help="Write results+timing summary to this JSON file")
    args = parser.parse_args()

    with open(args.path) as f:
        tickets = json.load(f)

    results = []
    durations = []
    for i, ticket in enumerate(tickets, start=1):
        message = ticket["message"] if isinstance(ticket, dict) else ticket
        label = ticket.get("id", i) if isinstance(ticket, dict) else i

        start = time.perf_counter()
        classification = route_ticket(message)
        elapsed = round(time.perf_counter() - start, 3)

        durations.append(elapsed)
        results.append({"id": label, "message": message, "seconds": elapsed, **classification})
        print(
            f"[{label}] ({elapsed:.2f}s) {classification['category']} / "
            f"{classification['priority']} -> {classification['assigned_team']}"
        )

    summary = {
        "count": len(durations),
        "total_seconds": round(sum(durations), 3),
        "avg_seconds": round(statistics.mean(durations), 3) if durations else 0,
        "median_seconds": round(statistics.median(durations), 3) if durations else 0,
    }
    print("\n--- AI routing timing summary ---")
    print(json.dumps(summary, indent=2))

    if args.out_path:
        os.makedirs(os.path.dirname(args.out_path) or ".", exist_ok=True)
        with open(args.out_path, "w") as f:
            json.dump({"summary": summary, "results": results}, f, indent=2)
        print(f"\nWrote {args.out_path}")


if __name__ == "__main__":
    main()
