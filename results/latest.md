# Harness results

Date (UTC): 2026-11-12 19:02. Self-run on a local Docker stack; numbers vary by machine. All events are fictional.

Settings: M=20 unique events, K=5 concurrent deliveries each, client timeout 1.0 s, up to 2 retries on timeout, events started 0.2 s apart, mock LLM latency 1.5 s, n8n 2.47.5.

## Every run

| Workflow | Run | Unique events | Events sent | HTTP deliveries | LLM calls | Records | Duplicate records | Missing | Wall time (s) |
|---|---|---|---|---|---|---|---|---|---|
| naive | 1 | 20 | 100 | 299 | 109 | 109 | 89 | 0 | 10.03 |
| builtin | 1 | 20 | 100 | 150 | 50 | 50 | 30 | 0 | 6.0 |
| fixed | 1 | 20 | 100 | 100 | 20 | 20 | 0 | 0 | 5.64 |
| naive | 2 | 20 | 100 | 296 | 106 | 106 | 86 | 0 | 11.13 |
| builtin | 2 | 20 | 100 | 131 | 31 | 31 | 11 | 0 | 5.8 |
| fixed | 2 | 20 | 100 | 100 | 20 | 20 | 0 | 0 | 5.69 |
| naive | 3 | 20 | 100 | 296 | 106 | 106 | 86 | 0 | 10.05 |
| builtin | 3 | 20 | 100 | 129 | 29 | 29 | 9 | 0 | 6.22 |
| fixed | 3 | 20 | 100 | 100 | 20 | 20 | 0 | 0 | 5.62 |

## Min / max per workflow

| Workflow | Runs | Unique events | http_deliveries | llm_calls | records | duplicate_records | events_missing | wall_time_s |
|---|---|---|---|---|---|---|---|---|
| naive | 3 | 20 | 296 - 299 | 106 - 109 | 106 - 109 | 86 - 89 | 0 | 10.03 - 11.13 |
| builtin | 3 | 20 | 129 - 150 | 29 - 50 | 29 - 50 | 9 - 30 | 0 | 5.8 - 6.22 |
| fixed | 3 | 20 | 100 | 20 | 20 | 0 | 0 | 5.62 - 5.69 |

HTTP status counts per run are in the JSON file next to this one.
