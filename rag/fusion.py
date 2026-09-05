"""① RAG-Fusion：查询变体生成 + ③ RRF 融合（v1.7 RAG）。

变体生成：让弱模型把"选中术语/问题"改写成多个不同角度的检索问句
（定义 / 原理 / 场景 / 对比…），每路各自召回后融合——补足单查询的词面盲区。
RRF（Reciprocal Rank Fusion）：对多路排序列表按 1/(k+rank) 累加打分融合，
纯排序合并、零参数调优、对分数尺度不敏感，是工业界混合检索标配。
"""
import re

_SYS = ("你是检索助手。把用户的学习问题改写成{n}个不同角度的检索问句，"
        "覆盖定义、原理、应用场景、相关概念等侧面。"
        "只输出问句本身，每行一个，不要编号、不要列表符号、不要解释。")

_NOISE = ("好的", "以下是", "我来", "理解", "基于")


def _parse_variants(text):
    """从模型输出解析变体：剥离序号/列表符号/解释噪音行。"""
    out = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        s = re.sub(r"^(\d+[.、)])|^[-*•]|^[:：]", "", s).strip()
        if not s or len(s) < 2:
            continue
        if any(s.startswith(w) for w in _NOISE):
            continue
        if s not in out:
            out.append(s)
    return out


def generate_variants(api, question, n=3):
    """生成 n 个检索变体。模型不可用/输出异常时降级为仅原问题。"""
    if n <= 1 or not question.strip():
        return [question]
    try:
        text = api.chat_once(
            [{"role": "system", "content": _SYS.format(n=n)},
             {"role": "user", "content": question}],
            max_tokens=256, temperature=0.4)
        vars_ = _parse_variants(text)[:n]
        if vars_:
            return vars_
    except Exception:
        pass
    return [question]


def rrf_merge(rankings, k=60, top=None):
    """RRF 融合多路 chunk-id 排序列表，返回融合后降序的 chunk id 列表。

    rankings: [[chunk_id, ...], ...]（每路按相关度降序，1-based rank 计分）。
    """
    scores = {}
    for rank_list in rankings:
        for i, cid in enumerate(rank_list, start=1):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + i)
    order = sorted(scores, key=lambda cid: scores[cid], reverse=True)
    return order[:top] if top else order
