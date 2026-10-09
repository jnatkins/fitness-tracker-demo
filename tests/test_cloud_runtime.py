"""Cloud routing and replication contract tests; no database or model downloads."""
from contextlib import nullcontext
from unittest.mock import Mock

import pytest

from health_demo import app, cdc, db


@pytest.fixture
def cloud(monkeypatch):
    monkeypatch.setenv('HEALTH_CLOUD', '1')


def test_cloud_enrichment_writes_only_current_postgres_text(cloud, monkeypatch):
    connection = Mock()
    select = Mock()
    select.fetchall.return_value = [{'id': 'oats', 'text': 'Oats. Breakfast'},
                                   {'id': 'banana', 'text': 'Banana. Fruit'}]
    # Second catalog text changed during embedding, so its conditional update does not match.
    connection.execute.side_effect = [Mock(), select, Mock(rowcount=1), Mock(rowcount=0)]
    monkeypatch.setattr(cdc, 'pg', lambda: nullcontext(connection))
    embed = Mock(return_value=[[.1, .2], [.3, .4]])
    monkeypatch.setattr(cdc, 'embed_batch', embed)
    insert = Mock(side_effect=AssertionError('Cloud catalog must not insert into ClickHouse'))
    monkeypatch.setattr(cdc, 'insert', insert)
    assert cdc.sync(12) == 1
    embed.assert_called_once_with(['Oats. Breakfast', 'Banana. Fruit'])
    calls = connection.execute.call_args_list
    assert 'embedding_text IS DISTINCT FROM' in calls[1].args[0]
    assert calls[1].args[1] == (cdc.MODEL, 12)
    assert all('change_outbox' not in call.args[0] for call in calls)
    update_sql, params = calls[2].args
    assert 'embedding=%s::real[]' in update_sql
    assert "name || '. ' || description = %s" in update_sql
    assert params == ([.1, .2], 'Oats. Breakfast', cdc.MODEL, 'oats', 'Oats. Breakfast', cdc.MODEL)
    insert.assert_not_called()


def test_cloud_empty_backlog_skips_embedding(cloud, monkeypatch):
    connection = Mock()
    connection.execute.return_value.fetchall.return_value = []
    monkeypatch.setattr(cdc, 'pg', lambda: nullcontext(connection))
    embed = Mock(side_effect=AssertionError('No empty embedding call'))
    monkeypatch.setattr(cdc, 'embed_batch', embed)
    assert cdc.sync() == 0
    embed.assert_not_called()


@pytest.mark.parametrize('enabled', [True, False])
def test_outer_final_rewrite_is_cloud_only(monkeypatch, enabled):
    monkeypatch.setenv('HEALTH_CLOUD', '1' if enabled else '0')
    client = Mock()
    client.query.return_value.named_results.return_value = []
    monkeypatch.setattr(db, 'ch', lambda: nullcontext(client))
    sql = 'SELECT * FROM health_demo.foods FINAL JOIN health_demo.meals FINAL ON 1=1'
    db.query(sql, {'x': 1})
    expected = sql.replace(' FINAL', '') if enabled else sql
    client.query.assert_called_once_with(expected, parameters={'x': 1})


def test_cloud_init_refuses_before_connecting(cloud, monkeypatch):
    monkeypatch.setattr(db, 'pg', Mock(side_effect=AssertionError('Must not connect')))
    with pytest.raises(RuntimeError, match='provisioned separately'):
        db.init()


@pytest.mark.parametrize('present,counts', [(True, [0, 1]), (False, [1, 0])])
def test_cloud_poll_checks_expected_view_state(cloud, monkeypatch, present, counts):
    query = Mock(side_effect=[[{'n': n}] for n in counts])
    monkeypatch.setattr(app, 'query', query)
    monkeypatch.setattr(app.time, 'sleep', lambda _: None)
    result = {'ok': True, 'id': 'meal-1'}
    assert app.await_cloud_meal(result, 'meal-1', present) is result
    assert query.call_count == 2
    sql, params = query.call_args.args
    assert 'deleted=0' in sql
    assert ' FINAL' not in sql
    assert params == {'id': 'meal-1', 'user': db.USER}


def test_cloud_poll_flags_timeout(cloud, monkeypatch):
    monkeypatch.setattr(app, 'query', lambda *args, **kwargs: [{'n': 0}])
    assert app.await_cloud_meal({'ok': True}, 'missing', True, timeout=0) == {
        'ok': True, 'replication_pending': True}


def test_cloud_backlog_counts_embeddings_not_outbox(cloud):
    connection = Mock()
    connection.execute.return_value.fetchone.return_value = {'n': 5}
    assert app.pending_changes(connection) == 5
    sql = connection.execute.call_args.args[0]
    assert 'embedding_text IS DISTINCT FROM' in sql
    assert 'change_outbox' not in sql
    assert 'Postgres embedding column' in app.embedding_status()


def test_cloud_confirm_remove_restore_route_without_sync(cloud, monkeypatch):
    monkeypatch.setattr(app, 'conversation_action', lambda *args: {'ok': True})
    monkeypatch.setattr(app, 'remove_meal', lambda _: {'ok': True})
    monkeypatch.setattr(app, 'restore_meal', lambda _: {'ok': True})
    monkeypatch.setattr(app, 'sync', Mock(side_effect=AssertionError('No outbox wait in cloud')))
    wait = Mock(return_value={'ok': True})
    monkeypatch.setattr(app, 'await_cloud_meal', wait)
    app.confirm_api(app.Confirm(draft_id='meal-1'))
    wait.assert_called_with({'ok': True}, 'meal-1', present=True)
    app.remove_api(app.MealAction(meal_id='meal-1'))
    wait.assert_called_with({'ok': True}, 'meal-1', False)
    app.restore_api(app.MealAction(meal_id='meal-1'))
    wait.assert_called_with({'ok': True}, 'meal-1', True)


def test_cloud_backlog_repairs_invalid_vectors_and_stale_model(cloud):
    sql = cdc.EMBEDDING_BACKLOG_SQL
    assert 'embedding_model IS DISTINCT FROM %s' in sql
    assert 'coalesce(cardinality(embedding), 0) <> 384' in sql
    assert 'v IS NULL' in sql
    assert "v > '-Infinity'::real AND v < 'Infinity'::real" in sql


def test_cloud_read_failure_does_not_report_committed_write_as_failed(cloud, monkeypatch):
    query = Mock(side_effect=TimeoutError('read timeout'))
    monkeypatch.setattr(app, 'query', query)
    assert app.await_cloud_meal({'ok': True}, 'meal-1', True) == {
        'ok': True, 'replication_pending': True}
    assert 0 < query.call_args.kwargs['receive_timeout'] <= 2
