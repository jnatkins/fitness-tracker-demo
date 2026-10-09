import pytest
from health_demo.usda import normalize
from health_demo.tools import Run

def food():
    return {'fdcId':123,'description':'Test food','dataType':'SR Legacy',
            'foodNutrients':[{'nutrient':{'id':i,'unitName':u},'amount':v}
                            for i,u,v in [(1008,'kcal',250),(1003,'g',10),(1004,'g',5),(1005,'g',40)]],
            'foodPortions':[{'gramWeight':20,'amount':1,'modifier':'tbsp','sequenceNumber':1}]}

def test_nutrition_is_per_100g_not_per_source_portion():
    row,label=normalize(food())
    assert row[3:8]==(250,10,40,5,20)
    assert label=='1 tbsp'

def test_missing_nutrition_is_not_zero():
    f=food();f['foodNutrients'].pop()
    with pytest.raises(ValueError,match='missing_carbs'):normalize(f)

def test_kilojoules_not_misread_as_calories():
    f=food();f['foodNutrients'][0]['nutrient']['unitName']='kJ'
    with pytest.raises(ValueError,match='unit_calories'):normalize(f)

def test_jelly_search_and_unrelated_food():
    run=Run();rows=run.call('search_foods',{'query':'jelly'})
    assert rows and rows[0]['name']=='Jelly'
    assert rows[0]['source']['fdc_id']
    assert rows[0]['vegan'] is None
    assert not run.call('search_foods',{'query':'unobtainium moon paste'})

def test_draft_requires_preceding_search():
    with pytest.raises(ValueError,match='Search each food'):
        Run().call('draft_meal',{'label':'Unverified','items':[{'food_id':'banana','grams':100}]})

def test_custom_catalog_food_stays_searchable():
    from uuid import uuid4
    from health_demo.db import pg
    from health_demo.cdc import sync
    fid='test-'+str(uuid4())
    with pg() as p:
        p.execute('INSERT INTO foods SELECT %s,%s,%s,calories,protein,carbs,fat,serving_grams,vegan FROM foods WHERE id=%s',
                  (fid,'Zorblax custom yogurt','Zorblax custom yogurt','yogurt'))
    try:
        sync()
        assert any(r['id']==fid for r in Run().call('search_foods',{'query':'Zorblax custom yogurt'}))
    finally:
        with pg() as p:p.execute('DELETE FROM foods WHERE id=%s',(fid,))
        sync()
