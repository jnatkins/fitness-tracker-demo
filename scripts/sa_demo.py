"""Own-environment Cloud setup. No hardcoded credentials or service IDs."""
import argparse
import os
import sys
import time
from pathlib import Path
from psycopg.conninfo import conninfo_to_dict
from health_demo.db import ch, pg, ROOT, USER


def validate(live=False):
    required=['DATABASE_URL','CLICKHOUSE_HOST','CLICKHOUSE_USER','CLICKHOUSE_PASSWORD']
    if live:
        required+=['AGENT_BASE_URL','AGENT_MODEL','AGENT_API_KEY']
        if os.getenv('LANGFUSE_TRACING_ENABLED','').lower()!='false':
            required+=['LANGFUSE_BASE_URL','LANGFUSE_PUBLIC_KEY','LANGFUSE_SECRET_KEY']
    for key in required:
        value=os.getenv(key,'').strip()
        if not value or value.startswith('op://') or 'replace-with-' in value or 'your-service.' in value:
            raise ValueError(f'Configure and resolve {key} before running this command')
    if os.getenv('HEALTH_CLOUD')!='1' or USER!='demo-alex':
        raise ValueError('Use scripts/sa-demo for the Cloud sample profile')
    if os.getenv('CLICKHOUSE_SECURE')!='true' or os.getenv('CLICKHOUSE_PORT')!='8443':
        raise ValueError('Configure ClickHouse HTTPS: CLICKHOUSE_SECURE=true, CLICKHOUSE_PORT=8443')
    conn=conninfo_to_dict(os.environ['DATABASE_URL'])
    if conn.get('sslmode')!='verify-full':
        raise ValueError('DATABASE_URL must use sslmode=verify-full and the service CA certificate')
    if conn.get('sslrootcert') and not Path(conn['sslrootcert']).expanduser().is_file():
        raise ValueError('The configured Postgres CA certificate file is missing')


def commands(path):
    return [s.strip() for s in path.read_text().split(';') if s.strip()]


def init():
    with pg() as p:
        p.execute((ROOT/'sql/cloud-postgres.sql').read_text())
    with ch() as c:
        for sql in commands(ROOT/'sql/cloud-app.sql'):c.command(sql)
    print('App schemas ready. Next configure the Postgres CDC ClickPipe.',flush=True)


def views():
    with ch() as c:
        for table in ('foods_cdc','meals_cdc'):
            columns={row[0] for row in c.query(f'DESCRIBE TABLE default.{table}').result_rows}
            required={'id','_peerdb_version','_peerdb_is_deleted'}
            required|={'embedding','embedding_model','embedding_text'} if table=='foods_cdc' else {'items','logged_at'}
            if not required<=columns:
                raise ValueError(f'default.{table} is missing required ClickPipes columns')
        # Refuse to replace unrelated local tables or existing views silently.
        present=c.query("SELECT name FROM system.tables WHERE database='health_demo' AND name IN ('foods','meals')").result_rows
        if present:
            raise ValueError('health_demo.foods/meals already exist; views is a first-setup step, not a migration')
        for sql in commands(ROOT/'sql/cloud-views.sql'):c.command(sql)
    print('CDC compatibility views ready.',flush=True)


def load():
    from health_demo.seed import seed
    from health_demo.usda import main as usda
    from health_demo.cdc import sync
    with ch() as c:
        c.query('SELECT id FROM health_demo.foods LIMIT 0')
        c.query('SELECT id FROM health_demo.meals LIMIT 0')
    print('Loading illustrative foods, sample meals and synthetic activity...',flush=True)
    seed()
    print('Downloading and importing public USDA SR Legacy + FNDDS catalogs...',flush=True)
    usda()
    print('Enriching Postgres catalog with local MiniLM embeddings...',flush=True)
    total=0
    while batch:=sync():
        total+=batch
        print('Embedded catalog rows:',total,flush=True)
    print('Source load complete. ClickPipes replication is asynchronous.',flush=True)
    check(wait=120)


def check(wait=0):
    with pg() as p:
        expected=p.execute('SELECT count(*) AS n FROM foods').fetchone()['n']
        meals=p.execute('SELECT count(*) AS n FROM meals WHERE user_id=%s',(USER,)).fetchone()['n']
    deadline=time.monotonic()+wait
    while True:
        with ch() as c:
            foods=c.query('SELECT count() FROM health_demo.foods WHERE deleted=0').result_rows[0][0]
            replicated=c.query('SELECT count() FROM health_demo.meals WHERE deleted=0 AND user_id={user:String}',parameters={'user':USER}).result_rows[0][0]
            activity=c.query('SELECT count() FROM health_demo.activity WHERE user_id={user:String}',parameters={'user':USER}).result_rows[0][0]
            raw=c.query('SELECT toDate(recorded_at),sum(steps),sum(active_minutes),count() FROM health_demo.activity WHERE user_id={user:String} GROUP BY toDate(recorded_at) ORDER BY toDate(recorded_at)',parameters={'user':USER}).result_rows
            rollup=c.query('SELECT day,sum(steps),sum(active_minutes),sum(samples) FROM health_demo.daily_activity WHERE user_id={user:String} GROUP BY day ORDER BY day',parameters={'user':USER}).result_rows
        if raw!=rollup:raise ValueError('Activity materialized view does not match source aggregates')
        if foods==expected and replicated==meals and foods and activity:
            print(f'Checked: {foods} searchable foods, {replicated} sample meals, {activity} activity rows; daily rollup matches.',flush=True)
            return
        if time.monotonic()>=deadline:
            raise ValueError('Data is incomplete or CDC is still catching up; check the ClickPipe and run check again')
        print('Waiting for ClickPipes replication...',flush=True);time.sleep(3)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['init','views','load-data','check','start'])
    args=parser.parse_args();validate(live=args.command=='start')
    if args.command=='init':init()
    elif args.command=='views':views()
    elif args.command=='load-data':load()
    elif args.command=='check':check()
    else:
        check()
        os.execvp('health-demo',['health-demo','serve'])


if __name__=='__main__':
    try:main()
    except Exception as exc:
        # Driver exception bodies can contain connection strings or SQL parameters.
        print(str(exc) if isinstance(exc,ValueError) else f'{type(exc).__name__}: setup failed; verify connections and the documented setup step',file=sys.stderr)
        sys.exit(1)
