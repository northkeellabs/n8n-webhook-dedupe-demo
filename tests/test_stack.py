"""Integration tests against the running compose stack: pytest -m stack

Needs `python scripts/demo.py setup` first. Each test resets the mock and truncates
the demo tables, so do not run it at the same time as the harness.
"""

import json
import time
import uuid

import httpx
import pytest

from dedupe_demo.harness import db_connect, fetch_mock_stats, reset_stack, run_variant

pytestmark = pytest.mark.stack

M, K = 5, 3


@pytest.fixture(autouse=True)
def _stack_up(settings):
    try:
        httpx.get(f"{settings.n8n_url}/healthz", timeout=3).raise_for_status()
        httpx.get(f"{settings.mock_url}/health", timeout=3).raise_for_status()
    except httpx.HTTPError as exc:
        pytest.fail(f"compose stack not running ({exc.__class__.__name__}); run: python scripts/demo.py setup")
    yield
    reset_stack(settings)


def wait_for(predicate, timeout_s=20.0, interval_s=0.25):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval_s)
    return predicate()


def row(settings, sql, *params):
    with db_connect(settings) as conn:
        return conn.execute(sql, params).fetchone()


def test_fixed_processes_each_event_exactly_once(settings):
    r = run_variant(settings, "fixed", m=M, k=K)
    print(json.dumps(r))
    assert r["llm_calls"] == r["unique_events"] == M
    assert r["records"] == M
    assert r["duplicate_records"] == 0 and r["events_missing"] == 0
    assert r["http_statuses"] == {"202": M * K}


def test_naive_lets_duplicates_through(settings):
    r = run_variant(settings, "naive", m=M, k=K)
    print(json.dumps(r))
    assert r["llm_calls"] > r["unique_events"]
    assert r["records"] > r["unique_events"]
    assert r["duplicate_records"] > 0


def test_fault_then_redelivery_processes_once(settings):
    eid = f"evt_fault_{uuid.uuid4().hex[:8]}"
    body = {"id": eid, "type": "invoice.paid", "fictional": True}
    url = f"{settings.n8n_url}/webhook/dedupe-fixed"
    reset_stack(settings, fail_first=[eid])

    def status():
        r = row(settings, "SELECT status, attempts FROM processed_events WHERE event_key = %s", eid)
        return r and r[0] in ("failed", "done") and r

    # First delivery: the LLM call fails (injected HTTP 500) -> status failed, no record.
    assert httpx.post(url, json=body, headers={"X-Event-Id": eid}, timeout=10).status_code == 202
    assert wait_for(status) == ("failed", 1)
    assert row(settings, "SELECT count(*) FROM records WHERE event_key = %s", eid)[0] == 0
    assert fetch_mock_stats(settings)["by_event"][eid] == {"ok": 0, "failed": 1}

    # Provider redelivers (twice at once): the failed claim is re-taken by exactly one.
    for resp in [httpx.post(url, json=body, headers={"X-Event-Id": eid}, timeout=10) for _ in range(2)]:
        assert resp.status_code == 202
    assert wait_for(lambda: (status() or (None,))[0] == "done")
    time.sleep(2.5)  # let any (wrong) second run finish before counting
    assert row(settings, "SELECT status, attempts FROM processed_events WHERE event_key = %s", eid) == ("done", 2)
    assert row(settings, "SELECT count(*) FROM records WHERE event_key = %s", eid)[0] == 1
    assert fetch_mock_stats(settings)["by_event"][eid] == {"ok": 1, "failed": 1}

    # A later redelivery of a done event does nothing.
    httpx.post(url, json=body, headers={"X-Event-Id": eid}, timeout=10)
    time.sleep(2.5)
    assert row(settings, "SELECT count(*) FROM records WHERE event_key = %s", eid)[0] == 1
    assert fetch_mock_stats(settings)["by_event"][eid] == {"ok": 1, "failed": 1}


def test_fixed_without_header_dedupes_on_body_hash(settings):
    """No X-Event-Id: the same JSON with different key order and whitespace is one event."""
    reset_stack(settings)
    tag = uuid.uuid4().hex[:8]
    url = f"{settings.n8n_url}/webhook/dedupe-fixed"
    variants = [
        f'{{"id":"nohdr_{tag}","amount":10,"customer":"Acme Fictional Ltd"}}',
        f'{{ "customer" : "Acme Fictional Ltd", "amount" : 10, "id" : "nohdr_{tag}" }}',
        f'{{\n  "amount": 10,\n  "id": "nohdr_{tag}",\n  "customer": "Acme Fictional Ltd"\n}}',
    ]
    other = f'{{"id":"nohdr_{tag}","amount":11,"customer":"Acme Fictional Ltd"}}'
    headers = {"Content-Type": "application/json"}
    for content in variants * 2 + [other]:
        assert httpx.post(url, content=content, headers=headers, timeout=10).status_code == 202

    def counts():
        return row(
            settings,
            "SELECT count(*), count(DISTINCT event_key) FROM records WHERE workflow = 'fixed' AND payload->>'id' = %s",
            f"nohdr_{tag}",
        )

    assert wait_for(lambda: counts()[0] >= 2)
    time.sleep(2.5)
    total, distinct = counts()
    assert (total, distinct) == (2, 2)  # one record for the 6 equivalent deliveries, one for the different body
    keys = [r for r in row(settings, "SELECT array_agg(event_key) FROM records WHERE payload->>'id' = %s", f"nohdr_{tag}")[0]]
    assert all(len(k) == 64 for k in keys)
