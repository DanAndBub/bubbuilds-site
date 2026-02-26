#!/usr/bin/env python3
"""
generate-dashboard-data.py
Reads the latest pipeline raw summary + scored items and writes site/dashboard-data.json.

Usage:
    python3 site/generate-dashboard-data.py

Run this after each pipeline cycle (collect + score) to refresh the dashboard.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent
RAW_DATA_DIR = REPO_ROOT / "pipeline" / "data" / "raw"
SCORED_DATA_DIR = REPO_ROOT / "pipeline" / "data" / "scored"
OUTPUT_FILE = SCRIPT_DIR / "dashboard-data.json"

# Threshold: items with avg_score >= this are considered "passed"
SCORE_THRESHOLD = 7.0

# Number of top stories to include
TOP_N = 5

# LLM cost estimate per item scored (rough estimate in USD)
# Adjust based on your actual model + token usage
COST_PER_ITEM_USD = 0.0005


# ── Helpers ───────────────────────────────────────────────────────────────────

def find_latest_date_dir(base_dir: Path) -> Path | None:
    """Return the most recent YYYY-MM-DD subdirectory, or None."""
    if not base_dir.exists():
        return None
    dirs = sorted(
        (d for d in base_dir.iterdir() if d.is_dir() and len(d.name) == 10),
        reverse=True
    )
    return dirs[0] if dirs else None


def load_json(path: Path) -> dict | list | None:
    """Load JSON from path, return None on failure."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"  [warn] Could not load {path}: {e}", file=sys.stderr)
        return None


def source_display_name(src: str) -> str:
    labels = {
        "reddit": "Reddit",
        "hackernews": "HN",
        "bluesky": "Bluesky",
        "brave_search": "Brave",
        "clawhub": "ClawHub",
        "github": "GitHub",
    }
    return labels.get(src, src)


# ── Main ──────────────────────────────────────────────────────────────────────

def build_dashboard_data() -> dict:
    now_iso = datetime.now(timezone.utc).isoformat()

    # ── Find latest data directories ──────────────────────────────────────────
    raw_dir = find_latest_date_dir(RAW_DATA_DIR)
    scored_dir = find_latest_date_dir(SCORED_DATA_DIR)

    if raw_dir is None:
        print("[error] No raw data directories found.", file=sys.stderr)
        sys.exit(1)

    date_str = raw_dir.name  # e.g. "2026-02-25"
    print(f"[info] Using raw data:    {raw_dir}")
    print(f"[info] Using scored data: {scored_dir}")

    # ── Load raw summary ───────────────────────────────────────────────────────
    summary_path = raw_dir / "_summary.json"
    summary = load_json(summary_path) or {}

    total_collected = summary.get("total_items", 0)
    collected_at = summary.get("collected_at")
    collectors = summary.get("collectors", {})
    sources_active = sum(
        1 for c in collectors.values() if c.get("status") == "success"
    )

    # Raw source breakdown from summary (more reliable than scoring leftovers)
    raw_source_breakdown = {
        src: info.get("count", 0)
        for src, info in collectors.items()
        if info.get("status") == "success"
    }

    # ── Load scored items ──────────────────────────────────────────────────────
    total_scored = 0
    passed_threshold = 0
    top_stories = []
    scored_source_breakdown = {}
    scored_at = None

    if scored_dir is not None:
        scored_path = scored_dir / "all_scored.json"
        scored_items = load_json(scored_path)

        if scored_items and isinstance(scored_items, list):
            total_scored = len(scored_items)

            # Track latest scored_at timestamp
            timestamps = [
                item.get("scored_at") for item in scored_items if item.get("scored_at")
            ]
            if timestamps:
                scored_at = max(timestamps)

            # Count passed threshold
            passed = [
                item for item in scored_items
                if isinstance(item.get("avg_score"), (int, float))
                and item["avg_score"] >= SCORE_THRESHOLD
            ]
            passed_threshold = len(passed)

            # Source breakdown from scored items
            for item in scored_items:
                src = item.get("source", "unknown")
                scored_source_breakdown[src] = scored_source_breakdown.get(src, 0) + 1

            # Top N by avg_score
            sorted_items = sorted(
                scored_items,
                key=lambda x: x.get("avg_score", 0),
                reverse=True
            )
            for item in sorted_items[:TOP_N]:
                top_stories.append({
                    "title": item.get("title") or item.get("text", "")[:120] or "(no title)",
                    "source": item.get("source", "unknown"),
                    "score": item.get("avg_score"),
                    "url": item.get("url") or item.get("uri") or "",
                })

    # ── Determine pipeline status ──────────────────────────────────────────────
    # Heuristic: if scored_at is recent (within last 30 min), pipeline ran today
    status = "idle"
    last_updated = scored_at or collected_at or now_iso

    # ── Estimated cost ─────────────────────────────────────────────────────────
    estimated_cost = round(total_scored * COST_PER_ITEM_USD, 4) if total_scored else 0.0

    # ── Use raw source breakdown (more accurate per-collector counts) ──────────
    # Fall back to scored breakdown if raw is empty
    source_breakdown = raw_source_breakdown if raw_source_breakdown else scored_source_breakdown

    # ── Assemble output ────────────────────────────────────────────────────────
    data = {
        "generatedAt": now_iso,
        "date": date_str,
        "status": status,
        "lastUpdated": last_updated,
        "totalCollected": total_collected,
        "totalScored": total_scored,
        "passedThreshold": passed_threshold,
        "sourcesActive": sources_active,
        "sourceBreakdown": source_breakdown,
        "topStories": top_stories,
        "estimatedCost": estimated_cost,
    }

    return data


def main():
    print("[generate-dashboard-data] Starting...")
    data = build_dashboard_data()

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"[generate-dashboard-data] Written to {OUTPUT_FILE}")
    print(f"  date:             {data['date']}")
    print(f"  totalCollected:   {data['totalCollected']}")
    print(f"  totalScored:      {data['totalScored']}")
    print(f"  passedThreshold:  {data['passedThreshold']}")
    print(f"  sourcesActive:    {data['sourcesActive']}")
    print(f"  estimatedCost:    ${data['estimatedCost']:.4f}")
    print(f"  topStories:       {len(data['topStories'])} items")
    print("  sourceBreakdown:", data["sourceBreakdown"])


if __name__ == "__main__":
    main()
