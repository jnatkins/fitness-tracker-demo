"""Reproducible USDA JSON import. Source files stay under .demo/usda."""
import hashlib
import json
import math
from pathlib import Path
import zipfile
import httpx
from psycopg.types.json import Jsonb
from .db import pg,ROOT

SOURCES={
 'sr': 'https://fdc.nal.usda.gov/fdc-datasets/FoodData_Central_sr_legacy_food_json_2018-04.zip',
 'fndds': 'https://fdc.nal.usda.gov/fdc-datasets/FoodData_Central_survey_food_json_2024-10-31.zip',
}
NUTRIENTS={'calories':(1008,'kcal'),'protein':(1003,'g'),'carbs':(1005,'g'),'fat':(1004,'g')}
DDL='''CREATE TABLE IF NOT EXISTS food_sources (
 food_id text PRIMARY KEY REFERENCES foods(id) ON DELETE CASCADE, fdc_id bigint NOT NULL,
 data_type text NOT NULL, source_url text NOT NULL, archive_url text NOT NULL,
 archive_sha256 text NOT NULL, publication_date text, portions jsonb NOT NULL,
 serving_description text NOT NULL, raw_food jsonb NOT NULL
)'''

def normalize(food):
    values={n['nutrient']['id']:n for n in food.get('foodNutrients',[])}
    macros={}
    for key,(nid,unit) in NUTRIENTS.items():
        n=values.get(nid)
        if n is None or n.get('amount') is None:
            raise ValueError('missing_'+key)
        if n['nutrient']['unitName'].lower()!=unit:
            raise ValueError('unit_'+key)
        value=float(n['amount'])
        if not math.isfinite(value) or value<0: raise ValueError('invalid_'+key)
        macros[key]=value
    portions=food.get('foodPortions',[])
    candidates=sorted((p for p in portions if p.get('gramWeight',0)>0),key=lambda p:p.get('sequenceNumber',999))
    portion=candidates[0] if candidates else None
    grams=float(portion['gramWeight']) if portion else 100.0
    if portion:
        label=portion.get('portionDescription') or f"{portion.get('amount',portion.get('value',1))} {portion.get('modifier','portion')}"
    else: label='100 g reference amount (no household portion supplied)'
    category=food.get('foodCategory',{}).get('description') or food.get('wweiaFoodCategory',{}).get('wweiaFoodCategoryDescription','')
    name=food['description']
    description=f"{name}. {category}. USDA {food['dataType']}. Nutrients per 100 g. Dietary status unknown."
    return (f"fdc-{food['fdcId']}",name,description,*[macros[k] for k in NUTRIENTS],grams,False),label

def main():
    folder=ROOT/'.demo/usda';folder.mkdir(parents=True,exist_ok=True)
    report={}
    with pg() as p:p.execute(DDL)
    for source,url in SOURCES.items():
        path=folder/(source+'.zip')
        if not path.exists():
            with httpx.stream('GET',url,follow_redirects=True,timeout=120) as r:
                r.raise_for_status()
                partial=path.with_suffix('.partial')
                with partial.open('wb') as f:
                    for block in r.iter_bytes(): f.write(block)
                partial.replace(path)
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        with zipfile.ZipFile(path) as z:
            data=json.loads(z.read(next(n for n in z.namelist() if n.endswith('.json'))))
        foods=next(iter(data.values()));skipped=[];changed=0;valid=0
        with pg() as p:
            for food in foods:
                try: row,label=normalize(food)
                except ValueError as e:
                    skipped.append({'fdc_id':food['fdcId'],'reason':str(e)});continue
                valid+=1
                result=p.execute('''INSERT INTO foods(id,name,description,calories,protein,carbs,fat,serving_grams,vegan)
                  VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET
                  name=EXCLUDED.name,description=EXCLUDED.description,calories=EXCLUDED.calories,
                  protein=EXCLUDED.protein,carbs=EXCLUDED.carbs,fat=EXCLUDED.fat,
                  serving_grams=EXCLUDED.serving_grams
                  WHERE (foods.name,foods.description,foods.calories,foods.protein,foods.carbs,foods.fat,foods.serving_grams)
                  IS DISTINCT FROM (EXCLUDED.name,EXCLUDED.description,EXCLUDED.calories,EXCLUDED.protein,EXCLUDED.carbs,EXCLUDED.fat,EXCLUDED.serving_grams)''',row)
                changed+=result.rowcount
                p.execute('''INSERT INTO food_sources VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                 ON CONFLICT(food_id) DO UPDATE SET raw_food=EXCLUDED.raw_food,portions=EXCLUDED.portions,
                 serving_description=EXCLUDED.serving_description,archive_sha256=EXCLUDED.archive_sha256,
                 archive_url=EXCLUDED.archive_url,publication_date=EXCLUDED.publication_date''',
                 (row[0],food['fdcId'],food['dataType'],f"https://fdc.nal.usda.gov/food-details/{food['fdcId']}/nutrients",url,digest,food.get('publicationDate'),Jsonb(food.get('foodPortions',[])),label,Jsonb(food)))
        report[source]={'source_url':url,'sha256':digest,'records':len(foods),'imported':valid,'changed':changed,'skipped':skipped}
        print(source, 'imported',valid,'changed',changed,'skipped',len(skipped),flush=True)
    (folder/'import-report.json').write_text(json.dumps(report,indent=2))

if __name__=='__main__':main()
