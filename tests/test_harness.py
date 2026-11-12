"""Harness counting logic with a fake transport (no network, no Docker)."""

import asyncio
from collections import Counter

import httpx

from dedupe_demo.harness import (
    DeliveryLog,
    make_events,
    send_burst,
    summarize,
    summary_by_variant,
    to_markdown,
)


def run_burst(handler, events, k, **kw):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await send_burst(client, "http://test/webhook/x", events, k, retry_delay_s=0, **kw)

    return asyncio.run(go())


def test_make_events_unique_and_fictional():
    events = make_events("r1", "fixed", 20)
    assert len({e.event_id for e in events}) == 20
    assert all(e.body["fictional"] is True and e.body["id"] == e.event_id for e in events)


def test_no_timeouts_counts_one_delivery_per_copy():
    events = make_events("r1", "fixed", 3)
    seen = Counter()

    def handler(request):
        seen[request.headers["x-event-id"]] += 1
        return httpx.Response(202)

    log = run_burst(handler, events, k=4)
    assert log.attempts == 12 and log.timeouts == 0 and log.statuses == Counter({202: 12})
    assert all(n == 4 for n in seen.values())


def test_timeout_triggers_retries_up_to_limit():
    events = make_events("r1", "naive", 2)

    def handler(request):
        raise httpx.ReadTimeout("simulated", request=request)

    log = run_burst(handler, events, k=5, max_retries=2)
    assert log.attempts == 2 * 5 * 3
    assert log.timeouts == 30
    assert log.statuses == Counter()


def test_retry_stops_after_success():
    events = make_events("r1", "naive", 1)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadTimeout("simulated", request=request)
        return httpx.Response(200)

    log = run_burst(handler, events, k=1, max_retries=2)
    assert log.attempts == 2 and log.timeouts == 1 and log.statuses == Counter({200: 1})


def test_header_can_be_omitted():
    events = make_events("r1", "fixed", 1)
    got = []

    def handler(request):
        got.append(request.headers.get("x-event-id"))
        return httpx.Response(202)

    run_burst(handler, events, k=2, send_header=False)
    assert got == [None, None]


def test_summarize_counts_duplicates_and_ignores_foreign_ids():
    events = make_events("r1", "naive", 3)
    ids = [e.event_id for e in events]
    log = DeliveryLog(attempts=20, timeouts=5, statuses=Counter({200: 15}))
    mock_stats = {
        "by_event": {
            ids[0]: {"ok": 3, "failed": 0},
            ids[1]: {"ok": 1, "failed": 1},
            "evt_other_run_000": {"ok": 9, "failed": 0},
        }
    }
    record_keys = [ids[0], ids[0], ids[0], ids[1], "evt_other_run_000"]
    s = summarize("naive", events, 5, log, mock_stats, record_keys, 3.14159)
    assert s["events_sent"] == 15
    assert s["unique_events"] == 3
    assert s["http_deliveries"] == 20 and s["retries"] == 5 and s["client_timeouts"] == 5
    assert s["llm_calls"] == 5 and s["llm_calls_failed"] == 1
    assert s["records"] == 4
    assert s["duplicate_records"] == 2
    assert s["events_missing"] == 1
    assert s["wall_time_s"] == 3.14


def test_summary_and_markdown_report_every_run_with_min_max():
    runs = []
    for run, (calls, recs) in enumerate([(20, 20), (24, 22)], start=1):
        runs.append(
            {
                "variant": "builtin", "run": run, "unique_events": 20, "events_sent": 100, "http_deliveries": 100,
                "llm_calls": calls, "records": recs, "duplicate_records": recs - 20, "events_missing": 0, "wall_time_s": 5.0,
            }
        )
    s = summary_by_variant(runs)["builtin"]
    assert s["runs"] == 2 and s["llm_calls"] == {"min": 20, "max": 24}
    meta = {"date_utc": "2026-01-01 00:00", "m": 20, "k": 5, "timeout_s": 1.0, "max_retries": 2,
            "interval_s": 0.2, "mock_latency_s": 1.5, "n8n_version": "x"}
    md = to_markdown(runs, meta)
    assert "| builtin | 1 | 20 | 100 | 100 | 20 | 20 | 0 |" in md
    assert "| builtin | 2 | 20 | 100 | 100 | 24 | 22 | 2 |" in md
    assert "20 - 24" in md
    assert md.isascii()
