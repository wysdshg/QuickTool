"""PDF 文本层提取（v1.7：RAG 文档库 PDF 导入）。

本项目唯一的第三方运行时依赖例外：pypdf（纯 Python，零原生扩展）。理由：
  - 中文 PDF 普遍用 CID 字体，文本流里存的是字形索引而非 Unicode，必须解
    ToUnicode CMap 才能还原中文。实测零依赖手写解流对中文 100% 乱码
    （41704 字符全是不认识的字形码），pypdf 零乱码（2026-09-05，教材/论文/
    简历/英文自造 6 样本验证）。
  - 排除 pypdf 的可选依赖（numpy/PIL/setuptools/pydoc 等）不影响
    extract_text 结果：源码全依赖 / 全量打包 / 瘦身打包三方对同样本提取
    SHA1 逐字节一致（验证记录见 docs，2026-09-05）。

质量体检（text_quality）：pypdf 不是万能的——
  - 扫描版/图片为主的 PDF：文本层字符极少（实测 99 页教材仅 1.1 万字）；
  - CID 字体缺 ToUnicode 映射的 PDF：整篇错映射成泰文/老挝文等"合法"
    Unicode 字符，只看 U+FFFD/PUA 探不出来（实测乱码率指标仅 0.58%），
    必须按「异常脚本占比」检测。
体检结果交给 UI 提示用户，不静默吞掉。
"""
import importlib.util
import os

# 不真 import pypdf：保持模块导入轻量（源码运行没装 pypdf 也能起程序），
# 只在真正导入 PDF 时才触发 import 并给出友好指引。
HAVE_PYPDF = importlib.util.find_spec("pypdf") is not None

_INSTALL_HINT = "需要 pypdf 组件：源码运行时 pip install pypdf（打包版已内置）"

# 判定「异常脚本」用：正常学习资料不会出现的文字区块。泰文/老挝文/高棉文等
# 是 CID 错映射的高发区（实测 s2 教材整篇映射成泰文+老挝文）。
_ODD_RANGES = (
    (0x0E00, 0x0E7F),    # 泰文
    (0x0E80, 0x0EFF),    # 老挝文
    (0x1000, 0x109F),    # 缅甸文
    (0x1780, 0x17FF),    # 高棉文
    (0x0900, 0x097F),    # 天城文
    (0x0980, 0x09FF),    # 孟加拉文
    (0x0A00, 0x0A7F),    # 古木基文
    (0x0A80, 0x0AFF),    # 古吉拉特文
    (0x0B00, 0x0B7F),    # 奥里亚文
    (0x0B80, 0x0BFF),    # 泰米尔文
    (0x0C00, 0x0C7F),    # 泰卢固文
    (0x0C80, 0x0CFF),    # 卡纳达文
    (0x0D00, 0x0D7F),    # 马拉雅拉姆文
    (0x0370, 0x03FF),    # 希腊文（中文资料里出现希腊字母通常也是错映射）
    (0x0590, 0x05FF),    # 希伯来文
    (0x0600, 0x06FF),    # 阿拉伯文
)


def _is_odd_char(ch):
    """异常脚本字符（CID 错映射的典型落点）。"""
    o = ord(ch)
    return any(lo <= o <= hi for lo, hi in _ODD_RANGES)


def _is_cjk(ch):
    o = ord(ch)
    return (0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF
            or 0x3040 <= o <= 0x30FF          # 平假名/片假名
            or 0xAC00 <= o <= 0xD7AF)         # 谚文


# 每页平均字符数低于该值 → 文本层偏少（正文多为图片）
SPARSE_PER_PAGE = 100
# 异常脚本占比超过该值 → 判定乱码
GARBLED_RATIO = 0.10


def extract_pdf(path, on_progress=None):
    """逐页提取 PDF 文本层，返回 [str]（与页码一一对应）。

    on_progress(page_idx, total) 在后台线程被回调（UI 用它刷进度）。
    pypdf 缺失时抛 RuntimeError（带安装指引）。
    """
    if not HAVE_PYPDF:
        raise RuntimeError(_INSTALL_HINT)
    from pypdf import PdfReader          # 局部 import：首用才付 ~150ms
    reader = PdfReader(path)
    total = len(reader.pages)
    pages = []
    for i, page in enumerate(reader.pages):
        try:
            pages.append(page.extract_text() or "")
        except Exception as exc:          # 单页坏不废整本（图片页可能抛）
            pages.append("")
            if on_progress:
                on_progress(i + 1, total, f"第{i + 1}页跳过：{exc}"[:80])
            continue
        if on_progress:
            on_progress(i + 1, total, "")
    return pages


def to_markdown(name, pages):
    """把逐页文本拼成带标题链的 markdown，供 splitter 生成分块来源。

    # 文件名
    ## 第 N 页
    正文……
    """
    base = os.path.basename(name)
    out = [f"# {base}"]
    for i, t in enumerate(pages):
        t = (t or "").strip()
        if not t:
            continue
        out.append(f"## 第 {i + 1} 页\n\n{t}")
    return "\n\n".join(out)


def text_quality(name, pages):
    """提取质量体检。返回 dict：
    pages / chars / per_page / cjk_ratio / odd_ratio / verdict / reason
    verdict: good | sparse | garbled | empty
    """
    txt = "\n".join(pages)
    n = len(txt.strip())
    pages_n = max(len(pages), 1)
    cjk = sum(1 for ch in txt if _is_cjk(ch))
    odd = sum(1 for ch in txt if _is_odd_char(ch))
    q = {
        "pages": len(pages),
        "chars": n,
        "per_page": round(n / pages_n, 1),
        "cjk_ratio": round(cjk / n, 4) if n else 0.0,
        "odd_ratio": round(odd / n, 4) if n else 0.0,
        "verdict": "good",
        "reason": "",
    }
    if n < 10:
        q["verdict"] = "empty"
        q["reason"] = "未提取到文本——扫描版/纯图片 PDF 没有文字层，暂时无法导入检索"
    elif q["odd_ratio"] >= GARBLED_RATIO:
        q["verdict"] = "garbled"
        q["reason"] = (f"文本层疑似乱码（异常字符 {q['odd_ratio'] * 100:.0f}%，"
                       "PDF 字体缺少 Unicode 映射），检索这类内容结果不可靠")
    elif q["per_page"] < SPARSE_PER_PAGE:
        q["verdict"] = "sparse"
        q["reason"] = (f"文本层偏少（平均每页 {q['per_page']:.0f} 字），"
                       "该 PDF 大量正文可能是图片，只有文字部分可被检索")
    return q
