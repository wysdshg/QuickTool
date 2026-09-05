"""检索层：FTS5(trigram) BM25 主检索 + LIKE 兜底（v1.7 RAG，零第三方依赖）。

背景（本机实测，sqlite 3.49.1 / trigram）：
  - 中文 / ≥3 字符英文（如 JVM）→ MATCH 直接命中，bm25() 排序可用；
  - <3 字符词（GC / OOM / 2 字中文「多态」）→ trigram MATCH 必返回 0
    （trigram 索引至少要 3 个字符的连续子串）→ 必须 LIKE '%词%' 兜底。
因此本模块策略 = 词面扩展（原词 + 别名）→ FTS MATCH 主路 → 命中不足时
LIKE 子串兜底补足。返回统一结果结构，供 RRF/Rerank 阶段消费。

别名表：内置少量高频术语（可自行扩充），将来可外置 data/rag_aliases.json。
"""
import re

# 高频术语 -> 文档中常见的同义/展开写法（小写键）。命中「选词与资料不同表述」场景。
_ALIASES = {
    "gc": ["垃圾回收", "garbage collection", "garbage collector"],
    "oom": ["内存溢出", "out of memory"],
    "jvm": ["java 虚拟机", "java virtual machine"],
    "jit": ["即时编译", "just-in-time"],
    "aot": ["提前编译", "ahead-of-time"],
    "jdk": ["java 开发工具包", "java development kit"],
    "jre": ["java 运行时", "java runtime"],
    "api": ["应用程序接口", "application programming interface"],
    "sdk": ["软件开发工具包", "software development kit"],
    "oop": ["面向对象", "object-oriented"],
}

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")


def _cjk_sliding(t):
    """超长中文整串不能直接 MATCH（trigram 要求文档内连续子串）——切成
    4 字滑窗（步长 2，补尾），如「垃圾回收算法有哪些」→ 垃圾回收/回收算法
    /算法有哪/法有哪些。≤4 字原样返回。
    """
    if len(t) <= 4:
        return [t]
    wins = {t[i:i + 4] for i in range(0, len(t) - 3, 2)}
    wins.add(t[-4:])
    return list(wins)


def _parse_tokens(text):
    """英文/数字 token 原样；连续中文段切成 4 字滑窗（>4 字时）。"""
    out = []
    for t in _TOKEN_RE.findall(text.lower()):
        if not t:
            continue
        if _CJK_RE.fullmatch(t):
            out.extend(_cjk_sliding(t))
        else:
            out.append(t)
    return out


def expand_terms(text):
    """原词（中文已滑窗）+ 别名展开，去重保序。供 FTS MATCH 与 LIKE 使用。"""
    seen, out = set(), []
    for t in _parse_tokens(text):
        cands = [t] + list(_ALIASES.get(t, []))
        for c in cands:
            if c and c not in seen:
                seen.add(c)
                out.append(c)
    return out


def _fts_query(terms):
    """FTS5 MATCH 查询串：含空格的别名做短语（引号），term 内部引号剥掉。"""
    parts = []
    for t in terms:
        safe = t.replace('"', " ")
        parts.append(f'"{safe}"' if " " in safe else safe)
    return " OR ".join(parts)


def _search_fts(store, terms, top_k, skip_ids=()):
    """FTS MATCH 主路，按 bm25() 升序取 top_k（跳过已收录 chunk id）。"""
    q = _fts_query([t for t in terms if len(t) >= 3])
    if not q:
        return []
    sql = ("SELECT c.id, c.doc_id, c.title, c.content, bm25(kb_fts) AS score"
           " FROM kb_fts JOIN chunks c ON c.id = kb_fts.rowid"
           " WHERE kb_fts MATCH ? ORDER BY score LIMIT ?")
    rows = []
    with store._lock:
        for r in store._conn.execute(sql, (q, top_k * 3)):
            if r["id"] not in skip_ids:
                rows.append({"id": r["id"], "doc_id": r["doc_id"],
                             "title": r["title"], "content": r["content"],
                             "score": r["score"], "match": "fts"})
            if len(rows) >= top_k:
                break
    return rows


def _search_like(store, terms, top_k, skip_ids=()):
    """LIKE '%term%' 兜底（短词/变体），按 chunk id 顺序取未收录的补足。"""
    rows = []
    with store._lock:
        have = set(skip_ids)
        for t in terms:
            if len(rows) >= top_k:
                break
            sql = ("SELECT id, doc_id, title, content FROM chunks"
                   " WHERE content LIKE ? ESCAPE '\\' ORDER BY id LIMIT ?")
            pat = f"%{t}%"
            for r in store._conn.execute(sql, (pat, top_k)):
                if r["id"] in have:
                    continue
                have.add(r["id"])
                rows.append({"id": r["id"], "doc_id": r["doc_id"],
                             "title": r["title"], "content": r["content"],
                             "score": None, "match": "like"})
                if len(rows) >= top_k:
                    break
    return rows


def search(store, query_text, top_k=8):
    """对用户问题做检索，返回统一结果列表（fts 路优先、like 路补足）。

    query_text = 选中名词 + 补充问题（如 "GC 请用通俗的话解释"）。
    """
    terms = expand_terms(query_text)
    if not terms:
        return []
    out = _search_fts(store, terms, top_k)
    if len(out) < top_k:
        out += _search_like(store, terms, top_k - len(out),
                            skip_ids={r["id"] for r in out})
    return out
