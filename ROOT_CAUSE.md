# Root cause: duplicate webhook events in n8n

Short, generic write-up of the bug this repo reproduces and fixes. It applies to most webhook-driven automations (n8n, Make, Zapier, custom code), not only to the demo.

## 1. Webhooks arrive more than once, by design

Almost every webhook provider promises "at least once" delivery, not "exactly once". Duplicates come from:

- Retries on timeout. The provider waits a few seconds for a 2xx. If your workflow responds only when it finishes (n8n "When Last Node Finishes"), a slow step such as an LLM call makes the provider give up and send the same event again, while the first run is still going.
- Retries on errors. A 5xx (or a dropped connection) triggers another delivery.
- Burst duplicates. Some providers, proxies or queue replays send the same event two or more times within milliseconds.
- Manual or bulk replays from the provider's dashboard.

So the workflow, not the provider, has to make processing idempotent.

## 2. Why a read-then-write check races

The common first fix is: look the event id up (SELECT, Google Sheet lookup, Remove Duplicates node, Data Store), and if it is not there, process it and then record it. That is two separate steps. When two deliveries of the same event run at the same time, both read "not seen" before either one writes "seen", so both run the LLM call and both insert a record. The slower the work between the read and the write (an LLM call takes seconds), the wider the window. Adding a check does not close the window; only an atomic operation does.

n8n's built-in Remove Duplicates node ("Remove Items Processed in Previous Executions") is a read-then-write too. In n8n 2.47.5, `checkProcessedAndRecord` loads the whole history for the workflow as one JSON array, checks and appends in memory, then writes the array back, with no lock or transaction. Concurrent executions can therefore both pass the check, and can also overwrite each other's writes, which erases recorded keys so that later redeliveries of those events pass as well. It works when executions do not overlap; under concurrent deliveries it leaks, and how much depends on load (see the README results).

## 3. The fix: respond immediately, then claim atomically

1. Respond 202 as soon as the webhook arrives (n8n Webhook "Respond: Immediately"). The provider gets its 2xx in milliseconds, so the slow LLM call no longer causes timeout retries. This removes most duplicates at the source.
2. Derive an idempotency key: the provider's event id header (here X-Event-Id) if it exists, otherwise a sha256 of the canonical JSON body (Postgres `jsonb` normalises key order and whitespace).
3. Claim the key with one atomic statement: `INSERT ... ON CONFLICT (event_key) DO UPDATE ... WHERE <re-claimable> RETURNING event_key`. The primary key makes Postgres serialise concurrent claims: exactly one delivery gets a row back; every other one gets nothing and stops.
4. Only the claimer calls the LLM, inserts the record, and marks the key done.

## 4. What the failed and stale re-claim handles

- Failed: if the LLM call (or the insert) errors, the workflow marks the key `failed`. A later redelivery of the same event can claim it again, so a transient error does not lose the event, and a success is still processed once.
- Stale processing: if n8n crashes mid-run, the key stays `processing` forever. The claim treats a `processing` row older than 5 minutes as abandoned and lets a redelivery take it over. Pick the window to be longer than your slowest legitimate run.
- Done is never re-claimed.

## 5. Limits

- Responding 202 before processing means the provider no longer retries on your internal failures. A `failed` row is re-processed only if the provider redelivers (or you replay it); for important events, add a scheduled sweep that re-runs `failed` and stale rows.
- If a run takes longer than the stale window, a redelivery can start a second run. Keep the window above your worst case, or have the worker refresh `claimed_at`.
- Exactly-once covers the claim, the LLM call and the record insert inside one workflow. Side effects outside your database (emails, payments) need their own idempotency key passed to that API.
- The idempotency table grows; delete `done` rows older than the provider's maximum retry period.
- A destination-side unique constraint (for example on `records.event_key`) is a good second guard. This demo leaves it off on purpose so duplicates stay visible.
