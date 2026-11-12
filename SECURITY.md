# Security notes

This is a local demo. It is not hardened for production.

- Ports: every service in `docker-compose.yml` is published on 127.0.0.1 only (n8n 5678, Postgres 5433, mock LLM 8000). Nothing listens on your LAN. Keep it that way, or put a TLS reverse proxy with authentication in front.
- Secrets: `N8N_ENCRYPTION_KEY` and `POSTGRES_PASSWORD` live only in `.env`, which is git-ignored. `python scripts/demo.py setup` generates random values and never prints them. `.env.example` holds no secrets. The Postgres credential is written to `data/import/` (git-ignored) only for the duration of the n8n CLI import and deleted right after; inside n8n it is stored encrypted with `N8N_ENCRYPTION_KEY`.
- Webhook authentication: the demo webhooks accept any POST so the harness can hit them. In production, add authentication: n8n Webhook "Header Auth" or "Basic Auth", or verify the provider's HMAC signature (for example in a Code node) before claiming the event. Reject unsigned requests before they touch the idempotency table, otherwise anyone can fill it with keys.
- The idempotency key is taken from a request header when present. If callers are untrusted, derive it from a verified, signed payload instead, so one caller cannot block another caller's event by sending its id first.
- `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` lets the workflows read `OPENAI_BASE_URL` / `OPENAI_API_KEY` from the environment. On a shared n8n instance, use n8n credentials instead, because any workflow editor could read environment variables.
- The mock LLM has no authentication and is meant for localhost only.
- n8n owner account: create it on first visit to the editor and use a strong password.

Report a security problem in this repo by opening a GitHub issue without exploit details, and we will reply with a private channel.
