"""Transactional-outbox CDC: acknowledge only after successful ClickHouse writes.
Replays use the same version; FINAL collapses retries. Not a WAL/ClickPipes emulator.
"""
import hashlib
import json
import os
from datetime import datetime
from .db import pg, insert, LOCK, cloud_mode
from .embeddings import catalog_embed, embed_batch, MODEL

FOOD_COLS = ['id','name','description','calories','protein','carbs','fat','serving_grams','vegan','embedding','embedding_model','content_hash','version','deleted']
# NaN, infinities and null elements are not usable cosine-search vectors.
EMBEDDING_BACKLOG_SQL = """(
    embedding_text IS DISTINCT FROM name || '. ' || description
    OR embedding_model IS DISTINCT FROM %s
    OR coalesce(cardinality(embedding), 0) <> 384
    OR EXISTS (SELECT 1 FROM unnest(embedding) AS v
               WHERE v IS NULL OR NOT (v > '-Infinity'::real AND v < 'Infinity'::real))
)"""

MEAL_COLS = ['id','user_id','label','logged_at','items','calories','protein','carbs','fat','version','deleted']

def enrich_catalog(limit=500):
    """Enrich Postgres rows; ClickPipes owns all cloud catalog/meal replication."""
    with LOCK, pg() as p:
        p.execute('SELECT pg_advisory_xact_lock(7182351)')
        rows = p.execute("SELECT id, name || '. ' || description AS text FROM foods "
                         "WHERE " + EMBEDDING_BACKLOG_SQL + " ORDER BY id LIMIT %s", (MODEL, limit)).fetchall()
        if not rows:
            return 0
        vectors = embed_batch([row['text'] for row in rows])
        if len(vectors) != len(rows):
            raise ValueError('Embedding batch did not match catalog batch')
        updated = 0
        for row, vector in zip(rows, vectors):
            # Do not attach an outdated vector if a catalog edit raced embedding.
            updated += p.execute("UPDATE foods SET embedding=%s::real[], embedding_text=%s, embedding_model=%s "
                                 "WHERE id=%s AND name || '. ' || description = %s "
                                 "AND " + EMBEDDING_BACKLOG_SQL,
                                 (vector, row['text'], MODEL, row['id'], row['text'], MODEL)).rowcount
        return updated


def sync(limit=500):
    if cloud_mode():
        return enrich_catalog(limit)
    with LOCK, pg() as p:
        # One consumer; transaction advisory lock also protects concurrent CLI/API processes.
        p.execute('SELECT pg_advisory_xact_lock(7182351)')
        changes = p.execute('SELECT * FROM change_outbox WHERE processed_at IS NULL ORDER BY id LIMIT %s', (limit,)).fetchall()
        food_rows, meal_rows = [], []
        vectors=iter(embed_batch([c['payload']['name']+'. '+c['payload']['description']
                                 for c in changes if c['entity']=='foods'])) if os.getenv('HEALTH_BULK_EMBED')=='1' else None
        for change in changes:
            d = change['payload']; version = change['id']; deleted = int(change['deleted'])
            if change['entity'] == 'foods':
                text = d['name']+'. '+d['description']
                vector=next(vectors) if vectors is not None else catalog_embed(text)
                food_rows.append([d[k] for k in FOOD_COLS[:9]] + [vector,MODEL,hashlib.sha256(text.encode()).hexdigest(),version,deleted])
            else:
                meal_rows.append([d['id'],d['user_id'],d['label'],datetime.fromisoformat(d['logged_at']),json.dumps(d['items'])] +
                    [d[k] for k in ['calories','protein','carbs','fat']] + [version,deleted])
        insert('foods',food_rows,FOOD_COLS)
        insert('meals',meal_rows,MEAL_COLS)
        if changes:
            p.execute('UPDATE change_outbox SET processed_at=now() WHERE id=ANY(%s)',([c['id'] for c in changes],))
        return len(changes)
