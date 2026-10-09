import random
from datetime import datetime,timedelta,timezone
from uuid import uuid4,uuid5,NAMESPACE_DNS
from psycopg.types.json import Jsonb
from .db import pg,query,insert,USER
from .cdc import sync

# Illustrative catalog values per 100 g; synthetic demo, not a dietary reference.
FOODS = [
 ('oats','Cooked oatmeal','Warm cooked rolled oats porridge cereal breakfast',71,2.5,12,1.5,240,True),
 ('banana','Banana','Fresh banana fruit sliced',89,1.1,23,.3,118,True),
 ('peanut-butter','Peanut butter','Smooth peanut nut butter spread',588,25,20,50,32,True),
 ('yogurt','Greek yogurt','Plain nonfat cultured Greek yogurt high protein dairy',59,10,3.6,.4,200,False),
 ('berries','Mixed berries','Blueberries raspberries strawberries fresh fruit',50,1,12,.4,80,True),
 ('eggs','Scrambled eggs','Cooked whole eggs breakfast',149,10,1.6,11,120,False),
 ('toast','Whole wheat toast','Toasted whole grain bread',247,13,41,4.2,70,True),
 ('avocado','Avocado','Fresh avocado green fruit',160,2,9,15,70,True),
 ('chicken','Grilled chicken breast','Lean cooked chicken breast poultry',165,31,0,3.6,150,False),
 ('rice','Brown rice','Cooked whole grain brown rice',123,2.7,26,1,180,True),
 ('salmon','Baked salmon','Cooked salmon fish fillet',206,22,0,12,150,False),
 ('tofu','Firm tofu','Soybean curd plant based protein',144,17,3,9,150,True),
 ('quinoa','Quinoa','Cooked quinoa grain bowl',120,4.4,21,1.9,180,True),
 ('broccoli','Steamed broccoli','Cooked green vegetable',35,2.4,7,.4,150,True),
 ('almonds','Almonds','Raw almonds nuts snack',579,21,22,50,28,True),
 ('apple','Apple','Fresh apple fruit',52,.3,14,.2,180,True),
 ('cottage-cheese','Cottage cheese','Low fat cottage cheese dairy breakfast protein',82,11,3.4,2.3,180,False),
 ('granola','Granola','Baked oat cereal with nuts',471,10,64,20,50,True),
 ('lentils','Cooked lentils','Lentil pulses legumes plant protein',116,9,20,.4,180,True),
 ('sweet-potato','Sweet potato','Baked orange sweet potato',90,2,21,.2,200,True),
 ('milk','Milk','Low fat dairy milk',42,3.4,5,1,240,False),
 ('soy-milk','Soy milk','Unsweetened plant based soy drink',33,2.9,1.7,1.8,240,True),
 ('protein-powder','Whey protein powder','Whey dairy protein shake powder',400,80,8,6,30,False),
 ('chia','Chia seeds','Chia seeds for pudding',486,17,42,31,20,True),
]

def nutrients(items, catalog):
    return {k:round(sum(catalog[i['food_id']][k]*i['grams']/100 for i in items),2) for k in ['calories','protein','carbs','fat']}

def seed():
    if USER != 'demo-alex':
        raise ValueError('Synthetic seeding is restricted to demo-alex')
    with pg() as p:
        p.execute('SELECT pg_advisory_xact_lock(7182352)')
        for row in FOODS:
            p.execute('INSERT INTO foods (id,name,description,calories,protein,carbs,fat,serving_grams,vegan) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',row)
        foods = {r['id']:r for r in p.execute('SELECT * FROM foods').fetchall()}
        combos = [('Oatmeal, banana & peanut butter',[('oats',240),('banana',118),('peanut-butter',16)]),
                  ('Greek yogurt, berries & granola',[('yogurt',250),('berries',80),('granola',40)]),
                  ('Eggs on toast',[('eggs',180),('toast',70)]),
                  ('Cottage cheese breakfast bowl',[('cottage-cheese',250),('berries',100),('almonds',20)])]
        today=datetime.now(timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0)
        for day in range(28):
            for slot in range(3 if day else 1):
                if slot == 0: label,parts=combos[day%4]
                elif slot == 1: label,parts='Chicken rice bowl',[('chicken',150),('rice',180),('broccoli',150)]
                else: label,parts='Salmon, quinoa & avocado',[('salmon',150),('quinoa',180),('avocado',70)]
                items=[{'food_id':f,'grams':g,'name':foods[f]['name']} for f,g in parts]
                n=nutrients(items,foods)
                mid=str(uuid5(NAMESPACE_DNS,f'health-demo-{(today-timedelta(days=day)).date()}-{slot}'))
                if p.execute('SELECT 1 FROM removed_meals WHERE id=%s',(mid,)).fetchone(): continue
                p.execute('INSERT INTO meals VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                    (mid,USER,label,today-timedelta(days=day)+timedelta(hours=8+slot*5),Jsonb(items),*[n[k] for k in ['calories','protein','carbs','fat']]))
    while sync(): pass
    existing={str(r['day']) for r in query('SELECT DISTINCT toDate(recorded_at) AS day FROM health_demo.activity')}
    rng=random.Random(42)
    batch=[]
    for u in range(250):
        user=USER if u==0 else f'synthetic-{u:04}'
        for day in range(28):
            if str((today-timedelta(days=day)).date()) in existing: continue
            slots=24 if day==0 else 48
            for slot in range(slots):
                training=(day%3!=0) or day==0
                active=2.5 if training and 12<=slot<=23 else 0
                steps=rng.randint(180,360) if active else rng.randint(15,100)
                batch.append([uuid4(),user,today-timedelta(days=day)+timedelta(minutes=slot*30),steps,active,rng.randint(95,155) if active else rng.randint(55,85),f'watch-{u:04}'])
                if len(batch)>=10000:
                    insert('activity',batch,['event_id','user_id','recorded_at','steps','active_minutes','heart_rate','device_id']);batch=[]
    insert('activity',batch,['event_id','user_id','recorded_at','steps','active_minutes','heart_rate','device_id'])
