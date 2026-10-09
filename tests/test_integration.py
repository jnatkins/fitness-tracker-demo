"""Runs against the dedicated local health_demo databases; uses unique disposable records."""
from datetime import datetime,timezone,timedelta
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from health_demo.app import app
from health_demo.db import pg,query,USER,insert
from health_demo.cdc import sync
from health_demo.tools import create_draft,update_draft,confirm,HISTORY_SQL,Run
from health_demo.embeddings import embed


def test_meal_confirmation_is_idempotent_and_portions_are_calculated():
    draft=create_draft('Integration meal',[{'food_id':'banana','grams':100}])
    assert draft['calories']==89
    draft=update_draft(draft['id'],[{'food_id':'banana','grams':200}])
    assert draft['calories']==178
    with pg() as p: assert p.execute('SELECT count(*) AS n FROM meals WHERE id=%s',(draft['id'],)).fetchone()['n']==0
    try:
        confirm(draft['id']);confirm(draft['id']);sync()
        rows=query('SELECT calories FROM health_demo.meals FINAL WHERE id={id:String} AND deleted=0',{'id':draft['id']})
        assert len(rows)==1 and rows[0]['calories']==178
        with pytest.raises(ValueError): update_draft(draft['id'],[{'food_id':'banana','grams':50}])
    finally:
        with pg() as p:
            p.execute('DELETE FROM meals WHERE id=%s',(draft['id'],));p.execute('DELETE FROM drafts WHERE id=%s',(draft['id'],))
        sync()


def test_cdc_update_replay_and_delete():
    fid='test-'+str(uuid4())
    with pg() as p:
        p.execute('INSERT INTO foods SELECT %s,name,description,calories,protein,carbs,fat,serving_grams,vegan FROM foods WHERE id=%s',(fid,'banana'))
    try:
        sync()
        with pg() as p:
            p.execute('UPDATE foods SET name=%s WHERE id=%s',('Tropical yellow banana',fid))
        sync()
        with pg() as p: p.execute('UPDATE change_outbox SET processed_at=NULL WHERE record_id=%s',(fid,))
        sync()
        rows=query('SELECT name,embedding FROM health_demo.foods FINAL WHERE id={id:String} AND deleted=0',{'id':fid})
        assert len(rows)==1 and rows[0]['name']=='Tropical yellow banana' and len(rows[0]['embedding'])==384
    finally:
        with pg() as p: p.execute('DELETE FROM foods WHERE id=%s',(fid,))
        sync()
    assert not query('SELECT id FROM health_demo.foods FINAL WHERE id={id:String} AND deleted=0',{'id':fid})


def test_materialized_view_matches_raw():
    raw=query('SELECT sum(steps) AS steps,sum(active_minutes) AS minutes FROM health_demo.activity WHERE user_id={u:String}',{'u':USER})[0]
    mv=query('SELECT sum(steps) AS steps,sum(active_minutes) AS minutes FROM health_demo.daily_activity WHERE user_id={u:String}',{'u':USER})[0]
    assert raw==mv


def test_history_includes_meals_without_activity():
    draft=create_draft('No activity date',[{'food_id':'banana','grams':100}]);confirm(draft['id'])
    day=datetime.now(timezone.utc)-timedelta(days=100)
    try:
        with pg() as p: p.execute('UPDATE meals SET logged_at=%s WHERE id=%s',(day,draft['id']))
        sync()
        rows=query(HISTORY_SQL,{'user':USER})
        row=next(r for r in rows if r['day']==day.date())
        assert row['calories']==89 and row['steps']==0
    finally:
        with pg() as p:
            p.execute('DELETE FROM meals WHERE id=%s',(draft['id'],));p.execute('DELETE FROM drafts WHERE id=%s',(draft['id'],))
        sync()


def test_hybrid_and_history_tools():
    run=Run()
    assert 'peanut butter' in run.call('search_foods',{'query':'peanut butter'})[0]['name'].lower()
    assert all(r['vegan'] for r in run.call('search_foods',{'query':'protein breakfast','vegan':True}))
    assert len(run.call('find_breakfasts',{}))>=1
    assert len(run.call('get_personal_history',{}))>=28


def test_api_guards_and_portion_validation():
    c=TestClient(app)
    assert c.get('/api/dashboard',headers={'Host':'attacker.example'}).status_code==403
    assert c.post('/api/chat',headers={'Origin':'https://attacker.example'},json={'message':'log banana'}).status_code==403
    assert c.post('/api/drafts/update',json={'draft_id':'x','portions':[{'food_id':'banana','grams':-1}]}).status_code==422
    assert c.get('/api/dashboard').status_code==200


def test_ai_embed_live_local_provider():
    # Requires the app's local embedding endpoint running on port 8088.
    r=query("SELECT length(aiEmbed('banana', 'sentence-transformers/all-MiniLM-L6-v2', map('credentials','health_local_embeddings'))) AS n SETTINGS ai_function_allow_insecure_endpoint=1")
    assert r[0]['n']==384
