import asyncio
import os
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime,timezone
from uuid import uuid4
from typing import Literal
from fastapi import FastAPI,HTTPException,Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,Field
from .db import pg,query,insert,USER,ROOT,cloud_mode,profile_metadata,activity_snapshot
from .cdc import sync,EMBEDDING_BACKLOG_SQL
from .tools import HISTORY_SQL,update_draft,confirm,Run,create_draft,remove_meal,restore_meal
from .agent import chat,mode,conversation_action
from .embeddings import MODEL,embed
from . import tracing

pipeline={'last_sync':None,'error':None,'changes':0}

def pending_changes(connection):
    if cloud_mode():
        return connection.execute('SELECT count(*) AS n FROM foods WHERE '+EMBEDDING_BACKLOG_SQL,
                                  (MODEL,)).fetchone()['n']
    return connection.execute('SELECT count(*) AS n FROM change_outbox WHERE processed_at IS NULL').fetchone()['n']


def embedding_status():
    if cloud_mode():
        return 'Local '+MODEL+' → Postgres embedding column → ClickPipes'
    return 'aiEmbed → local '+MODEL if os.getenv('HEALTH_AI_EMBED')=='1' else 'Local '+MODEL


def await_cloud_meal(result, meal_id, present, timeout=10):
    """Wait for the ClickPipes current-state view, never the local outbox."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            rows = query('SELECT count() AS n FROM health_demo.meals WHERE id={id:String} '
                         'AND user_id={user:String} AND deleted=0 SETTINGS max_execution_time=1',
                         {'id':meal_id,'user':USER},
                         receive_timeout=min(2,max(.1,deadline-time.monotonic())))
        except Exception:
            # The Postgres mutation already committed. Do not report it as failed.
            logging.warning('Cloud replication verification unavailable; mutation committed in Postgres')
            return {**result,'replication_pending':True}
        if bool(rows[0]['n']) == present:
            return result
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {**result,'replication_pending':True}
        time.sleep(min(.25,remaining))


async def worker():
    while True:
        try:
            n=await asyncio.to_thread(sync)
            pipeline.update(last_sync=datetime.now(timezone.utc).isoformat(),error=None,changes=pipeline['changes']+n)
        except Exception:
            label='Catalog enrichment' if cloud_mode() else 'CDC'
            logging.exception('%s cycle failed',label)
            pipeline['error']=label+' failed; see server log'
        await asyncio.sleep(2)

@asynccontextmanager
async def lifespan(app):
    await asyncio.to_thread(tracing.start)
    task=asyncio.create_task(worker())
    try:
        yield
    finally:
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass
        await asyncio.to_thread(tracing.stop)

app=FastAPI(lifespan=lifespan)

@app.middleware('http')
async def local_only(request:Request,call_next):
    # Local demo only. Prevent drive-by cross-origin mutations and DNS rebinding.
    host=request.headers.get('host','').split(':')[0]
    allowed={'127.0.0.1','localhost','testserver','host.docker.internal'}
    if host not in allowed: return JSONResponse({'detail':'Local demo only'},status_code=403)
    origin=request.headers.get('origin')
    if origin and origin not in {'http://127.0.0.1:8088','http://localhost:8088'}:
        return JSONResponse({'detail':'Origin not allowed'},status_code=403)
    return await call_next(request)

@app.exception_handler(ValueError)
async def invalid(request,exc): return JSONResponse({'detail':str(exc)},status_code=400)

@app.get('/api/dashboard')
def dashboard():
    rows=query(HISTORY_SQL,{'user':USER})
    today=next((r for r in rows if str(r['day'])==str(datetime.now(timezone.utc).date())),{})
    with pg() as p:
        pending=pending_changes(p)
        food_count=p.execute("SELECT count(*) AS n FROM foods WHERE id LIKE 'fdc-%'").fetchone()['n']
    meals=query('SELECT id,label,calories,protein,logged_at FROM health_demo.meals FINAL WHERE user_id={user:String} AND deleted=0 AND toDate(logged_at)=today() ORDER BY logged_at DESC',{'user':USER})
    latest=activity_snapshot()
    return {'today':today,'history':rows,'meals':meals,'profile':profile_metadata(),
      'latest_activity_day':latest['day'] if latest else None,'latest_activity':latest,
      'today_activity_available':bool(latest and str(latest['day'])==str(datetime.now(timezone.utc).date())),
      'status':{'catalog':f'{food_count:,} USDA foods + 24 illustrative demo foods','postgres':'Connected','clickhouse':'Connected','embedding':embedding_status(),'search':'Exact hybrid ranking over current food versions','agent':mode(),'cdc':(f'Postgres → ClickPipes CDC · {pending} awaiting embedding enrichment' if cloud_mode() else f'{pending} pending · transactional outbox')},
      'event_count':query('SELECT count() AS n FROM health_demo.activity WHERE user_id={user:String}',{'user':USER})[0]['n']}

class Message(BaseModel):
    message:str=Field(min_length=1,max_length=2000)
    conversation_id:str|None=Field(default=None,max_length=36)
@app.post('/api/chat')
def chat_api(body:Message): return chat(body.message,body.conversation_id)
class Confirm(BaseModel):
    draft_id:str
    conversation_id:str|None=Field(default=None,max_length=36)
@app.post('/api/meals/confirm')
def confirm_api(body:Confirm):
    result=conversation_action(body.conversation_id,body.draft_id,confirm)
    if cloud_mode():
        return await_cloud_meal(result,body.draft_id,present=True)
    # Await local replication so the next dashboard read includes this write.
    for _ in range(20):
        sync()
        with pg() as p:
            pending=p.execute("SELECT count(*) AS n FROM change_outbox WHERE entity='meals' AND record_id=%s AND processed_at IS NULL",(body.draft_id,)).fetchone()['n']
        if not pending: return result
    return {**result,'replication_pending':True}
class Portion(BaseModel):
    food_id:str
    grams:float=Field(gt=0,le=2000,allow_inf_nan=False)
class Update(Confirm): portions:list[Portion]=Field(min_length=1,max_length=12)
@app.post('/api/drafts/update')
def update_api(body:Update): return {'draft':conversation_action(body.conversation_id,body.draft_id,update_draft,[p.model_dump() for p in body.portions])}

class FoodSearch(BaseModel):
    query:str=Field(min_length=1,max_length=500)
    mode:Literal['vector','hybrid']='vector'

@app.post('/api/foods/search')
@tracing.op(name='daylight.manual_search',enable_code_capture=False)
def search_food_api(body:FoodSearch):
    run=Run()
    rows=run.call('search_foods',body.model_dump())
    return {'foods':rows,'tools':run.tools}

class ManualDraft(BaseModel):
    label:str=Field(min_length=1,max_length=200)
    items:list[Portion]=Field(min_length=1,max_length=12)

@app.post('/api/drafts/manual')
@tracing.op(name='daylight.manual_draft',enable_code_capture=False)
def manual_draft_api(body:ManualDraft):
    return {'draft':create_draft(body.label,[item.model_dump() for item in body.items])}

class MealAction(BaseModel):
    meal_id:str=Field(min_length=1,max_length=100)

@app.post('/api/meals/remove')
def remove_api(body:MealAction):
    result=remove_meal(body.meal_id)
    return replicate_meal_action(result,body.meal_id,present=False)

@app.post('/api/meals/restore')
def restore_api(body:MealAction):
    result=restore_meal(body.meal_id)
    return replicate_meal_action(result,body.meal_id)

def replicate_meal_action(result,meal_id,present=True):
    if cloud_mode():
        return await_cloud_meal(result,meal_id,present)
    for _ in range(20):
        sync()
        with pg() as p:
            pending=p.execute("SELECT count(*) AS n FROM change_outbox WHERE entity='meals' AND record_id=%s AND processed_at IS NULL",(meal_id,)).fetchone()['n']
        if not pending:return result
    return {**result,'replication_pending':True}

@app.get('/api/meals/removed')
def removed_api():
    with pg() as p:
        return {'meals':p.execute('SELECT id,label,logged_at FROM removed_meals WHERE user_id=%s ORDER BY logged_at DESC LIMIT 100',(USER,)).fetchall()}

@app.get('/api/engineering')
def engineering():
    with pg() as p:
        pending=pending_changes(p)
    rows=query('SELECT recorded_at,kind,tool,duration_ms,detail,query_text,run_id FROM health_demo.events ORDER BY recorded_at DESC LIMIT 50')
    return {'events':rows,'tools':[{**r,'ms':r['duration_ms'],'sql':r['query_text']} for r in rows if r['kind']=='tool_call'],
        'counts':query('SELECT kind,count() AS count FROM health_demo.events GROUP BY kind'),
        'langfuse':dict(tracing.state),
        'pipeline':{**pipeline,'pending':pending,'mechanism':('Postgres catalog enrichment → ClickPipes CDC → ClickHouse' if cloud_mode() else 'Postgres transactional outbox → embedding worker → ClickHouse'),'pending_kind':('catalog rows awaiting embedding enrichment' if cloud_mode() else 'outbox changes'),'worker':('Catalog embedding enrichment; ClickPipes handles replication' if cloud_mode() else 'Outbox replication and embedding'),'embedding':embedding_status(),'search':'Exact hybrid ranking over current food versions','agent':mode()}}

@app.post('/api/activity/tick')
def tick():
    if USER != 'demo-alex':
        raise HTTPException(status_code=403,detail='Synthetic activity is disabled for personal profiles')
    insert('activity',[[uuid4(),USER,datetime.now(timezone.utc),125,1,108,'watch-demo']],['event_id','user_id','recorded_at','steps','active_minutes','heart_rate','device_id'])
    return {'ok':True}

class EmbeddingInput(BaseModel):
    input:str|list[str]
    model:str='sentence-transformers/all-MiniLM-L6-v2'
@app.post('/api/embeddings')
def embedding_endpoint(body:EmbeddingInput):
    texts=[body.input] if isinstance(body.input,str) else body.input
    if len(texts)>32 or any(len(t)>4000 for t in texts): raise ValueError('Embedding batch too large')
    return {'object':'list','model':MODEL,'data':[{'object':'embedding','index':i,'embedding':embed(t)} for i,t in enumerate(texts)],'usage':{'prompt_tokens':0,'total_tokens':0}}

app.mount('/static',StaticFiles(directory=ROOT/'static'),name='assets')
app.mount('/',StaticFiles(directory=ROOT/'static',html=True),name='static')
