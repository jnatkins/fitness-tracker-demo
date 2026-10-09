from health_demo import agent, tracing

def test_trace_scrub_preserves_runtime_state():
    raw={'output':[{'type':'reasoning','encrypted_content':'opaque','summary':[{'text':'Checking portions'}]}]}
    clean=tracing.readable_trace(raw)
    assert 'encrypted_content' not in clean['output'][0]
    assert raw['output'][0]['encrypted_content']=='opaque'
    assert clean['output'][0]['summary']==raw['output'][0]['summary']

def test_conversation_continuity_isolation_and_bound(monkeypatch):
    agent._sessions.clear()
    seen=[]
    def fake(message,run,history):
        seen.append(history)
        return {'answer':message,'tools':[]}
    monkeypatch.setattr(agent,'traced_chat',fake)
    first=agent.chat('32 grams')
    sid=first['conversation_id']
    agent.chat('halve that',sid)
    assert seen[-1][0]['content']=='32 grams'
    agent.chat('new conversation')
    assert seen[-1]==[]
    for i in range(8): agent.chat(str(i),sid)
    assert len(agent._sessions[sid]['history'])==12

def test_failed_turn_not_added(monkeypatch):
    agent._sessions.clear()
    def fail(*args): raise ValueError('provider failed')
    monkeypatch.setattr(agent,'traced_chat',fail)
    import pytest
    with pytest.raises(ValueError): agent.chat('hello')
    assert next(iter(agent._sessions.values()))['history']==[]

def test_ui_action_updates_history_and_checks_draft(monkeypatch):
    agent._sessions.clear()
    monkeypatch.setattr(agent,'traced_chat',lambda *a:{'answer':'Review draft','tools':[], 'draft':{'id':'draft-1'}})
    sid=agent.chat('food')['conversation_id']
    def confirm(draft_id): return {'ok':True,'id':draft_id}
    agent.conversation_action(sid,'draft-1',confirm)
    assert '"ok": true' in agent._sessions[sid]['history'][-1]['content']
    import pytest
    with pytest.raises(ValueError,match='belong'):
        agent.conversation_action(sid,'other',confirm)
