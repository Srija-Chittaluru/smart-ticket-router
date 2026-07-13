"""Build a before/after (manual vs AI) routing time comparison table.

Usage:
    python scripts/compare_times.py <ai_batch_results.json> <manual_timing.csv> [--out comparison.md]

<ai_batch_results.json> is produced by:
    python scripts/run_batch.py data/sample_tickets.json --out results/ai_batch_results.json

<manual_timing.csv> is data/manual_timing_template.csv filled in by hand:
for each ticket, have a person read the message and decide category/
priority/team with a stopwatch running, and record the seconds it took.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics


def load_ai_results(path: str) -> dict:
    with open(path) as f:
        data = json.load(f)
    return {r["id"]: r["seconds"] for r in data["results"]}


def load_manual_times(path: str) -> dict:
    times = {}
    with open(path) as f:
        for row in csv.DictReader(f):
            if row["manual_seconds"]:
                times[int(row["ticket_id"])] = float(row["manual_seconds"])
    return times


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("ai_results")
    parser.add_argument("manual_csv")
    parser.add_argument("--out", default="results/comparison.md")
    args = parser.parse_args()

    ai_times = load_ai_results(args.ai_results)
    manual_times = load_manual_times(args.manual_csv)

    common_ids = sorted(set(ai_times) & set(manual_times))
    if not common_ids:
        print("No overlapping ticket ids with manual times recorded yet.")
        return

    lines = ["| Ticket | Manual (s) | AI (s) | Speedup |", "|---|---|---|---|"]
    manual_vals, ai_vals = [], []
    for tid in common_ids:
        m, a = manual_times[tid], ai_times[tid]
        manual_vals.append(m)
        ai_vals.append(a)
        speedup = m / a if a else float("inf")
        lines.append(f"| {tid} | {m:.1f} | {a:.2f} | {speedup:.1f}x |")

    avg_manual = statistics.mean(manual_vals)
    avg_ai = statistics.mean(ai_vals)
    lines.append("")
    lines.append(f"**Average manual routing time:** {avg_manual:.1f}s")
    lines.append(f"**Average AI routing time:** {avg_ai:.2f}s")
    lines.append(f"**Average speedup:** {avg_manual / avg_ai:.1f}x")

    output = "\n".join(lines)
    print(output)

    import os

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        f.write(output + "\n")
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
