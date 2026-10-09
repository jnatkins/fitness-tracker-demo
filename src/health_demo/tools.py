import json
import re
import time
from datetime import datetime,timezone
from uuid import uuid4
from psycopg.types.json import Jsonb
from .db import pg,query,insert,USER
from .embeddings import embed
from .seed import nutrients,FOODS
from .tracing import op,tool_inputs

HISTORY_SQL = """WITH a AS (
 SELECT day, sum(steps) AS steps, sum(active_minutes) AS active_minutes
 FROM health_demo.daily_activity WHERE user_id={user:String} GROUP BY day
), m AS (
 SELECT toDate(logged_at) AS day,sum(calories) AS calories,sum(protein) AS protein,
 sum(carbs) AS carbs,sum(fat) AS fat FROM health_demo.meals FINAL
 WHERE user_id={user:String} AND deleted=0 GROUP BY day
) SELECT day,sum(steps) AS steps,sum(active_minutes) AS active_minutes,
sum(calories) AS calories,sum(protein) AS protein,sum(carbs) AS carbs,sum(fat) AS fat FROM (
 SELECT day,steps,active_minutes,0 AS calories,0 AS protein,0 AS carbs,0 AS fat FROM a
 UNION ALL SELECT day,0 AS steps,0 AS active_minutes,calories,protein,carbs,fat FROM m
) GROUP BY day ORDER BY day"""
SEARCH_SQL = """SELECT id,name,description,calories,protein,carbs,fat,serving_grams,vegan,
 cosineDistance(embedding,{vector:Array(Float32)}) AS distance,
 length(arrayIntersect(tokens(lowerUTF8(concat(name,' ',description))), {terms:Array(String)})) AS lexical,
 (1-distance)+0.15*lexical AS score
 FROM health_demo.foods FINAL
 WHERE deleted=0 AND (NOT {vegan:Bool} OR vegan) AND protein >= {min_protein:Float64}
 AND (NOT {usda:Bool} OR id NOT IN {demo_ids:Array(String)})
 ORDER BY score DESC LIMIT 5"""

def sources(ids):
    with pg() as p:
        if not p.execute("SELECT to_regclass('food_sources') AS t").fetchone()['t']: return {}
        return {r['food_id']:r for r in p.execute('''SELECT food_id,fdc_id,data_type,source_url,
            portions,serving_description FROM food_sources WHERE food_id=ANY(%s)''',(ids,)).fetchall()}

class Run:
    def __init__(self):
        self.id=str(uuid4());self.tools=[];self.draft=None;self.matched_ids=set();self.removals=[]
    @op(name='daylight.tool',enable_code_capture=False,postprocess_inputs=tool_inputs)
    def call(self,name,args):
        start=time.perf_counter();sql=''
        try:
            if name=='search_foods':
                text=str(args.get('query',''))[:500]
                vegan=bool(args.get('vegan',False))
                # USDA has no universal vegan flag. Keep verified demo vegan results
                # available; do not infer that an unknown USDA food is vegan.
                with pg() as p:
                    usda=p.execute("SELECT EXISTS(SELECT 1 FROM foods WHERE id LIKE 'fdc-%') AS yes").fetchone()['yes'] and not vegan
                sql=SEARCH_SQL if args.get('mode','hybrid')=='hybrid' else SEARCH_SQL.replace('(1-distance)+0.15*lexical AS score','(1-distance) AS score')
                result=query(sql,{'vector':embed(text),'terms':re.findall(r'[a-z]+',text.lower()),'vegan':vegan,'min_protein':float(args.get('min_protein',0)),'usda':usda,'demo_ids':[f[0] for f in FOODS]})
                # Distant semantic neighbors without a lexical match are not evidence
                # that an ingredient exists. Matching is still subject to user review.
                result=[r for r in result if (r['lexical']>0 and r['distance']<=0.65) or r['distance']<=0.45]
                metadata=sources([r['id'] for r in result])
                for r in result:
                    r['source']=metadata.get(r['id'],{'data_type':'Local catalog (not USDA)'})
                    if r['id'] in metadata: r['vegan']=None
                    self.matched_ids.add(r['id'])
            elif name=='find_logged_meals':
                from datetime import date
                day=date.fromisoformat(args.get('day') or datetime.now(timezone.utc).date().isoformat())
                with pg() as p:
                    result=p.execute("SELECT id,label,logged_at,calories,protein FROM meals WHERE user_id=%s AND (logged_at AT TIME ZONE 'UTC')::date=%s AND label ILIKE %s ORDER BY logged_at DESC LIMIT 20",(USER,day,'%'+str(args.get('query',''))[:200]+'%')).fetchall()
                self.removals=result
            elif name=='get_personal_history':
                result=query(HISTORY_SQL,{'user':USER});sql=HISTORY_SQL
            elif name=='find_breakfasts':
                # Compare against the latest recorded breakfast, not an invented calorie target.
                sql="""WITH latest AS (
 SELECT calories,protein FROM health_demo.meals FINAL WHERE user_id={user:String} AND deleted=0
 AND toHour(logged_at)<11 ORDER BY logged_at DESC LIMIT 1)
 SELECT label,round(avg(calories),1) AS calories,round(avg(protein),1) AS protein,count() AS times_logged
 FROM health_demo.meals FINAL WHERE user_id={user:String} AND deleted=0 AND toHour(logged_at)<11
 AND calories BETWEEN (SELECT calories*0.75 FROM latest) AND (SELECT calories*1.25 FROM latest)
 AND protein > (SELECT protein FROM latest)
 GROUP BY label ORDER BY avg(protein) DESC LIMIT 3 SETTINGS prefer_column_name_to_alias=1"""
                result=query(sql,{'user':USER})
            elif name=='draft_meal':
                if any(i['food_id'] not in self.matched_ids for i in args['items']):
                    raise ValueError('Search each food before drafting; use only returned catalog IDs.')
                result=create_draft(args['label'],args['items']);self.draft=result
            else: raise ValueError('Unknown tool')
            ms=round((time.perf_counter()-start)*1000,1)
            detail=f'{len(result)} rows' if isinstance(result,list) else 'Portions ready for your confirmation'
            self.tools.append({'name':name,'ms':ms,'detail':detail,'sql':sql})
            record(self.id,'tool_call',name,ms,detail,sql)
            return result
        except Exception:
            record(self.id,'tool_error',name,(time.perf_counter()-start)*1000,'Tool failed; request was not completed','')
            raise

def record(run,kind,tool,ms,detail,sql=''):
    insert('events',[[uuid4(),run,USER,datetime.now(timezone.utc),kind,tool,ms,detail,sql]],
        ['event_id','run_id','user_id','recorded_at','kind','tool','duration_ms','detail','query_text'])

def make_draft(row):
    with pg() as p:
        foods={f['id']:f for f in p.execute('SELECT * FROM foods WHERE id=ANY(%s)',([x['food_id'] for x in row['items']],)).fetchall()}
    items=[]
    metadata=sources([i['food_id'] for i in row['items']])
    for i in row['items']:
        f=foods.get(i['food_id'])
        if f is None: raise ValueError('A selected food is no longer available; create a new draft.')
        grams=float(i['grams'])
        if not 0<grams<=2000: raise ValueError('Each portion must be between 0 and 2,000 grams.')
        items.append({'food_id':f['id'],'name':f['name'],'grams':grams,**{k:round(f[k]*grams/100,2) for k in ['calories','protein','carbs','fat']}})
        if f['id'] in metadata: items[-1]['source']=metadata[f['id']]
    return {'id':row['id'],'label':row['label'],'items':items,**nutrients(items,foods)}

def create_draft(label,items):
    if not 1<=len(items)<=12: raise ValueError('Choose 1–12 foods.')
    if len({i['food_id'] for i in items})!=len(items): raise ValueError('Combine duplicate food portions.')
    row={'id':str(uuid4()),'label':str(label)[:200],'items':items}
    draft=make_draft(row)
    with pg() as p:
        p.execute('INSERT INTO drafts(id,user_id,label,items) VALUES (%s,%s,%s,%s)',(row['id'],USER,row['label'],Jsonb(draft['items'])))
    return draft

@op(name='daylight.update_portions',enable_code_capture=False)
def update_draft(draft_id,portions):
    with pg() as p:
        row=p.execute('SELECT * FROM drafts WHERE id=%s AND user_id=%s FOR UPDATE',(draft_id,USER)).fetchone()
        if not row or row['confirmed']: raise ValueError('Draft is unavailable or already confirmed.')
        if sorted(i['food_id'] for i in portions)!=sorted(i['food_id'] for i in row['items']): raise ValueError('Update portions of the selected foods only.')
        row['items']=portions
        draft=make_draft(row)
        p.execute('UPDATE drafts SET items=%s WHERE id=%s',(Jsonb(draft['items']),draft_id))
        return draft

@op(name='daylight.confirm_meal',enable_code_capture=False)
def confirm(draft_id):
    with pg() as p:
        row=p.execute('SELECT * FROM drafts WHERE id=%s AND user_id=%s FOR UPDATE',(draft_id,USER)).fetchone()
        if not row: raise ValueError('Draft not found.')
        if row['confirmed']: return {'ok':True,'id':draft_id}
        # Persist the reviewed nutrition snapshot; a catalog edit cannot silently change confirmation.
        items=row['items']
        totals={k:round(sum(i[k] for i in items),2) for k in ['calories','protein','carbs','fat']}
        p.execute('INSERT INTO meals VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',
            (draft_id,USER,row['label'],datetime.now(timezone.utc),Jsonb(items),*[totals[k] for k in totals]))
        p.execute('UPDATE drafts SET confirmed=true WHERE id=%s',(draft_id,))
    record(draft_id,'meal_confirmed','confirm_meal',0,'User confirmed portions')
    return {'ok':True,'id':draft_id}

@op(name='daylight.remove_meal',enable_code_capture=False)
def remove_meal(meal_id):
    with pg() as p:
        p.execute('SELECT pg_advisory_xact_lock(7182352)')
        row=p.execute('SELECT * FROM meals WHERE id=%s AND user_id=%s FOR UPDATE',(meal_id,USER)).fetchone()
        if not row:
            old=p.execute('SELECT id,label FROM removed_meals WHERE id=%s AND user_id=%s',(meal_id,USER)).fetchone()
            if old: return {'ok':True,**old}
            raise ValueError('Meal not found.')
        p.execute('INSERT INTO removed_meals SELECT * FROM meals WHERE id=%s AND user_id=%s ON CONFLICT DO NOTHING',(meal_id,USER))
        p.execute('DELETE FROM meals WHERE id=%s AND user_id=%s',(meal_id,USER))
    return {'ok':True,'id':meal_id,'label':row['label']}

@op(name='daylight.restore_meal',enable_code_capture=False)
def restore_meal(meal_id):
    with pg() as p:
        p.execute('SELECT pg_advisory_xact_lock(7182352)')
        row=p.execute('SELECT * FROM removed_meals WHERE id=%s AND user_id=%s FOR UPDATE',(meal_id,USER)).fetchone()
        if not row:
            existing=p.execute('SELECT id,label FROM meals WHERE id=%s AND user_id=%s',(meal_id,USER)).fetchone()
            if existing: return {'ok':True,**existing}
            raise ValueError('Removed meal not found.')
        p.execute('INSERT INTO meals SELECT * FROM removed_meals WHERE id=%s AND user_id=%s ON CONFLICT DO NOTHING',(meal_id,USER))
        p.execute('DELETE FROM removed_meals WHERE id=%s AND user_id=%s',(meal_id,USER))
    return {'ok':True,'id':meal_id,'label':row['label']}
