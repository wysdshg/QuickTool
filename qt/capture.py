"""选中文本的自动提取。

Windows 没有「读取任意窗口当前选区」的通用 API，主流做法有三条：

  A. 模拟 Ctrl+C + 读剪贴板  —— 兼容性最好（浏览器/PDF/Word/记事本/代码编辑器/远程桌面都行）
  B. UI Automation TextPattern —— 不污染剪贴板，但很多程序（自绘 UI、PDF、游戏）不支持
  C. WM_GETTEXT / SendMessage  —— 只覆盖标准 Edit/RichEdit 控件

本工具采用 A（并在前后做全格式剪贴板快照/还原，用户几乎无感），
B 作为可选增强写进文档。
"""
import re
import time

from . import winapi as wa

_INVISIBLE = dict.fromkeys(map(ord, "\u00a0\u2007\u202f\u200b\u200e\u200f"), " ")


def normalize_text(text):
    """清洗抓取到的文本。

    从 PDF / 双栏论文里复制出来的文字通常带着换行和断词符：
        "the perfor-\nmance is"  ->  "the performance is"
    不做这步，翻译引擎会把一个词拆成两半，译文质量断崖式下降。
    """
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n").translate(_INVISIBLE)
    out, buf = [], ""
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:                       # 空行 = 段落边界，保留
            if buf:
                out.append(buf)
                buf = ""
            out.append("")
            continue
        if buf and buf.endswith("-") and line[:1].islower():   # 断词符跨行连接
            buf = buf[:-1] + line
        else:
            buf = f"{buf} {line}" if buf else line
    if buf:
        out.append(buf)
    res = "\n".join(out)
    res = re.sub(r"[ \t]{2,}", " ", res)
    res = re.sub(r"\n{3,}", "\n\n", res)
    return res.strip()


def looks_translatable(text):
    """拦截明显不是自然语言的东西（纯数字、纯符号、路径、代码标识符）。"""
    if not text or len(text.strip()) < 1:
        return False
    letters = sum(c.isalpha() for c in text)
    return letters >= max(1, len(text) * 0.3)


def get_selected_text(timeout=0.45, retries=2, first_timeout=None):
    """抓取当前选中的文本。

    关键点：
      1. 抓之前先把 Ctrl/Alt/Shift 抬起来，否则热键的修饰键会和 Ctrl+C 混在一起；
      2. 用剪贴板「序列号」判断复制是否完成，而不是盲等固定毫秒；
      3. 无论成功失败都还原剪贴板，用户之前复制的图片/文件不会被冲掉；
         但只在我们最后一次写入后剪贴板没被外部改过时才还原（v1.5.4：
         期间若系统截屏/其他程序/用户自己写了剪贴板，还原的 EmptyClipboard
         会把别人的新内容冲掉，必须放弃还原）。

    first_timeout：首轮等待时长（默认同 timeout）。大多数程序复制是瞬时的，
    首轮用更短的等待可以让「根本没选中内容」的场景快速失败；慢程序由后续
    重试兜底。注意本函数是**阻塞**的，绝不能在鼠标钩子的消息循环线程里调用
    （详见 main.py 的抓词 worker 注释）。
    """
    snapshot = wa.clipboard_snapshot()
    prev_seq = wa.get_clipboard_sequence()
    text = ""
    seq_last = prev_seq              # 我们最后一次写入剪贴板后的序列号

    for attempt in range(retries):
        wa.release_modifiers()
        wa.send_ctrl_c()
        wait = first_timeout if (attempt == 0 and first_timeout) else timeout
        deadline = time.perf_counter() + wait
        while time.perf_counter() < deadline:
            cur = wa.get_clipboard_sequence()
            if cur != prev_seq:
                candidate = wa.get_clipboard_text()
                if candidate and candidate.strip():
                    text = candidate
                    # 只有读到了文本才说明是我们的 Ctrl+C 复制生效，记下此刻
                    # 序列号；seq 变了但读不到文本（如系统截屏写的是 CF_DIB
                    # 位图）是外部写入，seq_last 必须保持 prev_seq——这样下面
                    # 的还原守卫会因为序列号不匹配而放弃还原，不冲掉别人的内容。
                    seq_last = cur
                break
            time.sleep(0.008)
        if text:
            break
        if attempt < retries - 1:
            time.sleep(0.06)

    # v1.5.4 竞态守卫：还原前校验剪贴板序列号。
    # 快照 -> Ctrl+C -> 还原 期间剪贴板若被外部改动（普通 PrtSc 系统截屏、
    # 其他程序、用户手动复制），序列号会推进；此时还原的 EmptyClipboard
    # 会把别人的新内容冲掉、再写回旧快照——症状就是「普通截屏后 Ctrl+V
    # 粘出来的是上一次 Ctrl+PrtSc 的旧图」。只在序列号仍停留在我们最后一次
    # 写入的时刻才还原。
    if wa.get_clipboard_sequence() == seq_last:
        wa.clipboard_restore(snapshot)
    return normalize_text(text)
