# Daylight: SA environment setup

Run the complete demo against **your own ClickHouse Cloud and managed Postgres
services**, with a real Postgres CDC ClickPipe, your model provider, and Langfuse.
The app and embedding worker run on your laptop; the databases and CDC run in Cloud.
This repository includes public food data importers and synthetic sample data,
not personal health exports, database contents, or live credentials.

## Have an agent set this up

Point your coding agent at this repository and give it:

> Read AGENTS.md and set up the complete Cloud demo in my environment, including
> managed Postgres, ClickHouse, the Postgres CDC ClickPipe, sample/USDA data, my
> model provider, and Langfuse. Use my credential manager, keep secrets out of
> files and output, and verify a live multi-turn demo before declaring it ready.

Provide your target organization/region, permitted demo sizing, and credential
references when they aren't already available to the agent. [AGENTS.md](AGENTS.md)
contains the provisioning order, CDC contract, recovery rules, and acceptance
checks. It does not provision anything merely by being read.

## What you configure

| Component | Configuration | Purpose |
| --- | --- | --- |
| ClickHouse-managed Postgres | `DATABASE_URL` with direct host, database, user, password, TLS CA | Food catalog, meal drafts, confirmed meals |
| ClickHouse Cloud | `CLICKHOUSE_HOST`, `CLICKHOUSE_PORT`, `CLICKHOUSE_USER`, `CLICKHOUSE_PASSWORD`, `CLICKHOUSE_SECURE` | Retrieval, activity analytics, agent events |
| Postgres CDC ClickPipe | Source database, `public.foods` / `public.meals`, destination table mapping | Replicate catalog embeddings and meal changes |
| AI provider | `AGENT_BASE_URL`, `AGENT_MODEL`, `AGENT_API_KEY`, `AGENT_API_STYLE`, `AGENT_REASONING_EFFORT` | Live agent/tool loop |
| Langfuse project | `LANGFUSE_BASE_URL`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | Model/tool inputs, outputs, usage, sessions |

Langfuse uses **two project keys**, plus the instance URL. The model key is
separate. Embeddings use local MiniLM, so no embedding-provider key is needed.
Service IDs, warehouse IDs, and ClickHouse Cloud management API keys are not
needed by the app: provision services through your console or normal tooling,
then configure their connection endpoints. Use a dedicated demo database/service
rather than applying these setup commands to an unrelated application database.

## 1. Provision services and configure connections

Create a ClickHouse Cloud service and a ClickHouse-managed Postgres service.
Allow your laptop's IP to connect to both. Allow the ClickPipes source IPs to
connect to Postgres if you have restricted its network access.

Install `uv` and, for the configuration flow below, the 1Password CLI. Then:

```bash
git clone https://github.com/jnatkins/fitness-tracker-demo.git
cd fitness-tracker-demo
cp .env.example .env
```

Edit `.env`: replace the example hosts, model name, and `op://` references with
your own values. Save the full Postgres libpq connection string in 1Password,
including `sslmode=verify-full` and `sslrootcert=/absolute/path/postgres-ca.pem`.
Download the service's CA certificate to that path. Use the direct Postgres
endpoint, not a transaction-pooling endpoint. Credentials need app-table
read/write permissions and schema creation permissions for initial setup;
ClickHouse credentials need to create app tables/views and query/insert data.

Secrets remain in your credential manager. `.env` is ignored by Git. It is
**not** implicitly loaded; every command below loads and resolves it explicitly:

```bash
op run --env-file=.env -- ./scripts/sa-demo init
```

This creates Postgres app tables, including the embedding columns, and ClickHouse
activity/events tables plus the daily activity materialized view. It does not
provision services, create a ClickPipe, or send data to the model/Langfuse.
If you use another credential manager, export the equivalent resolved environment
variables and run `./scripts/sa-demo init` directly. No personal credential resolver or hardcoded vault is used by this path.

## 2. Connect Postgres to ClickHouse with CDC

In your managed Postgres console, open **Sync to ClickHouse**, then
**Replicate data in ClickHouse**. Select your destination ClickHouse service and
the same source database configured in `DATABASE_URL`. Use **Initial load + CDC**.
Map only these tables:

| Source | ClickHouse destination |
| --- | --- |
| `public.foods` | `default.foods_cdc` |
| `public.meals` | `default.meals_cdc` |

Include all columns, including food `embedding`, `embedding_model`, and
`embedding_text`. Disable a schema-name prefix or customize table names to match
this mapping. Retain the ClickPipes `_peerdb_version` and `_peerdb_is_deleted`
metadata columns. The app's views select the latest complete row before filtering
embeddings and deletion flags. Do not replicate drafts, removed meals, or food
source metadata; those remain in Postgres. ClickPipes creates destination tables;
do not pre-create `health_demo.foods` or `health_demo.meals` as physical tables.

Wait for the ClickPipe to reach Running and create both destination tables. Then:

```bash
op run --env-file=.env -- ./scripts/sa-demo views
```

This validates required CDC columns and creates `health_demo.foods` and
`health_demo.meals` views. It refuses to overwrite existing objects. Run it once.
If a partial setup leaves one view created, inspect the exact error and remaining
objects rather than dropping tables or blindly reapplying migrations. In the
ClickHouse SQL console, use `SHOW CREATE TABLE health_demo.foods` and/or
`SHOW CREATE TABLE health_demo.meals` to inspect the objects that exist. If they
are the intended views, execute only the missing `CREATE VIEW` statement from
`sql/cloud-views.sql`, then continue with `load-data`. If they are unrelated
objects, choose a fresh dedicated demo service rather than overwriting them.

Current console steps: [managed Postgres ClickPipes setup](https://clickhouse.com/docs/products/managed-postgres/sync-to-clickhouse/clickpipes).
For a different Postgres provider, follow its [source setup guide](https://clickhouse.com/docs/integrations/clickpipes/postgres/source/generic)
for logical replication, source permissions, publication, and networking.

## 3. Load the demo data

```bash
op run --env-file=.env -- ./scripts/sa-demo load-data
```

This command performs the full load:

| Data | Source and destination |
| --- | --- |
| 24 illustrative foods and sample meals | Seeded into Postgres; replicated by ClickPipes |
| USDA SR Legacy and FNDDS foods | Public archives downloaded/imported into Postgres, with FDC IDs, nutrients and portions |
| Catalog embeddings | Local MiniLM writes 384-dimensional vectors and matching text/model metadata into Postgres; ClickPipes replicates updates |
| Sample activity | About 330,000 synthetic half-hour events across 250 users / 28 days, inserted directly into ClickHouse |
| Daily activity | Existing incremental materialized view aggregates activity as it arrives |
| Agent/tool events | Created in ClickHouse while using the app, not preloaded fake traces |

The active sample profile is `demo-alex` / Alex Morgan. No personal export is
needed. First load downloads the model and USDA archives and embeds roughly
13,000 catalog foods; allow time for that step. Progress is printed without
credentials. Raw public archives and import reports stay under `.demo/usda`.

The command checks replication for up to two minutes after enrichment. If CDC
is still catching up, inspect the ClickPipe and rerun **check**, not the whole load:

```bash
op run --env-file=.env -- ./scripts/sa-demo check
```

Checks compare searchable ClickHouse food and meal counts to Postgres and verify
the daily activity rollup against raw aggregates. They treat query failures as
errors. Unchanged USDA reimports do not generate new food updates. Seeded meals
use stable IDs; activity seeding skips existing dates. An interrupted activity
batch can leave a partial day and requires inspection/repair rather than treating
a rerun as an exactly-once loader.

## 4. Start the live demo

```bash
op run --env-file=.env -- ./scripts/sa-demo start
```

Open <http://127.0.0.1:8088>. Startup validates configuration and loaded data.
The app maintains catalog embeddings; ClickPipes owns replication. Keep the
terminal open. On macOS, you can prevent sleep with:

```bash
op run --env-file=.env -- caffeinate -i ./scripts/sa-demo start
```

Ctrl-C stops the app and flushes traces; Cloud data persists. Port 8088 must be
free. The SA launcher fixes `HEALTH_CLOUD=1`, `HEALTH_AI_EMBED=0`, and
`HEALTH_USER_ID=demo-alex`, so it uses sample activity rather than personal health records.
Use a model available to your account that supports the configured API style.
Set `LANGFUSE_TRACING_ENABLED=false` if deliberately rehearsing without tracing.

Verify a live trace by asking “Compare my steps today with my usual training
days,” then opening your Langfuse project's Traces and Sessions views. Inspect
the model input/output and nested history tool results. Enabled configuration
status alone does not prove successful remote delivery. Prompts and tool results
go to your selected model provider and Langfuse. Use the sample data; personal
imports require authorization for those destinations.

## Demo flow

1. Search a food manually, adjust its portion, and log it.
2. Ask “I had a peanut butter and jelly sandwich for lunch.” Review and confirm
   the draft; expand tool calls to inspect retrieval.
3. Ask “Compare my steps today with my usual training days.”
4. Open Under the hood; generate activity to demonstrate the materialized view.
5. Change a catalog entry in Postgres to show embedding enrichment and real CDC.
6. Inspect Langfuse inputs, outputs, model usage, and multi-turn sessions.

For step 5, while the app is running, add the provided skyr example using your
own configuration, then search for “skyr” after CDC catches up:

```bash
op run --env-file=.env -- uv run python scripts/catalog-change.py
```

## Optional local fallback

With Docker Desktop/OrbStack installed, `./scripts/start-team --catalog` starts
local databases and loads the same public/sample data. `--live` uses resolved
model/Langfuse variables from your environment. Local replication is a
transactional outbox, **not** the managed CDC ClickPipe. Native Linux Docker has
not been verified. `scripts/start` uses the local fallback; `scripts/start-cloud` runs the
configured Cloud app. Complete Cloud setup uses `scripts/sa-demo`.

## Verification and limits

The bootstrap is source-reviewed and tested with mocked database connections;
it has not yet been run end to end against a newly provisioned SA Cloud pair.
`uv run pytest -q` is the local-container integration suite; do not run it blindly
against Cloud because some cases assume local outbox and SQL `aiEmbed` behavior.
Use `sa-demo check` for Cloud data verification. This is a single-user laptop
app, without public hosting or multi-user authentication. Retrieval is exact
hybrid ranking, not an ANN scale benchmark. Day grouping is UTC. See README.md
for the implementation details and measured-demo limitations.
