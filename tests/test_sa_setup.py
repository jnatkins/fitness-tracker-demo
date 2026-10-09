"""SA bootstrap contract with fabricated settings and mocked databases."""
import importlib.util
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock
import pytest
spec=importlib.util.spec_from_file_location('sa_setup',Path(__file__).parents[1]/'scripts/sa_demo.py')
sa=importlib.util.module_from_spec(spec);spec.loader.exec_module(sa)

@pytest.fixture
def configured(monkeypatch):
    for k,v in {'HEALTH_CLOUD':'1','DATABASE_URL':'host=example.invalid dbname=postgres user=test password=fabricated sslmode=verify-full',
                'CLICKHOUSE_HOST':'example.invalid','CLICKHOUSE_USER':'test','CLICKHOUSE_PASSWORD':'fabricated',
                'CLICKHOUSE_PORT':'8443','CLICKHOUSE_SECURE':'true'}.items():monkeypatch.setenv(k,v)
    monkeypatch.setattr(sa,'USER','demo-alex')
    return monkeypatch


def test_unresolved_credentials_and_tls_fail_before_mutation(configured):
    sa.validate()
    configured.setenv('CLICKHOUSE_PASSWORD','op://example/item/field')
    with pytest.raises(ValueError,match='CLICKHOUSE_PASSWORD'):sa.validate()
    configured.setenv('CLICKHOUSE_PASSWORD','fabricated')
    configured.setenv('DATABASE_URL','host=example.invalid sslmode=require')
    with pytest.raises(ValueError,match='verify-full'):sa.validate()


def test_live_start_requires_model_and_project_config(configured):
    for k in ('AGENT_BASE_URL','AGENT_MODEL','AGENT_API_KEY','LANGFUSE_BASE_URL','LANGFUSE_PUBLIC_KEY','LANGFUSE_SECRET_KEY'):
        configured.delenv(k,raising=False)
    with pytest.raises(ValueError,match='AGENT_BASE_URL'):sa.validate(live=True)
    for k in ('AGENT_BASE_URL','AGENT_MODEL','AGENT_API_KEY'):configured.setenv(k,'fabricated')
    with pytest.raises(ValueError,match='LANGFUSE_BASE_URL'):sa.validate(live=True)
    configured.setenv('LANGFUSE_TRACING_ENABLED','false');sa.validate(live=True)


def test_init_leaves_catalog_replication_to_clickpipes(monkeypatch):
    postgres,clickhouse=Mock(),Mock()
    monkeypatch.setattr(sa,'pg',lambda:nullcontext(postgres))
    monkeypatch.setattr(sa,'ch',lambda:nullcontext(clickhouse))
    sa.init()
    sql=postgres.execute.call_args.args[0]
    assert 'embedding real[]' in sql and 'embedding_text text' in sql
    assert 'CREATE TRIGGER' not in sql and 'change_outbox' not in sql
    commands=[c.args[0] for c in clickhouse.command.call_args_list]
    assert any('activity_rollup' in s for s in commands)
    assert not any('health_demo.foods' in s or 'health_demo.meals' in s for s in commands)


def test_views_refuse_unrelated_existing_objects(monkeypatch):
    client=Mock()
    columns=[(k,) for k in ('id','_peerdb_version','_peerdb_is_deleted','embedding','embedding_model','embedding_text','items','logged_at')]
    client.query.side_effect=[Mock(result_rows=columns),Mock(result_rows=columns),Mock(result_rows=[('foods',)])]
    monkeypatch.setattr(sa,'ch',lambda:nullcontext(client))
    with pytest.raises(ValueError,match='already exist'):sa.views()
    client.command.assert_not_called()


def test_load_orders_seed_import_enrichment_and_replication_check(monkeypatch):
    from health_demo import seed,usda,cdc
    order=[]
    monkeypatch.setattr(seed,'seed',lambda:order.append('seed'))
    monkeypatch.setattr(usda,'main',lambda:order.append('usda'))
    batches=iter([500,0])
    def sync():order.append('enrich');return next(batches)
    monkeypatch.setattr(cdc,'sync',sync)
    monkeypatch.setattr(sa,'check',lambda wait:order.append(('check',wait)))
    monkeypatch.setattr(sa,'ch',lambda:nullcontext(Mock()))
    sa.load()
    assert order==['seed','usda','enrich','enrich',('check',120)]


def test_check_requires_mv_agreement_and_replication(monkeypatch):
    p=Mock();p.execute.return_value.fetchone.side_effect=[{'n':2},{'n':1}]
    c=Mock();raw=[('test-day',40,1,2)]
    c.query.side_effect=[Mock(result_rows=[(2,)]),Mock(result_rows=[(1,)]),Mock(result_rows=[(2,)]),Mock(result_rows=raw),Mock(result_rows=raw)]
    monkeypatch.setattr(sa,'pg',lambda:nullcontext(p));monkeypatch.setattr(sa,'ch',lambda:nullcontext(c))
    sa.check()
    p.execute.return_value.fetchone.side_effect=[{'n':2},{'n':1}]
    c.query.side_effect=[Mock(result_rows=[(1,)]),Mock(result_rows=[(1,)]),Mock(result_rows=[(2,)]),Mock(result_rows=raw),Mock(result_rows=raw)]
    with pytest.raises(ValueError,match='catching up'):sa.check()
