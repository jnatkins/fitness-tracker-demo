"""Profile isolation and date labeling, using only fabricated records and mocks."""
from contextlib import nullcontext
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from health_demo import app, agent, db


def test_profile_metadata_default_and_personal(monkeypatch):
    monkeypatch.setattr(db, 'USER', 'demo-alex')
    assert db.profile_metadata()['synthetic_activity_allowed'] is True
    monkeypatch.setattr(db, 'USER', 'personal-user')
    profile = db.profile_metadata()
    assert profile['name'] == 'Personal profile'
    assert profile['activity_source'] == 'Imported Fitbit activity'
    assert profile['synthetic_activity_allowed'] is False


def test_snapshot_is_scoped_and_uses_actual_latest_day(monkeypatch):
    stamp = datetime(2025, 1, 2, 12, tzinfo=timezone.utc)
    query = Mock(side_effect=[[{'last_recorded': stamp}],
                             [{'day': stamp.date(), 'steps': 123, 'active_minutes': 4}]])
    monkeypatch.setattr(db, 'USER', 'test-personal')
    monkeypatch.setattr(db, 'query', query)
    result = db.activity_snapshot()
    assert result['last_recorded'] == stamp
    assert result['day'] == stamp.date()
    assert all(call.args[1]['user'] == 'test-personal' for call in query.call_args_list)
    assert query.call_args.args[1]['day'] == stamp.date()


def test_empty_snapshot_is_not_zero_activity(monkeypatch):
    monkeypatch.setattr(db, 'query', lambda *a: [{'last_recorded': None}])
    assert db.activity_snapshot() is None


def test_dashboard_keeps_today_nutrition_separate_from_older_activity(monkeypatch):
    today = datetime.now(timezone.utc).date()
    old = today-timedelta(days=4)
    history = [{'day':old,'steps':1234,'active_minutes':15,'calories':0},
               {'day':today,'steps':0,'active_minutes':0,'calories':111}]
    connection = Mock()
    connection.execute.return_value.fetchone.return_value = {'n':0}
    monkeypatch.setattr(app, 'pg', lambda: nullcontext(connection))
    monkeypatch.setattr(app, 'pending_changes', lambda _: 0)
    monkeypatch.setattr(app, 'query', Mock(side_effect=[history,[],[{'n':10}]]))
    monkeypatch.setattr(app, 'activity_snapshot', lambda: {'day':old,'steps':1234,'active_minutes':15})
    result = app.dashboard()
    assert result['today']['day'] == today
    assert result['today']['calories'] == 111
    assert result['today']['steps'] == 0
    assert result['latest_activity_day'] == old
    assert result['latest_activity']['steps'] == 1234
    assert result['today_activity_available'] is False
    assert result['meals'] == []


def test_personal_tick_rejected_before_insert(monkeypatch):
    monkeypatch.setattr(app, 'USER', 'personal-user')
    insert = Mock()
    monkeypatch.setattr(app, 'insert', insert)
    with pytest.raises(HTTPException) as exc:
        app.tick()
    assert exc.value.status_code == 403
    insert.assert_not_called()


def test_demo_tick_unchanged(monkeypatch):
    monkeypatch.setattr(app, 'USER', 'demo-alex')
    insert = Mock()
    monkeypatch.setattr(app, 'insert', insert)
    assert app.tick() == {'ok':True}
    insert.assert_called_once()


def test_personal_prompt_names_source_and_freshness_without_invented_meals(monkeypatch):
    monkeypatch.setattr(agent, 'USER', 'personal-user')
    stamp = datetime(2025, 1, 2, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(agent, 'activity_snapshot', lambda: {'last_recorded':stamp})
    prompt = agent.system_prompt()
    assert stamp.isoformat() in prompt
    assert 'real imported Fitbit data' in prompt
    assert 'Missing activity is unknown, not zero' in prompt
    assert 'No real meal history was imported' in prompt
    assert 'never describe the newest historical row as today' in prompt


def test_rehearsal_does_not_call_old_history_today(monkeypatch):
    monkeypatch.setattr(agent, 'USER', 'demo-alex')
    run = Mock()
    run.call.return_value = [{'day':'2025-01-01','steps':900,'active_minutes':5}]
    answer = agent.rehearsal('compare today',run)
    assert 'no records for today' in answer
    assert 'Today you have recorded 900' not in answer
