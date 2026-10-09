"""Explicit, optional Langfuse tracing; no automatic provider instrumentation."""
from contextlib import contextmanager
from functools import wraps
import inspect
import os
from urllib.parse import urlparse

from langfuse import Langfuse, propagate_attributes

state = {'status': 'Disabled', 'provider': 'Langfuse', 'base_url': None}
_client = None


def readable_trace(value):
    """Retain provider summaries while excluding opaque state and credentials."""
    if isinstance(value, dict):
        return {k: readable_trace(v) for k, v in value.items()
                if k.lower() not in {'encrypted_content', 'authorization', 'api_key',
                                     'secret_key', 'public_key', 'password'}}
    if isinstance(value, (list, tuple)):
        return [readable_trace(v) for v in value]
    return value


def start():
    global _client
    if os.getenv('LANGFUSE_TRACING_ENABLED', '').lower() == 'false':
        return
    public = os.getenv('LANGFUSE_PUBLIC_KEY', '').strip()
    secret = os.getenv('LANGFUSE_SECRET_KEY', '').strip()
    base = os.getenv('LANGFUSE_BASE_URL', '').strip()
    if not any((public, secret, base)):
        return
    if not all((public, secret, base)):
        raise ValueError('Resolve LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and LANGFUSE_BASE_URL before starting tracing')
    url = urlparse(base)
    if not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('LANGFUSE_BASE_URL must be an instance URL without credentials')
    if url.scheme != 'https' and not (url.scheme == 'http' and url.hostname in {'localhost', '127.0.0.1'}):
        raise ValueError('Langfuse requires HTTPS except for local development')
    if os.getenv('HEALTH_USER_ID', 'demo-alex') != 'demo-alex' and os.getenv('HEALTH_ALLOW_PERSONAL_LANGFUSE') != '1':
        raise ValueError('Personal activity tracing requires explicit approval for Langfuse; then set HEALTH_ALLOW_PERSONAL_LANGFUSE=1')
    _client = Langfuse(public_key=public, secret_key=secret, base_url=base,
                      mask=lambda data: readable_trace(data),
                      should_export_span=lambda span: bool(span.instrumentation_scope and
                          span.instrumentation_scope.name == 'langfuse-sdk'))
    state.update(status='Enabled', base_url=base)


def stop():
    global _client
    if _client is not None:
        _client.shutdown()
        _client = None
    state.update(status='Disabled', base_url=None)


def tool_inputs(inputs):
    return {k: v for k, v in inputs.items() if k != 'self'}


@contextmanager
def attributes(values):
    if _client is None:
        yield
        return
    metadata = {k.replace('_', ''): str(v)[:200] for k, v in values.items()}
    with propagate_attributes(metadata=metadata):
        yield


@contextmanager
def thread(conversation_id):
    if _client is None:
        yield
        return
    with propagate_attributes(session_id=conversation_id,
                              user_id=os.getenv('HEALTH_USER_ID', 'demo-alex')):
        yield


def op(*, name, enable_code_capture=False, postprocess_inputs=None, postprocess_output=None):
    """Instrument existing synchronous boundaries without serializing mutable state."""
    def decorate(fn):
        signature = inspect.signature(fn)
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if _client is None:
                return fn(*args, **kwargs)
            inputs = dict(signature.bind(*args, **kwargs).arguments)
            if postprocess_inputs:
                inputs = postprocess_inputs(inputs)
            inputs = {k: v for k, v in inputs.items() if k not in {'self', 'run'}}
            kind = 'agent' if name == 'daylight.agent' else 'tool' if name == 'daylight.tool' else 'span'
            error = None
            with _client.start_as_current_observation(name=name, as_type=kind,
                    input=readable_trace(inputs)) as span:
                try:
                    result = fn(*args, **kwargs)
                except Exception as exc:
                    # HTTP errors may contain sensitive response bodies. Record type only.
                    span.update(level='ERROR', status_message=type(exc).__name__)
                    error = exc
                else:
                    output = postprocess_output(result) if postprocess_output else result
                    span.update(output=readable_trace(output))
            # Exit the SDK context normally to prevent automatic exception-body capture.
            if error is not None:
                raise error
            return result
        return wrapped
    return decorate


@contextmanager
def generation():
    """Keep tools under the generation that requested them."""
    if _client is None:
        yield
        return
    error = None
    with _client.start_as_current_observation(name='daylight.model', as_type='generation',
                                             model=os.getenv('AGENT_MODEL')) as span:
        try:
            yield
        except Exception as exc:
            span.update(level='ERROR', status_message=type(exc).__name__)
            error = exc
    if error is not None:
        raise error


def model_input(payload):
    if _client is not None:
        _client.update_current_generation(input=readable_trace(payload))


def model_output(response):
    if _client is not None:
        raw = response.get('usage') or {}
        usage = {key: raw[source] for key, source in (
            ('input', 'input_tokens' if 'input_tokens' in raw else 'prompt_tokens'),
            ('output', 'output_tokens' if 'output_tokens' in raw else 'completion_tokens'))
            if source in raw}
        _client.update_current_generation(output=readable_trace(response), usage_details=usage)
