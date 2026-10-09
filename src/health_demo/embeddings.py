import threading
from functools import lru_cache

MODEL = 'sentence-transformers/all-MiniLM-L6-v2'
_lock = threading.Lock()
_model = None

@lru_cache(maxsize=4096)
def embed(text):
    global _model
    with _lock:
        if _model is None:
            from fastembed import TextEmbedding
            _model = TextEmbedding(model_name=MODEL, cache_dir='.demo/models', threads=2)
        return next(_model.embed([text])).tolist()

def catalog_embed(text):
    import os
    if os.getenv('HEALTH_AI_EMBED') == '1':
        from .db import query
        return query("SELECT aiEmbed({text:String}, 'sentence-transformers/all-MiniLM-L6-v2', map('credentials','health_local_embeddings')) AS vector SETTINGS ai_function_allow_insecure_endpoint=1",{'text':text})[0]['vector']
    return embed(text)

def embed_batch(texts):
    """Local bulk backfill using the same model as the online aiEmbed provider."""
    global _model
    with _lock:
        if _model is None:
            from fastembed import TextEmbedding
            _model = TextEmbedding(model_name=MODEL, cache_dir='.demo/models', threads=2)
        return [v.tolist() for v in _model.embed(texts,batch_size=64)] if texts else []
