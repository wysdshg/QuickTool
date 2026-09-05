"""RagEngine：五阶段 RAG 总装（v1.7.1 fence-aware 上下文）。

  ① RAG-Fusion：LLM 生成 3 个问题变体（失败降级为仅原问题）
  ② 两路召回：每 query 跑 BM25（本地 FTS5）+ bge-m3 向量（云端 embed）
  ③ RRF 融合多路排序 → rrf_top_k
  ④ Rerank：云端 bge-reranker 对候选精排 → rerank_top_k（平台无端点可关）
  ⑤ 上下文组装（同节去重 + 代码配额）→ 大模型流式生成，on_delta 逐字回调

降级纪律：任何单路故障不阻塞问答（变体失败→原问题；embed/rerank 失败→跳过
该路）；检索完全失败才抛 RagApiError 由 UI 提示。网络全部由调用方线程承载
（UI 侧放 worker），本模块不建线程。
"""
import re

from .api import RagApi, RagApiError
from . import bm25, vectors
from .fusion import generate_variants, rrf_merge

_SYS = ("你是耐心且通俗的学习助手。只依据下方【参考资料】回答用户问题；"
        "资料未覆盖到的，先明说“资料未提及”，再用一两句常识补充。"
        "回答口语化、分要点，把机制和细节讲透，通常 500~900 字；"
        "用户明确要求精简时才从简。不要输出 Markdown 符号。"
        "资料中的代码块是参考实现：回答以原理讲解为主，"
        "仅当用户明确要实现或代码时才引用代码。")

_CTX_LIMIT = 6000     # 注入参考上下文总字符上限（8K 档留足余量给问答本体）

_CODE_FENCE_RE = re.compile(r"^\s*(```|~~~)", re.M)


def _select(chunks, code_quota=1):
    """同节去重 + 代码块配额（生成上下文防污染，v1.7.1）。

    - 同节去重：同一文档同一标题链的多个命中只留排名最高的一块——
      防止单一章节连续切片霸占 top_k（rerank 高分常常集中在同一节）；
    - 代码配额：含 fence 的代码块最多带 code_quota 块（默认 1）——
      小模型（8B 级）对大段代码的注意力稀释/复读倾向明显，代码只作为
      参考实现出场一次，其余名额让给理论讲解块。
    """
    seen, out, code_n = set(), [], 0
    for c in chunks:
        key = (c.get("doc_id"), c.get("title"))
        if key in seen:
            continue
        seen.add(key)
        if _CODE_FENCE_RE.search(c.get("content") or ""):
            if code_n >= code_quota:
                continue
            code_n += 1
        out.append(c)
    return out


def _build_context(chunks, limit=_CTX_LIMIT):
    """候选 chunk → 【来源】标注的参考上下文，按序累积至 limit 截断。"""
    parts, used = [], 0
    for i, c in enumerate(chunks, start=1):
        title = (c.get("title") or "未命名")[:60]
        body = (c.get("content") or "").strip()
        piece = f"[来源{i}：{title}]\n{body}"
        if used + len(piece) > limit:
            break
        parts.append(piece)
        used += len(piece)
    return "\n\n".join(parts)


class RagEngine:
    """绑定一个 KbStore + 应用 config；api 惰性创建（首次 ask 时校验 key）。"""

    def __init__(self, store, cfg=None):
        from qt.config import Config
        self.store = store
        self.cfg = cfg or Config()
        self._api = None

    def _retr(self, key, default):
        v = (self.cfg.get("rag.retrieval") or {}).get(key)
        return default if v is None else v

    def api(self):
        if self._api is None:
            self._api = RagApi(self.cfg)
        return self._api

    # ------------------------------------------------------------ 两路召回
    def _recall(self, query, top_k, allow_vector=True):
        """② 两路召回：BM25（必选）+ 向量（allow_vector 且 embed 成功）。

        向量路不依赖 BM25 命中：纯语义查询（无关键词字面重合）时 BM25 可能
        零命中，此时全靠向量路兜底，两路互补。
        """
        hits = bm25.search(self.store, query, top_k=top_k)
        rankings = [[h["id"] for h in hits]]
        if allow_vector:
            try:
                qvec = self.api().embed([query])[0]
            except RagApiError:
                qvec = None
            if qvec:
                vh = vectors.vector_search(self.store, qvec, top_k=top_k,
                                           exclude_ids={h["id"] for h in hits})
                if vh:
                    rankings.append([v["id"] for v in vh])
        return rankings

    # ------------------------------------------------------------ 主流程
    def ask(self, question, extra_context="", on_status=None, on_delta=None):
        """五阶段端到端问答。返回 {"answer", "refs", "stages"}。

        extra_context：用户选中内容（背景）。**只注入生成、不进检索**——
        检索 query 只用 question（背景长文会稀释关键词），而生成侧带上背景
        帮模型消歧（如"AI"既指人工智能也指某个软件）。

        on_status(str)：阶段提示（扩展问题/检索中/精排中/生成中）；
        on_delta(content, reasoning)：流式逐段回调（reasoning 为空因 thinking 已关）。
        """
        def status(msg):
            if on_status:
                on_status(msg)

        ref_retr = self._retr
        if not self.store.chunk_count():
            raise RagApiError("文档库为空：先在文档库中导入学习资料")
        if not question.strip():
            raise RagApiError("问题为空")

        # ① RAG-Fusion 变体（失败降级原问题）
        status("扩展问题…")
        queries = [question]
        if ref_retr("fusion_variants", 3) > 1:
            queries = generate_variants(self.api(), question,
                                        n=int(ref_retr("fusion_variants", 3)))

        # 向量路前置：库内还有 chunk 没算向量就先补算（网络，仅首次/新增文档后
        # 触发一次）。embed 失败降级纯 BM25——语义召回是加分项，不是底线。
        need_vec = ref_retr("enable_vector", True)
        if need_vec:
            try:
                if self.store.vec_count() < self.store.chunk_count():
                    status("向量化文档…")
                    vectors.embed_chunks_missing(
                        self.store, self.api(), batch=32,
                        on_progress=lambda d, t:
                            status(f"向量化文档 {d}/{t}…"))
            except Exception:
                need_vec = False

        # ②+③ 各路召回 → RRF 融合
        status("检索中…")
        rankings = []
        for q in queries:
            try:
                rankings.extend(self._recall(q, int(ref_retr("bm25_top_k", 30)),
                                             allow_vector=need_vec))
            except Exception:
                continue                    # 单 query 失败跳过，其余照常
        if not rankings:
            raise RagApiError("检索失败：请检查网络与 API 配置")
        merged = rrf_merge(rankings, top=int(ref_retr("rrf_top_k", 20)))
        if not merged:
            raise RagApiError("未检索到相关内容，试试换个问法或补充文档")

        # ④ Rerank（enable_rerank 且平台端点可用；失败降级 RRF 直取）
        status("精排中…")
        rrf_top = int(ref_retr("rrf_top_k", 20))
        rerank_k = int(ref_retr("rerank_top_k", 5))
        candidates = self.store.chunk_details(merged[:rrf_top])
        ordered = [candidates[i] for i in merged[:rrf_top] if i in candidates]
        if ref_retr("enable_rerank", True) and len(ordered) > rerank_k:
            try:
                docs = [c["content"] for c in ordered]
                scored = self.api().rerank(question, docs, top_n=rerank_k)
                ordered = [ordered[idx] for idx, _ in scored]
            except RagApiError:
                pass                        # rerank 失败 → RRF 顺序直取
        ordered = ordered[:rerank_k]
        ordered = _select(ordered)      # 同节去重 + 代码配额（防上下文污染）

        # ⑤ 上下文组装 + 流式生成
        status("生成中…")
        context = _build_context(ordered)
        refs = [{"doc_id": c["doc_id"], "title": c["title"],
                 "snippet": (c["content"] or "")[:80]} for c in ordered]
        user_msg = f"【问题】\n{question}"
        bg = (extra_context or "").strip()
        if bg:
            user_msg += f"\n\n【用户选中内容作背景】\n{bg}"
        user_msg += f"\n\n【参考资料】\n{context}"
        messages = [{"role": "system", "content": _SYS},
                    {"role": "user", "content": user_msg}]
        answer = self.api().chat(messages, max_tokens=1500,
                                 temperature=0.3, on_delta=on_delta)
        if not answer:
            raise RagApiError("模型未返回内容，请重试")
        return {"answer": answer, "refs": refs}
