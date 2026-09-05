"""稠密向量路（v1.7 RAG，零第三方依赖）。

chunks.vec 列存 L2 归一化后的 float32 向量，自描述格式：
  <uint32 维度><float32 × dim>（小端）
归一化在入库时做一次、查询向量归一化一次 → 检索只需点积（= 余弦）。
全量点积纯 Python：6000 chunk × 1024 维 ≈ 600 万乘加，worker 线程约
0.3-0.8s，可接受（检索本来就是后台线程）。
"""
import heapq
import math
import struct

_HEAD = struct.Struct("<I")


def _norm(v):
    s = 0.0
    for x in v:
        s += x * x
    return math.sqrt(s) if s else 1.0


def normalize(vec):
    """L2 归一化（零向量原样返回避免除零）。"""
    n = _norm(vec)
    return [x / n for x in vec]


def pack(vec):
    return _HEAD.pack(len(vec)) + struct.pack(f"<{len(vec)}f", *vec)


def unpack(blob):
    if not blob:
        return None
    (n,) = _HEAD.unpack_from(blob, 0)
    return list(struct.unpack(f"<{n}f", blob[_HEAD.size:]))


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def embed_chunks_missing(store, api, batch=32):
    """给库内缺失向量的 chunk 批量补算（网络，须在 worker 线程调用）。

    返回本次补算的 chunk 数。注意向量缺失判断 + 更新需事务内串行，
    避免与其它写并发（低频场景直接用内部锁即可）。
    """
    done = 0
    while True:
        with store._lock:
            rows = store._conn.execute(
                "SELECT id, content FROM chunks WHERE vec IS NULL"
                " ORDER BY id LIMIT ?", (batch,)).fetchall()
        if not rows:
            break
        vecs = api.embed([r["content"] for r in rows], batch=batch)
        with store._lock:
            for r, v in zip(rows, vecs):
                store._conn.execute(
                    "UPDATE chunks SET vec = ? WHERE id = ?",
                    (pack(normalize(v)), r["id"]))
            store._conn.commit()
        done += len(rows)
    return done


def vector_search(store, query_vec, top_k, exclude_ids=()):
    """归一化查询向量 → 全量点积取 TOP-K。

    exclude_ids：去重时排除 BM25 路已收录的 chunk id（混合召回用）。
    返回 [{"id","doc_id","title","content","score","match":"vec"}, ...]
    """
    q = normalize(query_vec)
    exclude = set(exclude_ids)
    scores = []                     # (score, id)
    for cid, blob in store.iter_vec_rows():
        if cid in exclude:
            continue
        v = unpack(blob)
        if v is None:
            continue
        scores.append((_dot(q, v), cid))
    top = heapq.nlargest(top_k, scores, key=lambda x: x[0])
    details = store.chunk_details([cid for _, cid in top])
    out = []
    for score, cid in top:
        d = details.get(cid)
        if d:
            out.append({"id": cid, "doc_id": d["doc_id"], "title": d["title"],
                        "content": d["content"], "score": score,
                        "match": "vec"})
    return out
