"""v1.7 RAG 界面：快捷提问窗 + 文档库管理窗（tkinter，零第三方依赖）。

QaWindow：置顶无边框小窗（复用便签窗的窗口骨架）。顶部输入问题按回车即问，
正文按块累积「问 / 答 / 来源」，答案流式追加（engine 在后台 RAG worker 跑，
经 app.q 队列回主线程逐段渲染——窗口方法只在主线程被调用）。
多个问答窗可并存（一个术语一个窗），方便横向对比。

KbManager：文档库管理（导入 .txt/.md/.pdf、删除、清空、看向量化进度）。
向量化是懒触发的：首次提问时 engine 自动补算缺失向量，这里只展示进度。
PDF 导入走后台线程（逐页提取 + 进度），解析完回主线程入库——sqlite 连接
不跨线程，Tk 对象更不跨线程，靠本窗私有 _pdfq 队列 + after 轮询交接。

线程约定：本模块只允许主线程触碰 Tk 对象；RAG 的联网在 app 的 RAG worker。
"""
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

from . import winapi as wa
from .ui import FONT_CN, current_theme

_ASK = "问"
_ANS = "答"
_SRC = "来源"


def _tag(th, name, **kw):
    return name, kw


class QaWindow:
    """快捷提问窗：一问一答一块，可多窗并存、可转存便签。"""

    W, H = 640, 480
    MIN_W, MIN_H = 380, 260
    MAX_W, MAX_H = 2400, 1600
    MAX_CHARS = 30000

    def __init__(self, app, selection="", source="", cascade=0):
        self.app = app
        self.closed = False
        self.busy = False
        self.win_id = 0                 # main 分配：用于 q 队列事件路由
        self._blocks = 0                # 完成的问答块数
        self._trim_from = 0             # 裁剪起点（块号）
        # selection = 用户选中内容（作背景/语境）。**不自动提问**：窗口打开
        # 后光标停在输入框，用户输入问题回车才发；直接回车 = 默认问法。
        # 选中的内容随问题一起发（engine 侧作 extra_context，帮模型消歧），
        # 也渲染在正文顶部，便于确认与存便签回看。
        self._selection = (selection or "").strip()
        self._source = source or ""

        cfg = getattr(app, "cfg", None)
        self.W = self._clamp(int(cfg.get("qa_w", self.W)) if cfg else self.W,
                             self.MIN_W, self.MAX_W)
        self.H = self._clamp(int(cfg.get("qa_h", self.H)) if cfg else self.H,
                             self.MIN_H, self.MAX_H)
        self.th = current_theme(app)
        th = self.th

        win = tk.Toplevel(app.root)
        self.win = win
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-alpha", 0.98)
        win.configure(bg=th["border"], cursor="arrow")

        outer = tk.Frame(win, bg=th["border"])
        outer.pack(fill="both", expand=True)
        self._outer = outer
        # 三行 grid：0=bar 1=txt(扩展) 2=输入区(固定)。
        # ⚠️ 用 grid 不用 pack：ScrolledText 默认高度~560，cavity 一旦
        # 不够 pack 会把非 expand widget 按比例压到 0（输入框全没）。
        # grid + rowconfigure(1, weight=1) 让扩展只发生在 txt 行，输入区永
        # 远保留自然高度。其它子 widget（entry/ask_btn）仍用 pack 横向布局。
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)
        self._build_bar(outer)

        # grid 到 outer row=1（可扩展行）
        self.txt = ScrolledText(
            outer, wrap="word", state="disabled",
            bg=th["card"], fg=th["fg"],
            relief="flat", bd=0, padx=10, pady=6,
            font=(FONT_CN, 10), spacing1=2, spacing3=2,
            height=10)
        self.txt.grid(row=1, column=0, sticky="nsew")
        self.txt.tag_configure(_ASK, foreground=th["accent"],
                               font=(FONT_CN, 9, "bold"))
        self.txt.tag_configure(_ANS, foreground=th["fg"])
        self.txt.tag_configure(_SRC, foreground=th["sub"],
                               font=(FONT_CN, 8))
        self.txt.tag_configure("err", foreground=th["danger"],
                               font=(FONT_CN, 9, "bold"))
        self.txt.tag_configure("hint", foreground=th["sub"],
                               font=(FONT_CN, 9))
        self._build_input(outer)

        # 右下角缩放手柄（无边框窗无系统缩放框，自补）
        grip = tk.Label(win, text="◢", font=(FONT_CN, 10),
                        fg=th["sub"], bg=th["card"],
                        cursor="bottom_right_corner")
        grip.place(relx=1.0, rely=1.0, anchor="se")
        grip.bind("<ButtonPress-1>", self._rs_start)
        grip.bind("<B1-Motion>", self._rs_move)
        self._grip = grip

        self._place(cascade)
        self._bind()
        win.after(30, lambda: wa.set_tool_window(win.winfo_id(), True))
        if self._selection:
            self._render_selection()
            self.status("输入问题回车提问（直接回车 = 解释选中内容）")
        else:
            self.status("选中术语按快捷键可带背景提问，或直接输入问题回车")
        # 从不自动发送：只把光标放好，等用户输入/回车
        win.after(150, self._focus_input)

    def _render_selection(self):
        """把选中内容渲染成正文顶部一条灰字背景（截断展示，存便签可回看）。"""
        sel = self._selection
        shown = sel if len(sel) <= 240 else sel[:240] + "…"
        head = f"〔选中背景〕{shown}\n" if sel else ""
        if head:
            self._edit(lambda: self.txt.insert("end", head, "hint"))
            self._edit(lambda: self.txt.insert("end", "\n", "hint"))

    # ------------------------------------------------------------ 构建
    def _build_bar(self, parent):
        th = self.th
        bar = tk.Frame(parent, bg=th["card"])
        # grid row=0 固定顶条
        bar.grid(row=0, column=0, sticky="ew")
        self._bar = bar
        self.lbl = tk.Label(bar, text="快捷提问 · Esc 关闭",
                            font=(FONT_CN, 9), fg=th["sub"], bg=th["card"])
        self.lbl.pack(side="left", padx=8, pady=3)
        self._bar_btns = []
        for txt, cmd in ((" ✕ ", self.close), (" 存便签 ", self._to_note),
                         (" 复制 ", self._copy_all), (" 清屏 ", self._clear)):
            b = tk.Label(bar, text=txt, font=(FONT_CN, 9),
                         fg=th["fg"], bg=th["card"],
                         cursor="hand2", padx=4)
            b.pack(side="right")
            b.bind("<Button-1>", lambda e=None, c=cmd: c())
            self._bar_btns.append(b)

    def _build_input(self, parent):
        th = self.th
        # 整个输入区包进 box，grid 到 outer row=2 固定底（weight=0 不被压）
        box = tk.Frame(parent, bg=th["border"])
        box.grid(row=2, column=0, sticky="ew")
        self.status_lbl = tk.Label(box, text="", font=(FONT_CN, 8),
                                   fg=th["sub"], bg=th["border"],
                                   anchor="w")
        self.status_lbl.pack(side="bottom", fill="x", padx=10, pady=(0, 3))
        row = tk.Frame(box, bg=th["border"])
        row.pack(side="bottom", fill="x")
        inner = tk.Frame(row, bg=th["card"])
        inner.pack(fill="x", padx=4, pady=(0, 4))
        self.entry = tk.Entry(
            inner, bg=th["card"], fg=th["fg"],
            insertbackground=th["fg"], relief="flat", bd=0,
            font=(FONT_CN, 10))
        self.entry.pack(side="left", fill="x", expand=True,
                        padx=8, pady=4)
        self.ask_btn = tk.Label(inner, text=" 提问 ", font=(FONT_CN, 9),
                                fg=th["on_accent"], bg=th["accent"],
                                cursor="hand2", padx=8, pady=2)
        self.ask_btn.pack(side="right", padx=(4, 6))
        self.ask_btn.bind("<Button-1>", lambda e=None: self.ask())

    # ------------------------------------------------------------ 状态/流式
    def status(self, msg):
        try:
            self.status_lbl.configure(text=msg)
        except Exception:
            pass

    def _set_busy(self, busy):
        self.busy = busy
        th = self.th
        try:
            if busy:
                self.ask_btn.configure(bg=th["hover"], fg=th["sub"])
            else:
                self.ask_btn.configure(bg=th["accent"], fg=th["on_accent"])
        except Exception:
            pass

    def ask(self, question=None):
        """主线程触发提问（回车 / 按钮）。busy 时忽略。

        问题 = 输入框文字；为空但带选中背景 → 默认问法「解释一下选中的内容」。
        请求 = 问题 + 背景（selection）一起交给 app.ask_rag → engine 侧
        question 只作检索 query、selection 作生成的消歧背景。
        """
        if self.closed:
            return
        if self.busy:
            self.status("上一条还在生成…")
            return
        q = (question if question is not None
             else self.entry.get()).strip()
        if not q:
            if self._selection:
                q = "解释一下选中的内容"
            else:
                self.status("问题为空：直接回车需要先选中内容作背景，或输入问题")
                return
        asker = getattr(self.app, "ask_rag", None)
        if not asker:
            self._toast("未就绪", "问答功能未初始化")
            return
        self._begin_block(q)
        self._set_busy(True)
        self.status("排队中…")
        asker(self, q, self._selection)
        # 发送后清空输入框，方便连续问（仅清输入框来源的；显式传参不清）
        if question is None:
            try:
                self.entry.delete(0, "end")
            except Exception:
                pass

    def _focus_input(self, retry=0):
        """把光标抢进输入框（热键弹窗场景）。

        热键 WM_HOTKEY 在 Win32 线程处理、弹窗在主线程，Windows 前台锁
        常导致新窗弹出但键盘焦点留在源窗口——必须三层抢：
        ① Tk focus_force ② Win32 force_foreground（模拟 Alt 骗过前台守卫）
        ③ 窗口映射/前台切换异步，兜底 200/500ms 重试两轮，期间 GetFocus 验证。
        """
        try:
            if self.closed:
                return
            self.win.lift()
            self.win.deiconify()
            self.entry.focus_set()
            self.entry.focus_force()
            wa.force_foreground(self.win.winfo_id())
            if retry < 2 and not self._input_has_focus():
                self.win.after((200, 500)[retry],
                               lambda: self._focus_input(retry + 1))
        except Exception:
            pass

    def _input_has_focus(self):
        """GetFocus 判定 entry 是否真的拿到系统键盘焦点（同一主线程查询）。"""
        try:
            cur = wa.user32.GetFocus()
            return bool(cur) and cur == self.entry.winfo_id()
        except Exception:
            return True            # 查询失败不阻塞（避免无限重试）

    # ------------------------------------------------------------ 主线程渲染
    def _begin_block(self, q):
        """新问答块：问句标蓝，答从块尾开始流式插入。"""
        self._blocks += 1
        self._edit(lambda: self.txt.insert(
            "end", f"【问】{q}\n", _ASK))
        self._trim()

    def on_delta(self, chunk):
        """流式片段（主线程）。"""
        if self.closed or not chunk:
            return
        self._edit(lambda: self.txt.insert("end", chunk, _ANS))
        self._edit(lambda: self.txt.see("end"))

    def on_status(self, msg):
        if self.closed:
            return
        self.status(msg)

    def on_done(self, answer, refs, err):
        """问答结束（主线程）。answer 可为 None（err 非空）。"""
        if self.closed:
            return
        self._set_busy(False)
        if err:
            self._edit(lambda: self.txt.insert(
                "end", f"\n⚠ {err}\n", "err"))
            self.status("失败")
            return
        if answer:
            self._edit(lambda: self.txt.insert("end", "\n", _ANS))
        if refs:
            line = "  ".join(
                f"{i}·{(r.get('title') or '未命名')[:26]}"
                for i, r in enumerate(refs, 1))
            self._edit(lambda: self.txt.insert(
                "end", f"[来源] {line}\n\n", _SRC))
        self._edit(lambda: self.txt.see("end"))
        self.status("完成")
        self.lbl.configure(
            text=f"快捷提问 · {self._blocks} 问 · Esc 关闭")

    # ------------------------------------------------------------ 工具
    def _edit(self, fn):
        """Text 在 disabled 状态下也要能写：临时切 normal。"""
        try:
            self.txt.configure(state="normal")
            fn()
            self.txt.configure(state="disabled")
        except Exception:
            pass

    def _trim(self):
        """防爆：超过上限从最早的块整段裁掉。"""
        try:
            while (self.txt.count("1.0", "end", "chars") or [0])[0] > \
                    self.MAX_CHARS and self._trim_from < self._blocks - 1:
                self._trim_from += 1
                first_line = f"1.0"
                # 每块以【问】开头，找第 _trim_from+1 个【问】的位置
                idx = self.txt.search("【问】", first_line)
                if not idx:
                    break
                nxt = self.txt.search("【问】", idx + "+1c")
                if not nxt:
                    nxt = "end"
                self._edit(lambda: self.txt.delete(first_line, nxt))
        except Exception:
            pass

    def _copy_all(self):
        text = self._all_text()
        if not text:
            self._toast("复制", "还没有内容。")
            return
        try:
            wa.set_clipboard_text(text)
        except Exception as exc:
            self._toast("复制失败", str(exc))
            return
        self._toast("已复制", f"{len(text)} 字")

    def _to_note(self):
        text = self._all_text()
        if not text:
            self._toast("存便签", "还没有内容。")
            return
        opener = getattr(self.app, "open_note", None)
        if not opener:
            return
        try:
            opener(text, "快捷提问")
        except Exception as exc:
            self._toast("存便签失败", str(exc))
            return
        self._toast("已存入便签", "可在置顶便签里继续编辑")

    def _clear(self):
        self._edit(lambda: self.txt.delete("1.0", "end"))
        self._blocks, self._trim_from = 0, 0
        self.lbl.configure(text="快捷提问 · Esc 关闭")
        self.status("已清屏")

    def _all_text(self):
        return self.txt.get("1.0", "end-1c")

    def _toast(self, title, body):
        q = getattr(self.app, "q", None)
        if q is not None:
            q.put(("toast", (title, body)))

    # ------------------------------------------------------------ 交互
    def _place(self, cascade=0):
        w, h = self.W, self.H
        x = y = None
        cfg = getattr(self.app, "cfg", None)
        if cfg is not None:
            try:
                rx, ry = cfg.get("qa_x", -9999), cfg.get("qa_y", -9999)
                if isinstance(rx, int) and isinstance(ry, int):
                    left, top, right, bottom = wa.get_work_area_at(rx, ry)
                    x = self._clamp(rx, left, right - w - 4)
                    y = self._clamp(ry, top, bottom - h - 4)
            except Exception:
                x = y = None
        if x is None:
            cx, cy = wa.get_cursor_pos()
            left, top, right, bottom = wa.get_work_area_at(cx, cy)
            # 多窗级联：新窗相对鼠标/记忆点错开一点，别完全叠住上一个
            dx = (cascade % 6) * 28
            dy = (cascade % 6) * 28
            x = self._clamp(cx - w // 2 + dx, left + 4, right - w - 4)
            y = self._clamp(cy - h // 2 + dy, top + 4, bottom - h - 4)
        self.win.geometry(f"{w}x{h}+{int(x)}+{int(y)}")

    def _bind(self):
        win = self.win
        win.bind("<Escape>", lambda e=None: self.close())
        win.bind("<Control-l>", lambda e=None: self._clear())
        self.entry.bind("<Return>", lambda e=None: self.ask())
        self._bar.bind("<ButtonPress-1>", self._drag_start)
        self._bar.bind("<B1-Motion>", self._drag_move)
        win.bind("<ButtonPress-1>", self._raise, add="+")

    def _raise(self, e=None):
        try:
            self.win.lift()
        except Exception:
            pass

    def _drag_start(self, event=None):
        if event is None:
            return
        self._dx, self._dy = event.x, event.y

    def _drag_move(self, event=None):
        if event is None:
            return
        try:
            self.win.geometry(f"+{self.win.winfo_x() + event.x - self._dx}"
                              f"+{self.win.winfo_y() + event.y - self._dy}")
        except Exception:
            pass

    @staticmethod
    def _clamp(v, lo, hi):
        try:
            v = int(v)
        except Exception:
            return lo
        return max(lo, min(hi, v))

    def _rs_start(self, event=None):
        if event is None:
            return
        self._rs = (event.x_root, event.y_root,
                    self.win.winfo_width(), self.win.winfo_height())

    def _rs_move(self, event=None):
        if event is None:
            return
        try:
            x0, y0, w0, h0 = self._rs
        except AttributeError:
            return
        w = self._clamp(w0 + event.x_root - x0, self.MIN_W, self.MAX_W)
        h = self._clamp(h0 + event.y_root - y0, self.MIN_H, self.MAX_H)
        self.W, self.H = w, h
        self.win.geometry(f"{w}x{h}")

    def _save_geometry(self):
        cfg = getattr(self.app, "cfg", None)
        if cfg is None:
            return
        try:
            w = self.win.winfo_width()
            h = self.win.winfo_height()
            if w < self.MIN_W:
                w = self.W
            if h < self.MIN_H:
                h = self.H
            cfg.set("qa_w", int(w))
            cfg.set("qa_h", int(h))
            cfg.set("qa_x", int(self.win.winfo_x()))
            cfg.set("qa_y", int(self.win.winfo_y()))
            cfg.save()
        except Exception:
            pass

    def close(self):
        if self.closed:
            return
        self.closed = True
        self._save_geometry()
        try:
            self.win.destroy()
        except Exception:
            pass
        wins = getattr(self.app, "qa_wins", None)
        if wins is not None and self in wins:
            try:
                wins.remove(self)
            except Exception:
                pass


# ============================================================ 文档库管理
class KbManager(tk.Toplevel):
    """文档库管理窗：导入 .txt/.md/.pdf、删除、清空、查看向量化进度。

    PDF 导入（v1.7）：pypdf 逐页提取在后台线程跑（进度经 _pdfq 回主线程），
    解析完拼成带「第 N 页」标题链的 markdown 入库，并做质量体检——
    扫描版（无文字层）与 CID 乱码 PDF 当场提示，不让坏文本静默入库。
    """

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.cfg = app.cfg
        self.th = current_theme(app)
        th = self.th
        self.title("QuickTool 文档库")
        self.geometry("620x430")
        self.minsize(460, 300)
        self.configure(bg=th["win"])
        self.deiconify()
        self.lift()
        self.focus_force()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._build()
        self.refresh()

    def _store(self):
        return self.app.ensure_kb_store()

    def _build(self):
        th = self.th
        head = tk.Label(self, text="把学习资料（.txt / .md / .pdf）导入后即可用快捷提问检索。"
                                   "向量在首次提问时自动补齐。",
                        font=(FONT_CN, 8), fg=th["sub"], bg=th["win"],
                        anchor="w", justify="left")
        head.pack(fill="x", padx=10, pady=(8, 2))

        body = tk.Frame(self, bg=th["win"])
        body.pack(fill="both", expand=True, padx=10, pady=2)
        self.listbox = tk.Listbox(body, bg=th["card"], fg=th["fg"],
                                  selectbackground=th["accent"],
                                  selectforeground=th["on_accent"],
                                  relief="flat", highlightthickness=0,
                                  font=(FONT_CN, 9), activestyle="none")
        sb = tk.Scrollbar(body, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        foot = tk.Frame(self, bg=th["win"])
        foot.pack(fill="x", padx=10, pady=8)
        self.summary = tk.Label(foot, text="", font=(FONT_CN, 8),
                                fg=th["sub"], bg=th["win"], anchor="w")
        self.summary.pack(side="left", fill="x", expand=True)
        for txt, cmd in ((" 导入文件… ", self.import_files),
                         (" 删除选中 ", self.delete_selected),
                         (" 清空库 ", self.clear_all)):
            b = ttk_button(foot, txt, cmd, th)
            b.pack(side="right", padx=2)

    def refresh(self):
        """重拉文档列表（主线程）。"""
        self._rows = []
        try:
            docs = self._store().list_documents()
        except Exception as exc:
            self._toast("读取文档库失败", str(exc))
            docs = []
        self.listbox.delete(0, "end")
        for d in docs:
            name = (d.get("name") or "未命名")[:44]
            line = (f"{name}  · {d['chunk_count']}段·{d['char_len']}字"
                    f" · 向量{d['vec_n']}/{d['chunk_count']}")
            self.listbox.insert("end", line)
            self._rows.append(d)
        n = len(docs)
        self.summary.configure(
            text=f"{n} 篇文档" if n else "库为空：先点『导入文件…』")

    # ------------------------------------------------------------ 动作
    def import_files(self):
        """导入 .txt/.md（同步，秒回）与 .pdf（后台线程逐页提取 + 进度）。"""
        paths = filedialog.askopenfilenames(
            parent=self, title="选择要导入的学习资料",
            filetypes=[("文本/笔记/PDF", "*.txt *.md *.pdf"),
                       ("所有文件", "*.*")])
        if not paths:
            return
        pdfs = [p for p in paths if p.lower().endswith(".pdf")]
        texts = [p for p in paths if not p.lower().endswith(".pdf")]
        added = skipped = failed = 0
        notes = []
        if texts:
            added, skipped, failed, notes = self._import_text_files(texts)
        pdf_jobs = 0
        if pdfs:
            pdf_jobs = self._import_pdfs_async(pdfs)
        if added or skipped or failed:
            self.refresh()
            msg = f"新增 {added}，跳过重复 {skipped}"
            if failed:
                msg += f"，失败 {failed}"
            if pdf_jobs:
                msg += f"；{pdf_jobs} 个 PDF 后台解析中…"
            self._toast("导入完成", msg)
        elif pdf_jobs:
            self._toast("PDF 导入", f"{pdf_jobs} 个 PDF 后台解析中…")
        if notes:
            self._toast("部分文件失败", "\n".join(notes[:5]))

    def _import_text_files(self, paths):
        """txt/md 同步导入（原 v1.7.0 逻辑），返回 (added, skipped, failed, notes)。"""
        store = self._store()
        added = skipped = failed = 0
        notes = []
        for p in paths:
            try:
                with open(p, "rb") as f:
                    raw = f.read()
                text = raw.decode("utf-8-sig", errors="replace")
            except Exception as exc:
                failed += 1
                notes.append(f"{os.path.basename(p)}：读取失败 {exc}")
                continue
            if not text.strip():
                skipped += 1
                continue
            try:
                r = store.add_document(os.path.basename(p), text,
                                       source="本地导入")
            except Exception as exc:
                failed += 1
                notes.append(f"{os.path.basename(p)}：{exc}")
                continue
            if r.get("added"):
                added += 1
            else:
                skipped += 1
        return added, skipped, failed, notes

    # ---------------- PDF：后台线程逐页提取，主线程入库 ----------------
    def _import_pdfs_async(self, paths):
        """PDF 走后台线程（pypdf 提取），经 _pdfq 回主线程入库。返回任务数。"""
        if not self._pdf_ready():
            return 0
        self._pdf_pending = getattr(self, "_pdf_pending", 0) + len(paths)
        self._pdf_stats = getattr(self, "_pdf_stats",
                                  {"added": 0, "skipped": 0, "failed": 0,
                                   "warns": []})
        if not hasattr(self, "_pdfq"):
            self._pdfq = queue.Queue()
            self._poll_pdf()
        for p in paths:
            name = os.path.basename(p)
            t = threading.Thread(target=self._pdf_worker,
                                 args=(name, p, self._pdfq), daemon=True)
            t.start()
        self.summary.configure(text=f"PDF 后台解析中（{self._pdf_pending} 个）…")
        return len(paths)

    def _pdf_ready(self):
        """pypdf 缺失时给指引（唯一第三方依赖例外，打包版内置）。"""
        from rag import pdftext
        if pdftext.HAVE_PYPDF:
            return True
        self._toast("PDF 支持不可用", pdftext._INSTALL_HINT)
        return False

    @staticmethod
    def _pdf_worker(name, path, q):
        """后台线程：逐页提取，进度/结果/异常全走队列，不碰 Tk/sqlite。"""
        from rag import pdftext
        try:
            pages = pdftext.extract_pdf(
                path, on_progress=lambda i, n, note:
                    q.put(("progress", (name, i, n, note))))
            q.put(("done", (name, pages)))
        except Exception as exc:
            q.put(("error", (name, str(exc))))

    def _poll_pdf(self):
        """主线程轮询 _pdfq：刷进度 / 入库 / 汇总 toast。

        窗口已关（_pdf_closed）就停止轮询——after 定时器挂在 Tcl 解释器上，
        destroy 后仍会触发，不守卫会对已销毁 widget 抛 TclError（v1.6.4 同款坑）。
        """
        if getattr(self, "_pdf_closed", False):
            return
        if not hasattr(self, "_pdfq"):
            return
        try:
            while True:
                kind, payload = self._pdfq.get_nowait()
                if kind == "progress":
                    name, i, n, note = payload
                    self.summary.configure(
                        text=f"解析 {name}… 第 {i}/{n} 页"
                             + (f"（{note}）" if note else ""))
                elif kind == "done":
                    self._pdf_ingest(*payload)
                elif kind == "error":
                    self._pdf_stats["failed"] += 1
                    self._pdf_stats["warns"].append(f"{payload[0]}：{payload[1]}")
                    self._pdf_pending -= 1
                    self._pdf_flush()
        except queue.Empty:
            pass
        if getattr(self, "_pdf_pending", 0) > 0 or not self._pdfq.empty():
            self.after(60, self._poll_pdf)
        else:
            self.refresh()          # 恢复正常摘要行（N 篇文档 / 库为空）

    def _pdf_ingest(self, name, pages):
        """主线程：markdown 拼装 + 质量体检 + 入库（sqlite 留在主线程）。"""
        from rag import pdftext
        q = pdftext.text_quality(name, pages)
        md = pdftext.to_markdown(name, pages)
        if not md.strip():
            self._pdf_stats["skipped"] += 1
        else:
            try:
                r = self._store().add_document(name, md, source="本地导入")
                if r.get("added"):
                    self._pdf_stats["added"] += 1
                else:
                    self._pdf_stats["skipped"] += 1
            except Exception as exc:
                self._pdf_stats["failed"] += 1
                self._pdf_stats["warns"].append(f"{name}：入库失败 {exc}")
        if q["verdict"] != "good":
            self._pdf_stats["warns"].append(f"{name}：{q['reason']}")
        self._pdf_pending -= 1
        self.refresh()
        self._pdf_flush()

    def _pdf_flush(self):
        """全部 PDF 处理完 → 汇总 toast（含质量体检提示）。"""
        if self._pdf_pending > 0:
            return
        st = self._pdf_stats
        msg = f"PDF 完成：新增 {st['added']}，跳过重复 {st['skipped']}"
        if st["failed"]:
            msg += f"，失败 {st['failed']}"
        self._toast("PDF 导入完成", msg)
        if st["warns"]:
            self._toast("PDF 质量提示", "\n".join(st["warns"][:5]))
        self._pdf_stats = {"added": 0, "skipped": 0, "failed": 0, "warns": []}

    def delete_selected(self):
        sel = self.listbox.curselection()
        if not sel:
            self._toast("删除", "先选中一篇文档")
            return
        d = self._rows[sel[0]]
        if not messagebox.askyesno(
                "确认删除",
                f"删除「{d['name']}」？\n其 {d['chunk_count']} 个分块将一并移除。",
                parent=self):
            return
        try:
            self._store().delete_document(d["id"])
        except Exception as exc:
            self._toast("删除失败", str(exc))
            return
        self.refresh()

    def clear_all(self):
        if not self._rows:
            return
        if not messagebox.askyesno(
                "清空文档库",
                "将删除全部文档与分块（不可恢复）。确定清空？",
                parent=self):
            return
        store = self._store()
        try:
            for d in self._rows:
                store.delete_document(d["id"])
        except Exception as exc:
            self._toast("清空失败", str(exc))
        self.refresh()

    def _toast(self, title, body):
        q = getattr(self.app, "q", None)
        if q is not None:
            q.put(("toast", (title, body)))

    def close(self):
        self._pdf_closed = True          # 停 _poll_pdf 轮询（后台线程自然收尾）
        if getattr(self.app, "kb_mgr", None) is self:
            self.app.kb_mgr = None
        try:
            self.destroy()
        except Exception:
            pass


def ttk_button(parent, text, command, th):
    """文档库用的小按钮（避开 ui.Settings 的 ttk 主题依赖，用 tk 原生画）。"""
    b = tk.Label(parent, text=text, font=(FONT_CN, 9),
                 fg=th["fg"], bg=th["hover"],
                 cursor="hand2", padx=8, pady=3)
    b.bind("<Button-1>", lambda e=None: command())
    b.bind("<Enter>",
           lambda e=None: b.configure(bg=th["accent"], fg=th["on_accent"]))
    b.bind("<Leave>",
           lambda e=None: b.configure(bg=th["hover"], fg=th["fg"]))
    return b
