#!/usr/bin/env python3
"""
Polls NSE's public corporate-announcements API for anything filed since the
last check that looks like an earnings-call transcript, and fires a
repository_dispatch event at a downstream repo when it finds one.

Deliberately imprecise. This script's only job is "did anything change" —
it does not (and should not) try to be the authoritative source of truth
about which filing matters, which company it belongs to, or whether it's
already been processed. That logic lives downstream, in whatever consumes
the dispatch event, which can dedupe properly. A false-positive trigger here
costs the downstream system one cheap "nothing new" check; a false negative
here costs a real filing being missed until the next scheduled run. Erring
towards over-triggering is the correct tradeoff.

No API key, no scraping tricks — this hits the same public JSON endpoint
NSE's own website uses, with ordinary browser headers and a cookie warm-up
(NSE 403s a cold request without one).
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

NSE_HOME = "https://www.nseindia.com/companies-listing/corporate-filings-announcements"
NSE_API = "https://www.nseindia.com/api/corporate-announcements"

STATE_FILE = Path(__file__).parent / "state.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}

# Same pattern a downstream consumer of this event would apply more
# carefully — this is just a cheap pre-filter so we don't dispatch on every
# board-meeting-intimation or dividend notice NSE publishes.
import re  # noqa: E402

TRANSCRIPT_PATTERN = re.compile(
    r"\b(transcript|earnings call|conference call|con[\s-]?call|analyst call|investor call)\b",
    re.I,
)


def load_state() -> Dict[str, Any]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            pass
    return {"last_seen": None}


def save_state(state: Dict[str, Any]) -> None:
    STATE_FILE.write_text(json.dumps(state))


def new_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    # Cookie warm-up: NSE's JSON API rejects a request with no prior visit.
    try:
        session.get("https://www.nseindia.com", timeout=20)
        session.get(NSE_HOME, timeout=20)
    except requests.RequestException:
        pass  # a failed warm-up just means the API call below will also fail and get caught
    return session


def fetch_today(session: requests.Session) -> List[Dict[str, Any]]:
    today = datetime.now(timezone.utc).strftime("%d-%m-%Y")
    response = session.get(
        NSE_API,
        headers={"Accept": "application/json", "Referer": NSE_HOME},
        params={"index": "equities", "from_date": today, "to_date": today},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, list) else []


def looks_like_transcript(row: Dict[str, Any]) -> bool:
    subject = f"{row.get('desc', '')} {row.get('attchmntText', '')}"
    return bool(TRANSCRIPT_PATTERN.search(subject))


def filed_at(row: Dict[str, Any]) -> str:
    return str(row.get("an_dt") or row.get("sort_date") or "")


def _parse_filed_at(value: str) -> Optional[datetime]:
    """NSE's "DD-Mon-YYYY HH:MM:SS" strings don't sort correctly as plain
    text across a month boundary (e.g. "01-Sep-2026" < "31-Aug-2026" as a
    string, since the day digits are compared before the month letters) —
    parse to a real datetime before comparing."""
    try:
        return datetime.strptime(value, "%d-%b-%Y %H:%M:%S")
    except (ValueError, TypeError):
        return None


def dispatch(target_repo: str, token: str) -> None:
    response = requests.post(
        f"https://api.github.com/repos/{target_repo}/dispatches",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json={"event_type": "new-filing-detected"},
        timeout=20,
    )
    response.raise_for_status()


def main() -> int:
    target_repo = os.environ.get("TARGET_REPO")
    token = os.environ.get("DISPATCH_TOKEN")
    if not target_repo or not token:
        print("TARGET_REPO and DISPATCH_TOKEN must both be set", file=sys.stderr)
        return 1

    state = load_state()
    last_seen: Optional[str] = state.get("last_seen")

    try:
        session = new_session()
        rows = fetch_today(session)
    except requests.RequestException as exc:
        # A transient NSE failure is not worth failing the whole workflow
        # over — the next run five minutes later tries again.
        print(f"fetch failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 0

    candidates = [r for r in rows if looks_like_transcript(r)]
    if not candidates:
        print("nothing new")
        return 0

    dated = [(filed_at(r), _parse_filed_at(filed_at(r))) for r in candidates]
    newest, newest_dt = max(dated, key=lambda pair: pair[1] or datetime.min)

    last_seen_dt = _parse_filed_at(last_seen) if last_seen else None
    if last_seen_dt and newest_dt and newest_dt <= last_seen_dt:
        print(f"{len(candidates)} candidate(s) today, none newer than last check ({last_seen})")
        return 0

    print(f"{len(candidates)} candidate filing(s) found, newest={newest!r} — dispatching")
    dispatch(target_repo, token)
    state["last_seen"] = newest or datetime.now(timezone.utc).isoformat()
    save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
