from datetime import datetime,timezone
import os
import threading
import clickhouse_connect
import psycopg
from psycopg.rows import dict_row
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
USER = os.getenv('HEALTH_USER_ID', 'demo-alex')
LOCK = threading.RLock()

def cloud_mode():
    return os.getenv('HEALTH_CLOUD') == '1'

def pg():
    return psycopg.connect(os.getenv('DATABASE_URL', 'postgresql://health_demo:local-demo-only@127.0.0.1:55432/health_demo'), row_factory=dict_row)

def ch(**options):
    return clickhouse_connect.get_client(host=os.getenv('CLICKHOUSE_HOST','127.0.0.1'),
        port=int(os.getenv('CLICKHOUSE_PORT','58123')), username=os.getenv('CLICKHOUSE_USER','health_demo'),
        password=os.getenv('CLICKHOUSE_PASSWORD','local-demo-only'), secure=os.getenv('CLICKHOUSE_SECURE','false')=='true', **options)

def query(sql, params=None, receive_timeout=None):
    if cloud_mode():
        # Cloud compatibility views already deduplicate their ClickPipes tables.
        for table in ('foods', 'meals'):
            sql = sql.replace(f'health_demo.{table} FINAL', f'health_demo.{table}')
    options = ({'send_receive_timeout': receive_timeout, 'connect_timeout': min(2, receive_timeout)}
               if receive_timeout is not None else {})
    with ch(**options) as c:
        r = c.query(sql, parameters=params or {})
        return [{k:(v.replace(tzinfo=timezone.utc) if isinstance(v,datetime) and v.tzinfo is None else v) for k,v in row.items()} for row in r.named_results()]

def insert(table, rows, columns):
    if rows:
        with ch() as c:
            c.insert('health_demo.'+table, rows, column_names=columns)

def init():
    if cloud_mode():
        raise RuntimeError('Cloud schema is provisioned separately; local init is disabled with HEALTH_CLOUD=1')
    with pg() as p:
        p.execute((ROOT/'sql/postgres.sql').read_text())
    with ch() as c:
        for stmt in (ROOT/'sql/clickhouse.sql').read_text().split(';'):
            if stmt.strip(): c.command(stmt)


def profile_metadata():
    personal = USER != 'demo-alex'
    return {'user_id': USER, 'kind': 'personal' if personal else 'demo',
            'name': 'Personal profile' if personal else 'Alex Morgan',
            'label': 'Fitbit profile' if personal else 'Demo profile',
            'activity_source': 'Imported Fitbit activity' if personal else 'Synthetic demo activity',
            'synthetic_activity_allowed': not personal}


def activity_snapshot():
    """Newest recorded activity for the active profile, distinct from today's meals."""
    last = query('SELECT maxOrNull(recorded_at) AS last_recorded FROM health_demo.activity '
                 'WHERE user_id={user:String}', {'user': USER})[0]['last_recorded']
    if last is None:
        return None
    day = last.date()
    rows = query('SELECT day,sum(steps) AS steps,sum(active_minutes) AS active_minutes '
                 'FROM health_demo.daily_activity WHERE user_id={user:String} AND day={day:Date} '
                 'GROUP BY day', {'user': USER, 'day': day})
    if not rows:
        return None
    return {**rows[0], 'last_recorded': last}
