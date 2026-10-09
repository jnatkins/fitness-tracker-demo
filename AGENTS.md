# Daylight agent setup contract

This repository is a single-user fitness demo for solutions architects. When the
user asks you to set it up, complete the **Cloud demo**, including provisioning
and a real Postgres CDC ClickPipe. `scripts/sa-demo` prepares schemas/data and
starts the app; it does **not** provision services or create the ClickPipe.
Do not substitute local Docker/outbox replication for Cloud without the user's
request. Do not stop after producing instructions if you have authorized access
to perform the setup.

## Read first

Inspect Git status and preserve unrelated changes. Read `HANDOFF.md`,
`.env.example`, `scripts/sa_demo.py`, `sql/cloud-postgres.sql`,
`sql/cloud-app.sql`, and `sql/cloud-views.sql`. These define the application's
actual configuration and table contract. This file adds agent provisioning and
verification responsibilities; the handoff is the human walkthrough.

`scripts/start` launches the local sample demo; `scripts/start-cloud` launches
an already-configured Cloud demo. Use `scripts/sa-demo` for full Cloud setup.
No personal health records or credential values are supplied in this repository.

## Resolve scope and credentials

- Resolve the user's ClickHouse organization, Cloud provider/region, resource
  names, and permitted demo sizing from their request or existing configuration.
  Ask only for missing choices that materially affect ownership, cost, or
  correctness. One authorized setup request covers creating the requisite
  demo services; don't repeatedly ask permission for each normal setup step.
- Use a dedicated demo Postgres database and ClickHouse service. Reuse a resource
  only after verifying it belongs to this setup and matches the table contract.
  Do not seed an unrelated database. Inspect existing resources before creating
  replacements, and keep a checkpoint to avoid duplicates after interruption.
- Use installed `clickhousectl` and current official documentation. Run the
  relevant `--help` before provisioning; flags and supported instance sizes can
  change. Install missing tools only within the user's authorized setup scope.
- Check `clickhousectl cloud auth status`. OAuth is read-only; provisioning
  requires write-capable Cloud API credentials. Resolve those through the user's
  credential manager. Do not replace global authentication/configuration without
  authorization. Specify the target organization explicitly where supported.
- Model credentials and a Langfuse project's public/secret keys are separate from
  Cloud management credentials. Discover existing approved credential references;
  ask for references and the destination URL when missing, never pasted keys.
  Create a Langfuse project only when authorized account/admin access supports
  it. Missing tracing/model access does not prevent independent database setup.

Never print, log, commit, or persist live secrets. Service creation returns initial
passwords once: capture responses in memory, save credentials directly into the
user's credential manager, and display only redacted IDs/status. Don't run create
commands with raw output displayed. Build secret-bearing arguments in a helper
from resolved credentials, without printing commands, arguments, or responses.
Disable debug/command echo. Keep `.env` limited to non-secret settings and secret
references; it is ignored. Do not invent credential values or use another person's vault/item references. Use `.demo/` for local certificates and redacted checkpoints;
never store passwords there.

## Provision and initialize, in this order

### 1. ClickHouse Cloud and managed Postgres

Discover installed commands with:

```bash
clickhousectl cloud service create --help
clickhousectl cloud postgres create --help
clickhousectl cloud postgres certs get --help
clickhousectl cloud clickpipe create postgres --help
```

Create the dedicated ClickHouse and ClickHouse-managed Postgres services in a
compatible provider/region using authorized sizes. Do not attach the demo as a
read replica of an unrelated warehouse. Capture the returned service/warehouse
IDs and endpoints in a redacted checkpoint. Poll service status until ready;
creation returning successfully does not mean the database is running.

Specify ClickHouse's IP allowlist explicitly: omitting `--ip-allow` currently
opens access to everyone. Allow the laptop's current IP and required ClickPipes
network access; don't use an unrestricted rule merely to avoid troubleshooting.
Configure Postgres access for the laptop and regional ClickPipes egress IPs.
Fetch its CA with `postgres certs get --output` into `.demo/`, and use the direct
Postgres endpoint, not the transaction pooler. The app requires Postgres
`sslmode=verify-full` and ClickHouse HTTPS on 8443.

Populate the user's ignored `.env` from `.env.example` with credential references.
The runtime doesn't need service IDs or a Cloud management API key. Keep those
in provisioning context rather than adding them to the app configuration.

### 2. App schemas

Load and resolve the user's configuration explicitly:

```bash
op run --env-file=.env -- ./scripts/sa-demo init
```

With another credential resolver, export equivalent variables and invoke
`./scripts/sa-demo init` directly. Every later command uses the same environment.
The wrapper pins Cloud mode, local embedding enrichment, and the synthetic
`demo-alex` profile. It never starts Docker or selects personal health activity.

Initialize before creating CDC: Postgres `foods` must already include `embedding`,
`embedding_model`, and `embedding_text`. ClickHouse app tables, especially the
activity MV, must exist before loading events. Don't use the local init/seed CLI
or local `sql/clickhouse.sql` wholesale: its physical foods/meals tables conflict
with the Cloud compatibility views.

### 3. Postgres CDC ClickPipe

Prefer `clickhousectl cloud clickpipe create postgres` with flags verified from
installed help; use the managed Postgres console if the installed CLI cannot
express the required mapping. Consult current official source setup docs for
logical replication, source user permissions, publication, and networking.
Resolve replication credentials securely; do not put live passwords into
visible tool calls. Apply any needed replication configuration only to the
identified demo source. Do not restart unrelated services or accept new legal
terms without authorization.

Required contract:

| Source | Destination |
| --- | --- |
| `public.foods` | `default.foods_cdc` |
| `public.meals` | `default.meals_cdc` |

Use initial snapshot **plus** ongoing CDC (`cdc` in the currently installed CLI;
verify this against its help). Include all source columns, particularly the
embedding fields. Preserve `_peerdb_version` and `_peerdb_is_deleted`. Do not
replicate drafts, removed meals, or `food_sources`. Verify the destination
**database** as well as table names; don't assume a CLI default without checking
its resulting configuration. Let ClickPipes create the destination tables.

Poll the ClickPipe until Running; inspect errors as errors, not evidence of no
data. Verify both destination tables and column types, including the replicated
numeric embedding array. Then create the app views:

```bash
op run --env-file=.env -- ./scripts/sa-demo views
```

`views` refuses to overwrite existing objects. If setup partially created a view,
inspect `SHOW CREATE TABLE` and follow the targeted recovery in `HANDOFF.md`.
Never drop an unrelated table or resync an existing pipe as a generic repair.

### 4. Load and check data

```bash
op run --env-file=.env -- ./scripts/sa-demo load-data
op run --env-file=.env -- ./scripts/sa-demo check
```

The loader imports the public USDA catalogs and portions into Postgres, seeds
sample meals and synthetic activity, generates local MiniLM embeddings, and
checks CDC catch-up. Embeddings need no external provider key. Raw activity goes
directly to ClickHouse and its incremental daily MV. Agent traces are generated
by real interactions, not preloaded fabricated calls.

Allow first-time model/catalog downloads and embedding enrichment to finish.
If the final CDC wait times out, investigate the pipe and rerun `check`, not the
whole load. Partial activity seeding requires inspection: skipping existing dates
is not a repair for an interrupted batch. Preserve existing data; don't reset
volumes, mutate timestamps, or destroy/recreate services to hide an error.

### 5. Live app, model, and Langfuse

```bash
op run --env-file=.env -- ./scripts/sa-demo start
```

Open http://127.0.0.1:8088. Keep the app running using the user's normal terminal
or process-management workflow, and provide the exact restart command. The app
and embedding worker run on the laptop, not inside the managed databases.
Select a model available to the user's account that supports the configured API.
Langfuse requires its actual instance URL, public key, and secret key. Confirm
sample-data tracing is the intended destination. Personal data requires separate
approval before enabling `HEALTH_ALLOW_PERSONAL_LANGFUSE`; never set that flag
just to silence a startup failure.

## Verify before declaring success

1. Both services are ready; the ClickPipe is Running with the exact mapping.
2. `sa-demo check` passes: searchable catalog/meals match Postgres counts and
   daily activity aggregates match raw activity.
3. In a fresh app session, manually search for jelly and create a meal draft.
   Confirm, remove, and restore a disposable test meal; wait for CDC and verify
   the UI/database results. Clean up only your identified test record.
4. While the app runs, execute the skyr example with the same configuration:
   `op run --env-file=.env -- uv run python scripts/catalog-change.py`.
   Verify its embedding is enriched and the food becomes searchable through CDC.
5. Run a live, multi-turn sample-data chat. Verify the real model responds, tools
   run, and the conversation retains context. Inspect the Langfuse trace for
   inputs/outputs, model/token usage, nested tools, and session grouping.
   Enabled status alone doesn't prove delivery. Use installed Langfuse tools/
   skill for remote verification; never print trace bodies or credentials.
6. Run focused source tests for any changes. The full local integration suite
   assumes Docker/outbox/SQL aiEmbed and must not be run blindly against Cloud.
   Require independent review for consequential setup-code/schema changes.

Record a redacted checkpoint with organization, services, warehouse, ClickPipe,
credential **references**, CA path, completed steps, verification, and remaining
blockers. No raw records, credentials, prompts, or trace bodies. Report the repo
revision, app URL, restart command, resource IDs/links, and actual checks performed.
If blocked, identify the exact missing access/input and next action; don't call
the demo ready or substitute a mocked test for live setup verification.
