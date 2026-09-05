"""Markdown/纯文本 -> 检索 chunk 切分（v1.7.1 fence-aware，零第三方依赖）。

策略（层级优先，优于纯定长）：
  1. 先按 Markdown 标题（# ~ ####）维护「标题链」，标题作为 chunk 元数据；
  2. 正文按段落累积，逼近 chunk_chars（默认 500 字符）封口；
  3. 相邻 chunk 重叠 chunk_overlap 字符（默认 50），防止句子/术语在边界被腰斩；
  4. 单段超长按句末标点就近硬切（仅限普通段落）；
  5. 代码块 fence（``` / ~~~）独立成「代码单元」：永不按标点切——
     ≤ CODE_ATOMIC_LIMIT 字符原子并入/独占 chunk（命中即完整代码）；
     超限的直接丢弃不入库（实测该尺寸几乎全是数据 blob：JSON 测试样例/
     DSL 导出，检索命中不了、命中了也没用，见 2026-09-05 语料实测）。

本模块纯函数、无状态、不碰网络/UI，便于单测。
"""
import re

_HEAD_RE = re.compile(r"^(#{1,4})\s+(.*?)\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_SPLIT_CHARS = "。！？；.!?;"

# 代码单元原子化上限：≤ 此值的代码块整体保留（覆盖实测 p99 教学代码 3045 字符）；
# 超过的直接跳过（数据 blob，检索价值趋零且会霸占生成上下文）。
CODE_ATOMIC_LIMIT = 3000


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
    """解析成 [(标题链, 段落文本, 是否代码块), ...]。标题链空则给 default_title。

    fence（```/~~~）块独立成单元并与前后散文 flush 分离：保证代码块作为
    原子单位不被散文稀释、也不把前导散文拖进代码块。
    """
    stack = []          # [(level, text)]，标题层级栈
    paras = []          # [(chain, para, is_code)]
    cur = []            # 当前段落行
    fence = None        # 当前代码块行（None=不在 fence 内）

    def flush_para():
        nonlocal cur
        if cur:
            para = "\n".join(cur).strip()
            chain = "/".join(t for _, t in stack) or default_title
            if para:
                paras.append((chain, para, False))
            cur = []

    def flush_fence():
        nonlocal fence
        if fence:
            para = "\n".join(fence).strip()
            chain = "/".join(t for _, t in stack) or default_title
            if para:
                paras.append((chain, para, True))
            fence = []

    for raw in text.splitlines():
        line = raw.strip()
        if fence is not None:
            fence.append(line)
            if _FENCE_RE.match(line):       # 闭合
                flush_fence()
                fence = None
            continue
        if _FENCE_RE.match(line):           # 开启
            flush_para()
            fence = [line]
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
    flush_fence()           # 未闭合的 fence 也落盘（容错：坏 markdown）
    return paras


def split_text(text, name="", chunk_chars=500, chunk_overlap=50):
    """把整篇文档切成 chunks：返回 [{"seq", "title", "content"}, ...]。

    seq 从 0 起连续；title 为标题链（无标题时回退文档名/未命名）；
    chunk_overlap=0 可关闭重叠。代码块单元：
      - ≤ CODE_ATOMIC_LIMIT：原子段落，永不切（放不进当前 chunk 就独占新 chunk）；
      - > CODE_ATOMIC_LIMIT：整块丢弃（数据 blob）。
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

    for chain, para, is_code in paras:
        if is_code and len(para) > CODE_ATOMIC_LIMIT:
            continue                    # 超限数据 blob：不入库
        if chain != cur_title:
            emit(False)             # 标题切换是天然边界：封口不带重叠
            cur_title = chain
        if is_code:
            # 代码单元原子：放得下就并入，放不下就封口后独占（永不切）
            if cur_txt and len(cur_txt) + 1 + len(para) <= chunk_chars:
                cur_txt += "\n" + para
            else:
                emit(False)
                cur_txt = para
            continue
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
