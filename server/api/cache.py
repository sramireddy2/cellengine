"""The Redis cache for the expensive half of the pipeline.

Key = sha256(dataset_id + PreprocessParams) computed in engine.params. Two
entries per key so the recolor path never touches the 18 MB expression blob:

    graph:<key>   PCA + kNN + UMAP + barcodes      needed to recluster + draw
    expr:<key>    log-normalized expression        needed for marker genes

Both are written together with the same TTL. A half-present pair (one evicted)
is treated as a miss and recomputed; there is no partial reuse to reason about.
"""
import logging

from django.conf import settings

from engine import cache as ecache
from engine.params import PreprocessParams
from engine.pipeline import PreprocessResult

from .infra import get_redis

log = logging.getLogger(__name__)


def get_preprocessed(key: str, params: PreprocessParams, need_expr: bool = True) -> PreprocessResult | None:
    r = get_redis()
    graph = r.get(f"graph:{key}")
    if graph is None:
        return None
    expr = None
    if need_expr:
        expr = r.get(f"expr:{key}")
        if expr is None:
            log.info("cache: graph present but expr evicted for %s; treating as miss", key)
            return None
    return ecache.deserialize(graph, expr, params)


def put_preprocessed(key: str, result: PreprocessResult) -> dict:
    r = get_redis()
    graph, expr = ecache.serialize_graph(result), ecache.serialize_expr(result)
    ttl = settings.CACHE_TTL_SECONDS
    with r.pipeline() as p:              # atomic: both keys land or neither does
        p.set(f"graph:{key}", graph, ex=ttl)
        p.set(f"expr:{key}", expr, ex=ttl)
        p.execute()
    return {"graph_bytes": len(graph), "expr_bytes": len(expr)}
