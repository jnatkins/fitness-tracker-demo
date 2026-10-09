from uuid import uuid4
from health_demo.db import pg,query,USER
from health_demo.tools import remove_meal,restore_meal,Run
from health_demo.cdc import sync
import pytest

def test_remove_restore_and_ownership(monkeypatch):
    mid=str(uuid4())
    with pg() as p:
        p.execute("INSERT INTO meals VALUES (%s,%s,'Removal test',now(),'[]',123,5,10,3)",(mid,USER))
    try:
        sync()
        assert any(r['id']==mid for r in Run().call('find_logged_meals',{'query':'Removal test'}))
        remove_meal(mid);remove_meal(mid);sync()
        assert query('SELECT deleted FROM health_demo.meals FINAL WHERE id={id:String}',{'id':mid})[0]['deleted']==1
        restore_meal(mid);restore_meal(mid);sync()
        assert query('SELECT deleted,calories FROM health_demo.meals FINAL WHERE id={id:String}',{'id':mid})[0]=={'deleted':0,'calories':123}
        with monkeypatch.context() as m:
            m.setattr('health_demo.tools.USER','someone-else')
            with pytest.raises(ValueError,match='not found'):remove_meal(mid)
    finally:
        with pg() as p:
            p.execute('DELETE FROM meals WHERE id=%s',(mid,))
            p.execute('DELETE FROM removed_meals WHERE id=%s',(mid,))
        sync()
