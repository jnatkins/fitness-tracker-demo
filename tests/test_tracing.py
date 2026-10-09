"""Tracing tests use fabricated records and an in-memory exporter only."""
import json
from contextlib import nullcontext
from unittest.mock import Mock
import pytest
from health_demo import tracing, agent

@pytest.fixture(autouse=True)
def clean_tracing(monkeypatch):
    monkeypatch.setattr(tracing, '_client', None)
    monkeypatch.setattr(tracing, 'state', {'status':'Disabled','provider':'Langfuse','base_url':None})
    for key in ('LANGFUSE_PUBLIC_KEY','LANGFUSE_SECRET_KEY','LANGFUSE_BASE_URL',
                'LANGFUSE_TRACING_ENABLED','HEALTH_USER_ID','HEALTH_ALLOW_PERSONAL_LANGFUSE'):
        monkeypatch.delenv(key, raising=False)


def config(monkeypatch):
    monkeypatch.setenv('LANGFUSE_PUBLIC_KEY','test-public')
    monkeypatch.setenv('LANGFUSE_SECRET_KEY','test-secret')
    monkeypatch.setenv('LANGFUSE_BASE_URL','https://example.invalid')


def test_unconfigured_or_disabled_never_initializes(monkeypatch):
    client=Mock();monkeypatch.setattr(tracing,'Langfuse',client)
    tracing.start();client.assert_not_called()
    config(monkeypatch);monkeypatch.setenv('LANGFUSE_TRACING_ENABLED','false')
    tracing.start();client.assert_not_called()


def test_partial_config_and_personal_data_guard(monkeypatch):
    client=Mock();monkeypatch.setattr(tracing,'Langfuse',client)
    monkeypatch.setenv('LANGFUSE_BASE_URL','https://example.invalid')
    with pytest.raises(ValueError,match='LANGFUSE_PUBLIC_KEY'):tracing.start()
    config(monkeypatch);monkeypatch.setenv('HEALTH_USER_ID','personal-user')
    with pytest.raises(ValueError,match='explicit approval'):tracing.start()
    client.assert_not_called()


@pytest.mark.parametrize('base',['http://example.com','https://name:credential@example.com','https://example.com/?key=test'])
def test_destination_validation(monkeypatch,base):
    config(monkeypatch);monkeypatch.setenv('LANGFUSE_BASE_URL',base)
    with pytest.raises(ValueError):tracing.start()


def test_lifecycle_mask_and_export_scope(monkeypatch):
    config(monkeypatch);factory=Mock();monkeypatch.setattr(tracing,'Langfuse',factory)
    tracing.start()
    assert tracing.state['status']=='Enabled'
    options=factory.call_args.kwargs
    assert options['mask'](data={'encrypted_content':'opaque','summary':'visible'})=={'summary':'visible'}
    assert options['should_export_span'](Mock(instrumentation_scope=Mock(name='ignored')))==False
    tracing.stop();factory.return_value.shutdown.assert_called_once()
    assert tracing._client is None


def test_mutable_state_and_opaque_data_are_excluded(monkeypatch):
    client=Mock();span=Mock();client.start_as_current_observation.return_value=nullcontext(span)
    monkeypatch.setattr(tracing,'_client',client)
    @tracing.op(name='daylight.tool',postprocess_inputs=tracing.tool_inputs)
    def call(self,name,args):return {'encrypted_content':'opaque','summary':'visible'}
    result=call(object(),'search_foods',{'query':'banana'})
    inputs=client.start_as_current_observation.call_args.kwargs['input']
    assert inputs=={'name':'search_foods','args':{'query':'banana'}}
    assert span.update.call_args.kwargs['output']=={'summary':'visible'}
    assert result['encrypted_content']=='opaque'


def test_real_sdk_nesting_sessions_usage_and_roundtrip(monkeypatch):
    from langfuse import Langfuse
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    exporter=InMemorySpanExporter()
    client=Langfuse(public_key='test-in-memory-public',secret_key='test-only',
        base_url='http://localhost:1',tracer_provider=TracerProvider(),span_exporter=exporter,
        mask=lambda data:tracing.readable_trace(data))
    monkeypatch.setattr(tracing,'_client',client)
    monkeypatch.setenv('AGENT_MODEL','test-model')
    monkeypatch.setenv('AGENT_BASE_URL','https://example.invalid/v1')
    monkeypatch.setenv('AGENT_API_STYLE','responses')
    monkeypatch.setattr(agent,'system_prompt',lambda:'Test system')
    responses=iter([
        {'output':[{'type':'reasoning','encrypted_content':'opaque-roundtrip','summary':[{'text':'Look up food'}]},
                   {'type':'function_call','call_id':'call1','name':'search_foods','arguments':'{"query":"banana"}'}],
         'usage':{'input_tokens':10,'output_tokens':5}},
        {'output':[{'type':'message','content':[{'type':'output_text','text':'Done'}]}],
         'usage':{'input_tokens':20,'output_tokens':3}}])
    sent=[]
    http=Mock()
    def post(*args,**kwargs):
        sent.append(kwargs['json'])
        response=Mock(status_code=200);response.json.return_value=next(responses);return response
    http.post.side_effect=post
    monkeypatch.setattr(agent.httpx,'Client',lambda **kwargs:nullcontext(http))
    class Run:
        id='test-run';tools=[];removals=[];draft=None
        @tracing.op(name='daylight.tool',postprocess_inputs=tracing.tool_inputs)
        def call(self,name,args):return [{'name':'Banana'}]
    with tracing.thread('conversation-test'),tracing.attributes({'run_id':'test-run'}):
        result=agent.traced_chat('Find banana',Run(),[])
    client.flush()
    spans=exporter.get_finished_spans()
    generations=[s for s in spans if s.name=='daylight.model']
    tool=next(s for s in spans if s.name=='daylight.tool')
    root=next(s for s in spans if s.name=='daylight.agent')
    assert len(generations)==2
    assert tool.parent.span_id==generations[0].context.span_id
    assert all(s.parent.span_id==root.context.span_id for s in generations)
    assert result['answer']=='Done'
    assert 'opaque-roundtrip' in json.dumps(sent[1])
    encoded=json.dumps([dict(s.attributes) for s in spans],default=str)
    assert 'opaque-roundtrip' not in encoded
    assert 'Look up food' in encoded and 'conversation-test' in encoded and 'test-run' in encoded
    assert 'input' in encoded and 'usage' in encoded
    # Every request has a distinct pre-tool/after-tool payload recorded.
    assert 'function_call_output' in encoded
    client.shutdown()


def test_sdk_error_records_type_without_body_or_exception_event(monkeypatch):
    from langfuse import Langfuse
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    exporter=InMemorySpanExporter()
    client=Langfuse(public_key='test-in-memory-errors',secret_key='test-only',
        base_url='http://localhost:1',tracer_provider=TracerProvider(),span_exporter=exporter)
    monkeypatch.setattr(tracing,'_client',client)
    @tracing.op(name='daylight.tool')
    def tool_failure():
        raise ValueError('sensitive-response-must-not-export')
    @tracing.op(name='daylight.agent')
    def failing():
        with tracing.generation():
            tool_failure()
    with pytest.raises(ValueError,match='sensitive-response'):
        failing()
    client.flush()
    spans=exporter.get_finished_spans()
    assert len(spans)==3
    assert all(s.instrumentation_scope.name=='langfuse-sdk' for s in spans)
    assert all('sensitive-response' not in (s.status.description or '') for s in spans)
    assert all(not s.events for s in spans)
    encoded=json.dumps([dict(s.attributes) for s in spans],default=str)
    assert 'ValueError' in encoded
    assert 'sensitive-response' not in encoded
    client.shutdown()
