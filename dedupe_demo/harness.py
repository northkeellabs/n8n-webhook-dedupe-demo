"""Load harness: duplicate webhook deliveries against the three n8n workflows.

For each workflow (after a reset) it sends M unique fictional events, each delivered
K times concurrently (a burst of duplicates), and behaves like a webhook provider:
client timeout 1 s, up to 2 retries when a delivery times out. Then it waits until
processing settles and counts mock LLM calls and destination rows.

Usage:
  python -m dedupe_demo.harness                       # M=20, K=5, all three, 1 run each
  python -m dedupe_demo.harness --runs 3              # full run
  python -m dedupe_demo.harness --variants fixed --m 5 --k 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .config import ROOT, Settings, load_settings

VARIANTS = ("naive", "builtin", "fixed")
WEBHOOK_PATHS = {v: f"/webhook/dedupe-{v}" for v in VARIANTS}

COMPANIES = ["Acme Fictional Ltd", "Example Widgets Co", "Placeholder Foods", "Sample Logistics", "Demo Dental Clinic"]


@dataclass
class Event:
    event_id: str
    body: dict


@dataclass
class DeliveryLog:
    attempts: int = 0          # HTTP deliveries actually sent (incl. retries)
    timeouts: int = 0          # deliveries the client gave up on after the timeout
    errors: int = 0            # connection or other transport errors
    statuses: Counter = field(default_factory=Counter)


def make_events(run_id: str, variant: str, m: int) -> list[Event]:
    """M unique fictional events. Every value here is made up for the demo."""
    events = []
    for i in range(m):
        eid = f"evt_{run_id}_{variant}_{i:03d}"
        events.append(
            Event(
                eid,
                {
                    "id": eid,
                    "type": "invoice.paid",
                    "fictional": True,
                    "data": {
                        "customer": COMPANIES[i % len(COMPANIES)],
                        "invoice": f"INV-DEMO-{i:04d}",
                        "amount_cents": 1000 + 37 * i,
                        "currency": "USD",
                    },
                },
            )
        )
    return events


async def deliver(
    client: httpx.AsyncClient,
    url: str,
    event: Event,
    log: DeliveryLog,
    *,
    timeout_s: float = 1.0,
    max_retries: int = 2,
    retry_delay_s: float = 0.2,
    send_header: bool = True,
) -> None:
    """One provider delivery: POST, and on a client-side timeout retry up to max_retries times."""
    headers = {"X-Event-Id": event.event_id} if send_header else {}
    for attempt in range(1 + max_retries):
        log.attempts += 1
        try:
            resp = await client.post(url, json=event.body, headers=headers, timeout=timeout_s)
            log.statuses[resp.status_code] += 1
            return
        except httpx.TimeoutException:
            log.timeouts += 1
            if attempt < max_retries:
                await asyncio.sleep(retry_delay_s)
        except httpx.HTTPError:
            log.errors += 1
            return


async def send_burst(
    client: httpx.AsyncClient, url: str, events: list[Event], k: int, *, interval_s: float = 0.0, **kwargs
) -> DeliveryLog:
    """Deliver every event K times concurrently (a burst of duplicates).

    Event i starts at i * interval_s, like a provider streaming events; the K
    copies of one event always go out at the same instant. interval_s=0 sends
    all M*K deliveries at once.
    """
    log = DeliveryLog()

    async def event_burst(i: int, ev: Event) -> None:
        if interval_s:
            await asyncio.sleep(i * interval_s)
        await asyncio.gather(*(deliver(client, url, ev, log, **kwargs) for _ in range(k)))

    await asyncio.gather(*(event_burst(i, ev) for i, ev in enumerate(events)))
    return log


def summarize(
    variant: str,
    events: list[Event],
    k: int,
    log: DeliveryLog,
    mock_stats: dict,
    record_keys: list[str],
    wall_time_s: float,
) -> dict:
    """Pure counting logic (unit-tested with a fake transport)."""
    ids = {e.event_id for e in events}
    by_event = mock_stats.get("by_event", {})
    llm_ok = sum(v.get("ok", 0) for key, v in by_event.items() if key in ids)
    llm_failed = sum(v.get("failed", 0) for key, v in by_event.items() if key in ids)
    rows = [key for key in record_keys if key in ids]
    distinct = set(rows)
    sent = len(events) * k
    return {
        "variant": variant,
        "events_sent": sent,
        "unique_events": len(ids),
        "http_deliveries": log.attempts,
        "retries": log.attempts - sent,
        "client_timeouts": log.timeouts,
        "transport_errors": log.errors,
        "http_statuses": {str(code): n for code, n in sorted(log.statuses.items())},
        "llm_calls": llm_ok + llm_failed,
        "llm_calls_failed": llm_failed,
        "records": len(rows),
        "duplicate_records": len(rows) - len(distinct),
        "events_missing": len(ids - distinct),
        "wall_time_s": round(wall_time_s, 2),
    }


# --------------------------------------------------------------------------- stack I/O


def db_connect(settings: Settings):
    import psycopg

    return psycopg.connect(settings.pg_conninfo(), autocommit=True)


def reset_stack(settings: Settings, *, latency_s: float | None = None, fail_first: list[str] | None = None) -> None:
    body: dict = {}
    if latency_s is not None:
        body["latency_s"] = latency_s
    if fail_first is not None:
        body["fail_first"] = fail_first
    httpx.post(f"{settings.mock_url}/reset", json=body, timeout=5).raise_for_status()
    with db_connect(settings) as conn:
        conn.execute("TRUNCATE records, processed_events")


def fetch_record_keys(settings: Settings, variant: str, prefix: str) -> list[str]:
    with db_connect(settings) as conn:
        rows = conn.execute(
            "SELECT event_key FROM records WHERE workflow = %s AND event_key LIKE %s ORDER BY id",
            (variant, prefix + "%"),
        ).fetchall()
    return [r[0] for r in rows]


def fetch_mock_stats(settings: Settings) -> dict:
    return httpx.get(f"{settings.mock_url}/stats", timeout=5).json()


def wait_settled(
    settings: Settings, variant: str, prefix: str, ids: set[str], t0: float, *, stable_s: float = 4.0, max_s: float = 120.0
) -> float:
    """Poll until LLM calls and rows stop changing for stable_s. Returns seconds from t0 to the last change."""
    last_sig = None
    last_change = time.monotonic()
    while True:
        stats = fetch_mock_stats(settings)
        calls = sum(v["ok"] + v["failed"] for key, v in stats.get("by_event", {}).items() if key in ids)
        rows = len(fetch_record_keys(settings, variant, prefix))
        sig = (calls, rows)
        now = time.monotonic()
        if sig != last_sig:
            last_sig, last_change = sig, now
        elif now - last_change >= stable_s:
            return last_change - t0
        if now - t0 > max_s:
            return now - t0
        time.sleep(0.5)


async def _send(settings: Settings, variant: str, events: list[Event], k: int, **kwargs) -> DeliveryLog:
    limits = httpx.Limits(max_connections=1000, max_keepalive_connections=0)
    async with httpx.AsyncClient(limits=limits) as client:
        return await send_burst(client, settings.n8n_url + WEBHOOK_PATHS[variant], events, k, **kwargs)


def run_variant(
    settings: Settings,
    variant: str,
    *,
    m: int = 20,
    k: int = 5,
    timeout_s: float = 1.0,
    max_retries: int = 2,
    interval_s: float = 0.2,
    latency_s: float | None = None,
    run_id: str | None = None,
) -> dict:
    run_id = run_id or secrets.token_hex(3)
    reset_stack(settings, latency_s=latency_s)
    events = make_events(run_id, variant, m)
    prefix = f"evt_{run_id}_{variant}_"
    t0 = time.monotonic()
    log = asyncio.run(
        _send(settings, variant, events, k, timeout_s=timeout_s, max_retries=max_retries, interval_s=interval_s)
    )
    wall = wait_settled(settings, variant, prefix, {e.event_id for e in events}, t0)
    result = summarize(
        variant, events, k, log, fetch_mock_stats(settings), fetch_record_keys(settings, variant, prefix), wall
    )
    result.update(
        {
            "run_id": run_id,
            "m": m,
            "k": k,
            "client_timeout_s": timeout_s,
            "max_retries": max_retries,
            "interval_s": interval_s,
            "mock_latency_s": fetch_mock_stats(settings).get("latency_s"),
        }
    )
    return result


# --------------------------------------------------------------------------- reporting

TABLE_COLS = [
    ("variant", "Workflow"),
    ("run", "Run"),
    ("unique_events", "Unique events"),
    ("events_sent", "Events sent"),
    ("http_deliveries", "HTTP deliveries"),
    ("llm_calls", "LLM calls"),
    ("records", "Records"),
    ("duplicate_records", "Duplicate records"),
    ("events_missing", "Missing"),
    ("wall_time_s", "Wall time (s)"),
]

SUMMARY_KEYS = ["http_deliveries", "llm_calls", "records", "duplicate_records", "events_missing", "wall_time_s"]


def summary_by_variant(results: list[dict]) -> dict:
    out: dict = {}
    for variant in VARIANTS:
        rs = [r for r in results if r["variant"] == variant]
        if not rs:
            continue
        out[variant] = {
            "runs": len(rs),
            "unique_events": rs[0]["unique_events"],
            **{key: {"min": min(r[key] for r in rs), "max": max(r[key] for r in rs)} for key in SUMMARY_KEYS},
        }
    return out


def to_markdown(results: list[dict], meta: dict) -> str:
    lines = [
        "# Harness results",
        "",
        f"Date (UTC): {meta['date_utc']}. Self-run on a local Docker stack; numbers vary by machine. All events are fictional.",
        "",
        f"Settings: M={meta['m']} unique events, K={meta['k']} concurrent deliveries each, client timeout {meta['timeout_s']} s, "
        f"up to {meta['max_retries']} retries on timeout, events started {meta['interval_s']} s apart, mock LLM latency {meta['mock_latency_s']} s, n8n {meta['n8n_version']}.",
        "",
        "## Every run",
        "",
        "| " + " | ".join(h for _, h in TABLE_COLS) + " |",
        "|" + "---|" * len(TABLE_COLS),
    ]
    for r in results:
        lines.append("| " + " | ".join(str(r.get(key, "")) for key, _ in TABLE_COLS) + " |")
    lines += ["", "## Min / max per workflow", "", "| Workflow | Runs | Unique events | " + " | ".join(SUMMARY_KEYS) + " |"]
    lines.append("|" + "---|" * (3 + len(SUMMARY_KEYS)))
    for variant, s in summary_by_variant(results).items():
        cells = [f"{s[key]['min']} - {s[key]['max']}" if s[key]["min"] != s[key]["max"] else str(s[key]["min"]) for key in SUMMARY_KEYS]
        lines.append(f"| {variant} | {s['runs']} | {s['unique_events']} | " + " | ".join(cells) + " |")
    lines += ["", "HTTP status counts per run are in the JSON file next to this one.", ""]
    return "\n".join(lines)


def write_results(results: list[dict], meta: dict, out_stem: Path) -> None:
    out_stem.parent.mkdir(parents=True, exist_ok=True)
    payload = {"meta": meta, "summary": summary_by_variant(results), "runs": results}
    out_stem.with_suffix(".json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    out_stem.with_suffix(".md").write_text(to_markdown(results, meta), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--variants", nargs="+", choices=VARIANTS, default=list(VARIANTS))
    p.add_argument("--runs", type=int, default=1)
    p.add_argument("--m", type=int, default=20, help="unique events per run")
    p.add_argument("--k", type=int, default=5, help="concurrent deliveries per event")
    p.add_argument("--timeout", type=float, default=1.0, help="client timeout per delivery (s)")
    p.add_argument("--retries", type=int, default=2, help="max retries after a timeout")
    p.add_argument("--interval", type=float, default=0.2, help="seconds between event starts (0 = all at once)")
    p.add_argument("--latency", type=float, default=None, help="override mock LLM latency (s)")
    p.add_argument("--out", default="results/latest", help="output path without extension")
    args = p.parse_args(argv)

    settings = load_settings()
    results = []
    for run in range(1, args.runs + 1):
        for variant in args.variants:
            r = run_variant(
                settings,
                variant,
                m=args.m,
                k=args.k,
                timeout_s=args.timeout,
                max_retries=args.retries,
                interval_s=args.interval,
                latency_s=args.latency,
            )
            r["run"] = run
            results.append(r)
            print(
                f"run {run} {variant:8s} deliveries={r['http_deliveries']:4d} llm_calls={r['llm_calls']:4d} "
                f"records={r['records']:4d} duplicates={r['duplicate_records']:4d} missing={r['events_missing']} "
                f"wall={r['wall_time_s']}s",
                flush=True,
            )
            raw = ROOT / "results" / "raw" / f"{r['run_id']}-{variant}.json"
            raw.parent.mkdir(parents=True, exist_ok=True)
            raw.write_text(json.dumps(r, indent=2), encoding="utf-8")

    from .config import read_env_file

    meta = {
        "date_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "m": args.m,
        "k": args.k,
        "timeout_s": args.timeout,
        "max_retries": args.retries,
        "interval_s": args.interval,
        "mock_latency_s": results[0]["mock_latency_s"] if results else None,
        "n8n_version": read_env_file().get("N8N_VERSION", "2.47.5"),
        "runs_per_variant": args.runs,
    }
    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    write_results(results, meta, out)
    def _show(p):
        try:
            return p.relative_to(ROOT)
        except ValueError:
            return p

    print(f"wrote {_show(out.with_suffix('.json'))} and {_show(out.with_suffix('.md'))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
