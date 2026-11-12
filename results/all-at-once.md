# Harness results

Date (UTC): 2026-11-12 19:03. Self-run on a local Docker stack; numbers vary by machine. All events are fictional.

Settings: M=20 unique events, K=5 concurrent deliveries each, client timeout 1.0 s, up to 2 retries on timeout, events started 0.0 s apart, mock LLM latency 1.5 s, n8n 2.47.5.

## Every run

| Workflow | Run | Unique events | Events sent | HTTP deliveries | LLM calls | Records | Duplicate records | Missing | Wall time (s) |
|---|---|---|---|---|---|---|---|---|---|
| naive | 1 | 20 | 100 | 300 | 100 | 100 | 80 | 0 | 5.36 |
| builtin | 1 | 20 | 100 | 300 | 282 | 282 | 262 | 0 | 13.83 |
| fixed | 1 | 20 | 100 | 296 | 20 | 20 | 0 | 0 | 5.27 |

## Min / max per workflow

| Workflow | Runs | Unique events | http_deliveries | llm_calls | records | duplicate_records | events_missing | wall_time_s |
|---|---|---|---|---|---|---|---|---|
| naive | 1 | 20 | 300 | 100 | 100 | 80 | 0 | 5.36 |
| builtin | 1 | 20 | 300 | 282 | 282 | 262 | 0 | 13.83 |
| fixed | 1 | 20 | 296 | 20 | 20 | 0 | 0 | 5.27 |

HTTP status counts per run are in the JSON file next to this one.
