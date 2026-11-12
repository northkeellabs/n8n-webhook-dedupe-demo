"""Key derivation and atomic claim, using the exact query from workflows/fixed.json
against the live Postgres container (unit level: no n8n involved)."""

import json
import re

import pytest

from dedupe_demo.workflow_sql import run_node_query

pytestmark = pytest.mark.db

WF, CLAIM = "fixed.json", "Claim event"


def claim(db, header, body_json="{}"):
    rows = run_node_query(db, WF, CLAIM, header, body_json)
    return rows[0][0] if rows else None


def derive_key(db, header, body_json):
    """Run the claim inside a rolled-back transaction and return the key it derived."""
    with db.transaction(force_rollback=True):
        return claim(db, header, body_json)


def status(db, key):
    return db.execute("SELECT status, attempts FROM processed_events WHERE event_key = %s", (key,)).fetchone()


# ------------------------------------------------------------------ key derivation


def test_header_wins_over_body(db, key):
    assert derive_key(db, key, '{"id": "something-else"}') == key


def test_empty_header_falls_back_to_body_hash(db):
    k = derive_key(db, "", '{"id": "evt_1"}')
    assert re.fullmatch(r"[0-9a-f]{64}", k)


def test_body_hash_ignores_key_order_and_whitespace(db):
    a = '{"id":"evt_1","data":{"amount":10,"items":[1,2,3],"currency":"USD"}}'
    b = '{\n  "data" : { "currency": "USD", "items": [1, 2, 3], "amount": 10 },\n  "id" : "evt_1"\n}'
    assert json.loads(a) == json.loads(b)
    assert derive_key(db, "", a) == derive_key(db, "", b)


def test_different_bodies_give_different_keys(db):
    k1 = derive_key(db, "", '{"id": "evt_1", "amount": 10}')
    k2 = derive_key(db, "", '{"id": "evt_1", "amount": 11}')
    k3 = derive_key(db, "", '{"id": "evt_2", "amount": 10}')
    assert len({k1, k2, k3}) == 3


def test_array_order_matters(db):
    assert derive_key(db, "", '{"items": [1, 2]}') != derive_key(db, "", '{"items": [2, 1]}')


# ------------------------------------------------------------------ claim behaviour


def test_claim_once_then_nothing(db, key):
    assert claim(db, key) == key
    assert status(db, key) == ("processing", 1)
    assert claim(db, key) is None
    assert claim(db, key) is None
    assert status(db, key) == ("processing", 1)


def test_failed_is_reclaimable(db, key):
    assert claim(db, key) == key
    run_node_query(db, WF, "Mark failed", key)
    assert status(db, key) == ("failed", 1)
    assert claim(db, key) == key
    assert status(db, key) == ("processing", 2)
    assert claim(db, key) is None


def test_fresh_processing_is_not_reclaimable(db, key):
    assert claim(db, key) == key
    db.execute("UPDATE processed_events SET claimed_at = now() - interval '4 minutes' WHERE event_key = %s", (key,))
    assert claim(db, key) is None


def test_stale_processing_is_reclaimable(db, key):
    assert claim(db, key) == key
    db.execute("UPDATE processed_events SET claimed_at = now() - interval '6 minutes' WHERE event_key = %s", (key,))
    assert claim(db, key) == key
    assert status(db, key) == ("processing", 2)


def test_done_is_never_reclaimed(db, key):
    assert claim(db, key) == key
    run_node_query(db, WF, "Mark done", key)
    db.execute("UPDATE processed_events SET claimed_at = now() - interval '1 day' WHERE event_key = %s", (key,))
    assert claim(db, key) is None
    assert status(db, key) == ("done", 1)


def test_concurrent_claims_only_one_wins(settings, db, key):
    """20 separate connections race to claim the same key; exactly one gets a row."""
    import concurrent.futures
    import threading

    import psycopg

    barrier = threading.Barrier(20)

    def worker():
        with psycopg.connect(settings.pg_conninfo(), autocommit=True) as conn:
            barrier.wait()
            return claim(conn, key)

    with concurrent.futures.ThreadPoolExecutor(20) as pool:
        results = list(pool.map(lambda _: worker(), range(20)))
    assert results.count(key) == 1
    assert results.count(None) == 19
