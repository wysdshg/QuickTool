"""Markdown/纯文本 -> 检索 chunk 切分（v1.7 RAG，零第三方依赖）。

策略（层级优先，优于纯定长）：
  1. 先按 Markdown 标题（# ~ ####）维护「标题链」，标题作为 chunk 元数据；
  2. 正文按段落累积，逼近 chunk_chars（默认 500 字符）封口；
  3. 相邻 chunk 重叠 chunk_overlap 字符（默认 50），防止句子/术语在边界被腰斩；
  4. 单段超长按句末标点就近硬切；
  5. 代码块 fence（``` / ~~~）内不识别标题、原样保留。

本模块纯函数、无状态、不碰网络/UI，便于单测。
"""
import re

_HEAD_RE = re.compile(r"^(#{1,4})\s+(.*?)\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_SPLIT_CHARS = "。！？；.!?;"


def _nearest_cut(s, limit):
    """在 limit 前尽量找句末标点切；找不到就硬切。"""
    if len(s) <= limit:
        return len(s)
    lo = max(0, limit - 120)
    for i in range(limit - 1, lo, -1):
        if s[i] in _SPLIT_CHARS:
            return i + 1
    return limit


def _split_long(text, cap):
    """把超长单段按句子边界切成 ≤ cap 的若干片。"""
    out, rest = [], text
    guard = 0
    while len(rest) > cap and guard < 10000:
        cut = _nearest_cut(rest, cap)
        piece = rest[:cut].strip()
        if piece:
            out.append(piece)
        rest = rest[cut:].lstrip("\n")
        guard += 1
    rest = rest.strip()
    if rest:
        out.append(rest)
    return out


def _parse_units(text, default_title):
    """解析成 [(标题链, 段落文本), ...]。标题链空则给 default_title。"""
    stack = []          # [(level, text)]，标题层级栈
    paras = []          # [(chain, para)]
    cur = []            # 当前段落行
    in_fence = False

    def flush_para():
        nonlocal cur
        if cur:
            para = "\n".join(cur).strip()
            chain = "/".join(t for _, t in stack) or default_title
            if para:
                paras.append((chain, para))
            cur = []

    for raw in text.splitlines():
        line = raw.strip()
        if not in_fence and _FENCE_RE.match(line):
            in_fence = True
            cur.append(line)
            continue
        if in_fence:
            cur.append(line)
            if _FENCE_RE.match(line):
                in_fence = False
            continue
        if not line:
            flush_para()
            continue
        m = _HEAD_RE.match(line)
        if m:
            flush_para()
            lvl = len(m.group(1))
            txt = m.group(2).strip()
            while stack and stack[-1][0] >= lvl:
                stack.pop()
            stack.append((lvl, txt))
            continue
        cur.append(line)
    flush_para()
    return paras


def split_text(text, name="", chunk_chars=500, chunk_overlap=50):
    """把整篇文档切成 chunks：返回 [{"seq", "title", "content"}, ...]。

    seq 从 0 起连续；title 为标题链（无标题时回退文档名/未命名）；
    chunk_overlap=0 可关闭重叠。
    """
    if not text or not text.strip():
        return []
    default_title = name.strip() or "未命名"
    paras = _parse_units(text, default_title)
    if not paras:
        return []

    chunks = []
    cur_txt = ""            # 当前 chunk 缓冲（可能以重叠尾开头）
    cur_title = None        # 当前缓冲所属标题链

    def emit(keep_tail):
        """把 cur_txt 落成 chunk；keep_tail 时保留尾部 overlap 供下个 chunk 续接。"""
        nonlocal cur_txt
        body = cur_txt.strip()
        if body:
            chunks.append({"seq": len(chunks), "title": cur_title or default_title,
                           "content": body})
        if keep_tail and chunk_overlap > 0 and len(body) > chunk_overlap:
            cur_txt = body[-chunk_overlap:]
        else:
            cur_txt = ""

    for chain, para in paras:
        if chain != cur_title:
            emit(False)             # 标题切换是天然边界：封口不带重叠
            cur_title = chain
        while True:
            if cur_txt and len(cur_txt) + 1 + len(para) <= chunk_chars:
                cur_txt += "\n" + para
                break
            if not cur_txt:
                if len(para) <= chunk_chars:
                    cur_txt = para
                else:
                    pieces = _split_long(para, chunk_chars)
                    for i, piece in enumerate(pieces):
                        if i == 0:
                            cur_txt = piece
                        else:
                            emit(True)
                            cur_txt = piece
                break
            emit(True)              # 超限：封口并带重叠，重新走追加逻辑
    emit(False)
    return chunks
