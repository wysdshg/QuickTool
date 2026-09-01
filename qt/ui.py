"""界面层：光标跟随悬浮窗 + 设置窗口。全部用 tkinter 标准库实现。"""
import base64
import math
import os
import struct
import time
import tkinter as tk
import zlib
from tkinter import font as tkfont, ttk

from . import winapi as wa
from .engines import ENGINES, engine_display_names

FONT_CN = "Microsoft YaHei UI"
FONT_MONO = "Consolas"

THEMES = {
    "dark": {"card": "#232936", "border": "#3a4256", "fg": "#e9edf5",
             "sub": "#93a0b5", "accent": "#5aa9ff", "hover": "#2e3648",
             "danger": "#ff6b6b"},
    "light": {"card": "#ffffff", "border": "#dfe5ee", "fg": "#1f2430",
              "sub": "#6b7688", "accent": "#2563eb", "hover": "#f2f5fa",
              "danger": "#e0483c"},
}


# ================================================================== 悬浮窗
class Popup:
    W_MIN, W_PAD = 280, 36

    def __init__(self, app, text, result, engine, ok=True, loading=False):
        self.app = app
        self.ok = ok
        self.closed = False
        self._close_job = None

        p = app.cfg.get("popup")
        self.theme_name = p.get("theme", "dark")
        th = THEMES.get(self.theme_name, THEMES["dark"])
        self.th = th
        self.max_w = int(p.get("max_width", 520))
        self.alpha = float(p.get("alpha", 0.97))
        self.auto_hide = int(p.get("auto_hide_ms", 0) or 0)

        self.res_font = tkfont.Font(family=FONT_CN, size=int(p.get("font_size", 13)))
        self.src_font = tkfont.Font(family=FONT_CN, size=10)
        self.btn_font = tkfont.Font(family=FONT_CN, size=9)

        win = tk.Toplevel(app.root)
        self.win = win
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-alpha", self.alpha)
        win.configure(bg=th["border"], cursor="arrow")
        outer = tk.Frame(win, bg=th["border"], padx=1, pady=1)
        outer.pack(fill="both", expand=True)
        self.card = tk.Frame(outer, bg=th["card"])
        self.card.pack(fill="both", expand=True)

        self.body = tk.Frame(self.card, bg=th["card"])
        self.body.pack(fill="both", expand=True, padx=14, pady=12)

        self.text, self.result, self.engine = text, result, engine
        self._render(loading)
        self._place()
        self._bind()

    # ------------------------------------------------------------ 内容
    def _render(self, loading):
        for child in self.body.winfo_children():
            child.destroy()
        th, bg = self.th, self.th["card"]

        if self.text:
            src = tk.Label(self.body, text=self._ellipsis(self.text, 240),
                           font=self.src_font, fg=th["sub"], bg=bg,
                           justify="left", wraplength=self._wrap(), anchor="w")
            src.pack(fill="x", anchor="w")
            sep = tk.Frame(self.body, bg=th["border"], height=1)
            sep.pack(fill="x", pady=8)

        color = th["fg"] if self.ok else th["danger"]
        font = self.res_font if not loading else self.src_font
        self.lbl_result = tk.Label(self.body, text=self.result or "…", font=font,
                                   fg=color, bg=bg, justify="left",
                                   wraplength=self._wrap(), anchor="w")
        self.lbl_result.pack(fill="x", anchor="w")

        bar = tk.Frame(self.body, bg=bg)
        bar.pack(fill="x", pady=(10, 0))
        tk.Label(bar, text=self.engine or "", font=self.btn_font,
                 fg=th["sub"], bg=bg).pack(side="left")
        for label, cmd in (("关闭", self.close), ("复制", self.copy)):
            b = tk.Label(bar, text=label, font=self.btn_font, fg=th["accent"],
                         bg=bg, padx=8, pady=2, cursor="hand2")
            b.pack(side="right")
            # 具名函数而非 lambda：Tk 回调报错时栈里能看到名字，好排查。
            # e 给默认值是防御性的——极端情况下（widget 正在销毁）Tk 可能
            # 不带 event 参数调用回调，否则会抛 TypeError 打断整个回调链。
            def on_click(e=None, c=cmd):
                c()

            def on_enter(e=None, w=b):
                try:
                    w.configure(bg=th["hover"])
                except Exception:
                    pass

            def on_leave(e=None, w=b):
                try:
                    w.configure(bg=bg)
                except Exception:
                    pass

            b.bind("<Button-1>", on_click)
            b.bind("<Enter>", on_enter)
            b.bind("<Leave>", on_leave)

    def update(self, text, result, engine, ok=True):
        """翻译完成后原地刷新，避免闪烁。"""
        self.text, self.result, self.engine, self.ok = text, result, engine, ok
        self._render(False)
        self._place()
        if self.auto_hide > 0:
            self._schedule_auto_hide()

    # ------------------------------------------------------------ 尺寸与定位
    def _wrap(self):
        return max(120, self.max_w - self.W_PAD)

    def _width(self):
        sample = (self.result or "")[:150]
        w = self.res_font.measure(sample) + 30
        return max(self.W_MIN, min(self.max_w, w))

    @staticmethod
    def _ellipsis(s, n):
        s = s.replace("\n", " ")
        return s if len(s) <= n else s[:n] + " …"

    def _place(self):
        w = self._width()
        self.win.geometry(f"{w}x10+0+0")
        self.win.update_idletasks()
        h = self.win.winfo_reqheight()

        left, top, right, bottom = wa.get_work_area()
        if self.app.cfg.get("popup.follow_cursor", True):
            cx, cy = wa.get_cursor_pos()
            x, y = cx + 16, cy + 26
            if y + h > bottom:
                y = max(top, cy - h - 18)
        else:
            x, y = (left + right - w) // 2, (top + bottom - h) // 2
        x = max(left + 4, min(x, right - w - 4))
        y = max(top + 4, min(y, bottom - h - 4))
        self.win.geometry(f"{w}x{h}+{int(x)}+{int(y)}")

    # ------------------------------------------------------------ 交互
    def _bind(self):
        w = self.win
        w.bind("<Escape>", lambda e: self.close())
        w.bind("<FocusIn>", lambda e: self._cancel_close())
        w.bind("<FocusOut>", lambda e: self._schedule_close(260))
        for child in (w, self.card, self.body):
            child.bind("<ButtonPress-1>", self._drag_start)
            child.bind("<B1-Motion>", self._drag_move)
        w.after(30, lambda: wa.set_tool_window(w.winfo_id(), True))
        w.focus_force()

    def _drag_start(self, event):
        self._dx, self._dy = event.x, event.y

    def _drag_move(self, event):
        try:
            self.win.geometry(f"+{self.win.winfo_x() + event.x - self._dx}"
                              f"+{self.win.winfo_y() + event.y - self._dy}")
        except Exception:
            pass

    def _schedule_auto_hide(self):
        self._cancel_close()
        self._close_job = self.win.after(self.auto_hide, self.close)

    def _schedule_close(self, delay):
        self._cancel_close()
        self._close_job = self.win.after(delay, self.close)

    def _cancel_close(self):
        if self._close_job is not None:
            try:
                self.win.after_cancel(self._close_job)
            except Exception:
                pass
            self._close_job = None

    def copy(self):
        text = self.result if self.ok else self.text
        if text:
            wa.set_clipboard_text(text)
        self.close()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self._cancel_close()
        try:
            self.win.destroy()
        except Exception:
            pass
        if getattr(self.app, "popup", None) is self:
            self.app.popup = None
        self.app.popup_open = False


# ================================================================== 迷你按钮
class MiniButton:
    """『译』字迷你按钮：拖选文字后出现在鼠标旁，点击即翻译。

    目标就是『小 + 不挡视野』：26x26 圆形、半透明、置顶无边框，
    鼠标一进去就取消自动隐藏，点一下出译文、再点空白或等 2.2s 就消失。
    """

    SIZE = 26
    HIDE_MS = 2200       # 出现后自动消失的时间（鼠标进入即暂停计时）
    FONT_SIZE = 11

    def __init__(self, app, x, y, text):
        self.app = app
        self.text = text
        self.closed = False
        self._hide_job = None
        th = THEMES.get(app.cfg.get("popup.theme", "dark"), THEMES["dark"])
        self.th = th

        win = tk.Toplevel(app.root)
        self.win = win
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-alpha", 0.92)
        self._draw(th)
        self._place(x, y)
        self._bind()
        self._schedule_hide(self.HIDE_MS)
        win.after(30, lambda: wa.set_tool_window(win.winfo_id(), True))

    def _draw(self, th):
        s = self.SIZE
        cv = tk.Canvas(self.win, width=s, height=s, highlightthickness=0,
                       bg=th["card"], cursor="hand2")
        cv.pack()
        cv.create_oval(1, 1, s - 1, s - 1, fill=th["card"], outline=th["border"],
                       width=1, tags="bg")
        cv.create_text(s // 2, s // 2, text="译", fill=th["accent"], tags="txt",
                       font=("Microsoft YaHei UI", self.FONT_SIZE, "bold"))
        self.cv = cv

    def _place(self, x, y):
        s = self.SIZE
        left, top, right, bottom = wa.get_work_area()
        px = min(x + 10, right - s - 6)
        py = y + 16
        if py + s > bottom:
            py = max(top + 6, y - s - 10)
        px = max(left + 6, px)
        self.win.geometry(f"{s}x{s}+{int(px)}+{int(py)}")
        self.win.update_idletasks()

    def _bind(self):
        self.win.bind("<Button-1>", lambda e: self._click())
        self.win.bind("<Enter>", lambda e: self._hover(True))
        self.win.bind("<Leave>",
                      lambda e: (self._hover(False), self._schedule_hide(1200)))
        self.cv.bind("<Button-1>", lambda e: self._click())

    def _hover(self, on):
        try:
            self.cv.itemconfigure("bg",
                                  fill=self.th["hover"] if on else self.th["card"])
        except Exception:
            pass

    def _click(self):
        text = self.text
        self.close()
        self.app.mini_translate(text)

    def get_rect(self):
        """屏幕坐标矩形 (left, top, right, bottom)，供鼠标钩子忽略自身区域。"""
        if self.closed:
            return None
        x, y = self.win.winfo_x(), self.win.winfo_y()
        return (x - 2, y - 2, x + self.SIZE + 2, y + self.SIZE + 2)

    def _schedule_hide(self, delay):
        self._cancel_hide()
        self._hide_job = self.win.after(delay, self.close)

    def _cancel_hide(self):
        if self._hide_job is not None:
            try:
                self.win.after_cancel(self._hide_job)
            except Exception:
                pass
            self._hide_job = None

    def close(self):
        if self.closed:
            return
        self.closed = True
        self._cancel_hide()
        try:
            self.win.destroy()
        except Exception:
            pass
        if getattr(self.app, "mini_btn", None) is self:
            self.app.mini_btn = None


# ================================================================== 截图选区
class RegionSelector(tk.Toplevel):
    """全屏半透明遮罩 + 拖拽框选截图区域（截图翻译第一步）。

    交互借鉴 STranslate / Snipaste：按下左键拖出矩形，松开确认，
    Esc / 右键 / 点太小的矩形 = 取消。选完回调 on_done(x, y, w, h)。

    坑（同 Settings）：root 是 withdrawn 的，绝不能调 transient()——
    会继承 withdrawn 状态导致遮罩永远不可见。
    """

    MIN_SIZE = 10          # 小于 10px 视为误触，直接取消

    def __init__(self, app, on_done, title="拖拽框选要翻译的屏幕区域 · Esc 取消"):
        super().__init__()
        self.app = app
        self.on_done = on_done
        self.done = False
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.overrideredirect(True)
        self.geometry(f"{sw}x{sh}+0+0")
        self.configure(bg="black")
        self.attributes("-alpha", 0.32)
        self.attributes("-topmost", True)
        cv = tk.Canvas(self, bg="black", highlightthickness=0,
                       cursor="crosshair")
        cv.pack(fill="both", expand=True)
        cv.create_text(sw // 2, 46,
                       text=title,
                       fill="#e8f0ff", font=("Microsoft YaHei UI", 12))
        self.cv = cv
        self._rect = None
        self._sx = self._sy = 0
        self._pressed = False          # 防御：只认 press→release 完整配对
        cv.bind("<ButtonPress-1>", self._press)
        cv.bind("<B1-Motion>", self._drag)
        cv.bind("<ButtonRelease-1>", self._release)
        self.bind("<Escape>", lambda e: self.close())
        self.bind("<Button-3>", lambda e: self.close())
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.deiconify()
        self.lift()
        self.focus_force()

    def _press(self, ev):
        self._sx, self._sy = ev.x, ev.y
        self._pressed = True
        if self._rect:
            self.cv.delete(self._rect)

    def _drag(self, ev):
        if self._rect:
            self.cv.delete(self._rect)
        self._rect = self.cv.create_rectangle(
            self._sx, self._sy, ev.x, ev.y,
            outline="#22c55e", width=2)

    def _release(self, ev):
        # 孤儿 release（无 press 配对）直接忽略：遮罩弹出瞬间若鼠标残留
        # 按下状态，松手会带 _sx/_sy=0，从屏幕左上角(0,0)到鼠标位置生成
        # 一个巨大的"随机"选区直接截屏（v1.5.2 实测：1124x110/968x134
        # 两个左上角矩形，用户没动鼠标却完成截图）。
        if not self._pressed:
            return
        self._pressed = False
        x0, x1 = sorted((self._sx, ev.x))
        y0, y1 = sorted((self._sy, ev.y))
        w, h = x1 - x0, y1 - y0
        if w < self.MIN_SIZE or h < self.MIN_SIZE:
            self.close()                     # 误触，取消
            return
        self.close(completed=True)           # 完成路径：_selecting 由回调清
        try:
            self.on_done(x0, y0, w, h)
        except Exception:
            pass

    def close(self, completed=False):
        # completed=True 表示选区已交付给回调（on_done），由回调负责清
        # _selecting；否则（Esc/右键/误触取消）立即清——不然下一次拖拽
        # 会被钩子当成划词（v1.5.3 竞态：选区完成瞬间 _handle_drag_end
        # 误判拖拽为划词，CaptureWorker 还原剪贴板会把刚写入的 CF_DIB
        # 覆盖掉，用户截图后 Ctrl+V 粘到旧内容）。
        if self.done:
            return
        self.done = True
        try:
            self.destroy()
        except Exception:
            pass
        if getattr(self.app, "ocr_selector", None) is self:
            self.app.ocr_selector = None
        if not completed:
            setattr(self.app, "_selecting", False)


# ================================================================== 截图对照窗
class PinWindow:
    """『截图对照』小窗：把截下的屏幕区域固定置顶显示，方便长网页回头对照。

    用法：热键触发 -> RegionSelector 框选 -> grab_screen_bmp 抓屏 ->
    PinWindow 显示。无边框置顶，可整窗拖动；鼠标滚轮缩放（1x ~ 8x 缩小，
    保持 1:1 以上不放大——对照真实尺寸最实用）；Esc / 右键 / 右上角 ✕ 关闭。
    用完即关，不创建窗口时零内存开销。

    多窗口（v1.5.1）：app.pin_wins 是列表，可同时存在多个对照窗，各自
    独立关闭。层级按「点谁谁在前」：点击任意窗口会 lift + focus 置前。
    新窗口初始位置相对鼠标级联偏移（28px × 序号），避免完全盖住旧窗口。

    tk.PhotoImage 只认 GIF/PPM/PNG（不认 BMP），而抓屏给的是 BMP 字节，
    所以先用标准库 zlib 手写 PNG 编码（8bit RGB 无 alpha，IDAT 压缩），
    base64 后喂给 PhotoImage —— 全程零第三方依赖。
    """

    ZOOM_MIN, ZOOM_MAX = 1, 8          # subsample 缩小倍数范围
    CASCADE = 28                       # 多窗口级联偏移步长（px）

    def __init__(self, app, bmp_bytes, index=1):
        self.app = app
        self.index = index             # 序号（工具条显示「对照 N」）
        self.closed = False
        w, h = _bmp_size(bmp_bytes)
        self._orig_w, self._orig_h = w, h
        self._bmp = bmp_bytes                       # 原始 BMP（「存 PNG」用 1:1 原图）
        self._img_orig = _bmp_to_photo(bmp_bytes)   # 1:1 原图（缩放基准）
        self._factor = 1

        win = tk.Toplevel(app.root)
        self.win = win
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-alpha", 0.98)
        win.configure(bg=THEMES["dark"]["border"], cursor="arrow")

        outer = tk.Frame(win, bg=THEMES["dark"]["border"])
        outer.pack(fill="both", expand=True)
        self._build_bar(outer)
        self.cv = tk.Canvas(outer, bg="#1c212b", highlightthickness=0,
                            cursor="fleur")
        self.cv.pack(fill="both", expand=True)

        self._fit_size()              # 初始自适应屏幕（最大 85% 工作区）
        self._render()
        self._place()
        self._bind()
        win.after(30, lambda: wa.set_tool_window(win.winfo_id(), True))
        win.focus_force()

    # ------------------------------------------------------------ 构建
    def _build_bar(self, parent):
        bar = tk.Frame(parent, bg=THEMES["dark"]["card"])
        bar.pack(fill="x")
        self.lbl_title = tk.Label(
            bar, text=f"对照 {self.index} · 滚轮缩放 · Esc 关闭",
            font=("Microsoft YaHei UI", 9), fg=THEMES["dark"]["sub"],
            bg=THEMES["dark"]["card"])
        self.lbl_title.pack(side="left", padx=8, pady=3)
        self.lbl_zoom = tk.Label(bar, text="100%", font=("Consolas", 9),
                                 fg=THEMES["dark"]["accent"],
                                 bg=THEMES["dark"]["card"])
        self.lbl_zoom.pack(side="right", padx=6)
        b_save = tk.Label(bar, text=" 存 PNG ", font=("Microsoft YaHei UI", 9),
                          fg=THEMES["dark"]["fg"], bg=THEMES["dark"]["card"],
                          cursor="hand2", padx=6)
        b_save.pack(side="right")
        b_save.bind("<Button-1>", lambda e: self._save_png())
        b = tk.Label(bar, text=" ✕ ", font=("Microsoft YaHei UI", 10),
                     fg=THEMES["dark"]["fg"], bg=THEMES["dark"]["card"],
                     cursor="hand2", padx=6)
        b.pack(side="right")
        b.bind("<Button-1>", lambda e: self.close())

    def _fit_size(self):
        """按工作区 85% 上限取初始缩放倍数（只缩小，不放大）。"""
        left, top, right, bottom = wa.get_work_area()
        max_w, max_h = (right - left) * 0.85, (bottom - top) * 0.85
        f = 1
        while self._orig_w // f > max_w or self._orig_h // f > max_h:
            f += 1
        self._factor = min(f, self.ZOOM_MAX)

    def _render(self):
        img = self._img_orig
        f = self._factor
        if f > 1:
            img = img.subsample(f, f)
        self._photo = img
        w, h = img.width(), img.height()
        self.cv.configure(width=w, height=h, scrollregion=(0, 0, w, h))
        self.cv.delete("all")
        self.cv.create_image(w // 2, h // 2, image=img)
        self.lbl_zoom.configure(text=f"{100 // f}%")

    def _save_png(self, dir=None):
        """把截图原图（1:1，非缩放态）存为 PNG；成功返回路径，失败返回 None。

        触发：工具条「存 PNG」按钮 / 快捷键 Ctrl+S（主动按键 = 明确保存意图，
        同系统截屏的"点通知才存"逻辑；剪贴板则始终自动放，无需确认）。
        """
        try:
            d = dir or pin_save_dir()
            os.makedirs(d, exist_ok=True)
            name = time.strftime("QuickTool_%Y%m%d_%H%M%S.png")
            path = os.path.join(d, name)
            with open(path, "wb") as f:
                f.write(_bmp_to_png(self._bmp))
        except Exception as exc:
            self._toast("保存失败", f"{exc}")
            return None
        self._toast("截图已保存", f"{path}")
        return path

    def _toast(self, title, text):
        """非阻塞提示（模态 messagebox 会卡死主线程，一律走队列 toast）。"""
        q = getattr(self.app, "q", None)
        if q is not None:
            q.put(("toast", (title, text)))

    def _place(self):
        w = self.cv.winfo_reqwidth() + 2
        bar_h = 26
        h = self.cv.winfo_reqheight() + 2 + bar_h
        cx, cy = wa.get_cursor_pos()
        left, top, right, bottom = wa.get_work_area()
        # 级联偏移：序号越大越往右下错开，避免新窗口完全盖住旧窗口
        off = (self.index - 1) * self.CASCADE
        x = min(max(cx - w // 2 + off, left + 4), right - w - 4)
        y = min(max(cy - h // 2 + off, top + 4), bottom - h - 4)
        self.win.geometry(f"{w}x{h}+{int(x)}+{int(y)}")

    # ------------------------------------------------------------ 交互
    def _bind(self):
        win = self.win
        win.bind("<Escape>", lambda e: self.close())
        win.bind("<Button-3>", lambda e: self.close())
        win.bind("<Control-s>", lambda e: self._save_png())
        win.bind("<Control-S>", lambda e: self._save_png())
        # 整窗拖动（含工具条与画布）；按下即置前（多窗口「点谁谁在前」）
        for wdg in (win, self.cv):
            wdg.bind("<ButtonPress-1>", self._drag_start)
            wdg.bind("<B1-Motion>", self._drag_move)

        def _raise(e=None):
            try:
                self.win.lift()
                self.win.focus_force()
            except Exception:
                pass

        win.bind("<ButtonPress-1>", _raise, add="+")
        self.cv.bind("<ButtonPress-1>", _raise, add="+")
        # 滚轮缩放
        self.cv.bind("<MouseWheel>", self._zoom)
        win.bind("<MouseWheel>", self._zoom)
        self.cv.bind("<Button-4>", self._zoom)   # Linux 上滚
        self.cv.bind("<Button-5>", self._zoom)   # Linux 下滚

    def _drag_start(self, event):
        self._dx, self._dy = event.x, event.y

    def _drag_move(self, event):
        try:
            self.win.geometry(f"+{self.win.winfo_x() + event.x - self._dx}"
                              f"+{self.win.winfo_y() + event.y - self._dy}")
        except Exception:
            pass

    def _zoom(self, event):
        if getattr(event, "num", 0) == 5:          # Linux 下滚 = 缩小
            delta = 1
        elif getattr(event, "num", 0) == 4:        # Linux 上滚 = 放大
            delta = -1
        else:
            delta = -1 if event.delta > 0 else 1   # Windows: 正 = 放大
        f = self._factor + delta
        if not (self.ZOOM_MIN <= f <= self.ZOOM_MAX):
            return
        self._factor = f
        self._render()
        self._place()
        return "break"

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.win.destroy()
        except Exception:
            pass
        # 从 app.pin_wins 列表移除自己（多窗口各自独立关闭）
        wins = getattr(self.app, "pin_wins", None)
        if wins:
            try:
                if self in wins:
                    wins.remove(self)
            except Exception:
                pass


# ---------------------------------------------------------------- BMP -> PNG
def _bmp_size(data):
    """从 BMP 文件头取宽高（像素）。"""
    if data[:2] != b"BM":
        raise ValueError("不是 BMP 数据")
    w = struct.unpack_from("<i", data, 18)[0]
    h = struct.unpack_from("<i", data, 22)[0]
    if w <= 0 or h <= 0:
        raise ValueError(f"BMP 尺寸非法：{w}x{h}")
    return w, h


def _bmp_to_png(data):
    """把 32bpp 自底向上 BMP 转成 8bit RGB PNG（标准库 zlib，零依赖）。

    grab_screen_bmp 输出的是 32bpp BI_RGB、自底向上行序（首行在文件尾）。
    PNG 需要自顶向下 + filter=0 的扫描线。逐像素取 BGR 前三字节转 RGB。
    """
    w, h = _bmp_size(data)
    off = struct.unpack_from("<I", data, 10)[0]    # 像素数据偏移
    stride = w * 4
    scanlines = bytearray()
    for y in range(h):                              # 自底向上 -> 自顶向下
        row = data[off + (h - 1 - y) * stride:
                   off + (h - y) * stride]
        scanlines.append(0)                         # filter type 0
        for i in range(w):
            scanlines += row[i * 4:i * 4 + 3]       # BGR -> RGB
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)   # 8bit, truecolor RGB

    def chunk(tag, payload):
        body = tag + payload
        return struct.pack(">I", len(payload)) + body \
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(scanlines), 6))
            + chunk(b"IEND", b""))


def _bmp_to_photo(data):
    """BMP 字节 -> tk.PhotoImage（PNG 编码 + base64，Tk 8.6 原生支持 PNG）。"""
    return tk.PhotoImage(data=base64.b64encode(_bmp_to_png(data)))


def pin_save_dir():
    """截图文件默认保存目录：%USERPROFILE%\Pictures\QuickTool\（同系统截屏惯例）。"""
    base = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return os.path.join(base, "Pictures", "QuickTool")


# ================================================================== 设置
class Settings(tk.Toplevel):
    LANGS = [("zh-CN", "简体中文"), ("zh-TW", "繁体中文（台湾）"),
             ("en", "英语"), ("ja", "日语"), ("ko", "韩语"),
             ("fr", "法语"), ("de", "德语"), ("es", "西班牙语"), ("ru", "俄语")]
    KEY_MOD = {"Control": "Ctrl", "Alt": "Alt", "Shift": "Shift", "Meta": "Win"}

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.cfg = app.cfg
        self.title("QuickTool 设置")
        self.geometry("560x620")
        self.resizable(True, True)
        self.configure(bg="#f5f7fa")
        # 注意：不要对隐藏的 root 调 transient()！实测 Tk 会让子窗口继承父窗口
        # 的 withdrawn 状态，且立即 deiconify 也无效，导致设置窗口创建后不可见、
        # 用户以为"点设置没反应"。独立 Toplevel 正常显示在任务栏，反而更直观。
        self.deiconify()
        self.lift()
        self.focus_force()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build()

    def _on_close(self):
        """关闭设置窗口时清掉 app.settings 引用，否则重开会撞上已销毁的 Toplevel。"""
        if getattr(self.app, "settings", None) is self:
            self.app.settings = None
        self.destroy()

    # ------------------------------------------------------------ 构建
    def _build(self):
        style = ttk.Style(self)
        for f in ("Microsoft YaHei UI", "Microsoft YaHei"):
            try:
                tkfont.Font(family=f, size=9).actual()
                F = f
                break
            except Exception:
                F = "System"
        style.configure("TLabel", font=(F, 9))
        style.configure("TButton", font=(F, 9))
        style.configure("TLabelframe.Label", font=(F, 9, "bold"))

        wrap = tk.Frame(self, bg="#f5f7fa")
        wrap.pack(fill="both", expand=True)
        canvas = tk.Canvas(wrap, bg="#f5f7fa", highlightthickness=0)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(canvas, bg="#f5f7fa")
        canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind_all("<MouseWheel>",
                        lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))

        self._section_engine(inner)
        self._section_hotkey(inner)
        self._section_llm(inner)
        self._section_ui(inner)
        self._section_footer(inner)

    def _frame(self, parent, title):
        lf = ttk.LabelFrame(parent, text=" " + title + " ", padding=10)
        lf.pack(fill="x", padx=12, pady=6)
        return lf

    def _row(self, parent, label, widget, hint=""):
        r = tk.Frame(parent, bg=parent.cget("bg") if "bg" in parent.keys() else "#f5f7fa")
        r.pack(fill="x", pady=3)
        tk.Label(r, text=label, width=12, anchor="w", bg="#f5f7fa",
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        widget.pack(side="left", fill="x", expand=True)
        if hint:
            tk.Label(r, text=hint, fg="#8a94a6", bg="#f5f7fa",
                     font=("Microsoft YaHei UI", 8)).pack(side="left", padx=6)

    # ------------------------------------------------------------ 引擎
    def _section_engine(self, root):
        f = self._frame(root, "翻译引擎")
        names = [(n, lab) for n, lab, _ in engine_display_names()]
        self.engine_var = tk.StringVar(value=self.cfg.get("engine"))
        self._row(f, "主引擎", ttk.Combobox(
            f, textvariable=self.engine_var, values=[n for n, _ in names],
            state="readonly", width=14))
        tk.Label(f, text=" · ".join(f"{n}={lab}" for n, lab in names),
                 fg="#8a94a6", bg="#f5f7fa", wraplength=500, justify="left",
                 font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(2, 6))

        self.fb_vars = {}
        fb = tk.Frame(f, bg="#f5f7fa")
        fb.pack(fill="x")
        tk.Label(fb, text="失败回退", width=12, anchor="w", bg="#f5f7fa",
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        chain = set(self.cfg.get("fallback_chain") or [])
        for name, _ in names:
            v = tk.BooleanVar(value=name in chain)
            self.fb_vars[name] = v
            ttk.Checkbutton(fb, text=name, variable=v).pack(side="left", padx=3)

        self.tgt_var = tk.StringVar(value=self.cfg.get("target_lang"))
        self._row(f, "目标语言", ttk.Combobox(
            f, textvariable=self.tgt_var, state="readonly", width=14,
            values=[c for c, _ in self.LANGS]))

        btns = tk.Frame(f, bg="#f5f7fa")
        btns.pack(fill="x", pady=(6, 0))
        ttk.Button(btns, text="测试当前引擎", command=self._test).pack(side="left")
        self.test_out = tk.Label(btns, text="", fg="#2563eb", bg="#f5f7fa",
                                 font=("Microsoft YaHei UI", 8), wraplength=380,
                                 justify="left")
        self.test_out.pack(side="left", padx=8)

    # ------------------------------------------------------------ 快捷键
    def _section_hotkey(self, root):
        f = self._frame(root, "快捷键（点输入框后直接按下组合键即可录制）")
        self.hk_vars = {}
        for key, label in (("hotkey_translate", "划词翻译"),
                           ("hotkey_ocr", "截图翻译"),
                           ("hotkey_pin", "截图对照"),
                           ("hotkey_settings", "打开设置"),
                           ("hotkey_quit", "退出程序")):
            var = tk.StringVar(value=self.cfg.get(key))
            self.hk_vars[key] = var
            e = ttk.Entry(f, textvariable=var, width=18)
            self._row(f, label, e)
            e.bind("<KeyPress>", lambda ev, v=var: self._capture(ev, v))
        tk.Label(f, text="注意：录制时全局热键也会同时触发一次，属正常现象。",
                 fg="#8a94a6", bg="#f5f7fa",
                 font=("Microsoft YaHei UI", 8)).pack(anchor="w")

    def _capture(self, event, var):
        st = event.state
        mods = []
        if st & 0x0004:
            mods.append("Ctrl")
        if st & 0x0008:
            mods.append("Alt")
        if st & 0x0001:
            mods.append("Shift")
        if st & 0x0040:
            mods.append("Win")
        k = event.keysym
        if k in ("Control_L", "Control_R", "Alt_L", "Alt_R", "Shift_L",
                 "Shift_R", "Win_L", "Win_R"):
            return "break"
        k = k.upper() if len(k) == 1 else k
        if k.lower().startswith("f") and k[1:].isdigit():
            k = k.lower()
        var.set("+".join(mods + [k]))
        return "break"

    # ------------------------------------------------------------ 大模型
    def _section_llm(self, root):
        f = self._frame(root, "大模型 API（OpenAI 兼容）")
        llm = self.cfg.get("llm")
        self.llm_vars = {}
        for key, label, width in (("base_url", "Base URL", 46),
                                  ("api_key", "API Key", 46),
                                  ("model", "模型名", 24)):
            v = tk.StringVar(value=llm.get(key, ""))
            self.llm_vars[key] = v
            self._row(f, label, ttk.Entry(f, textvariable=v, width=width,
                                          show="*" if key == "api_key" else ""))
        presets = tk.Frame(f, bg="#f5f7fa")
        presets.pack(fill="x", pady=2)
        tk.Label(presets, text="预设", width=12, anchor="w", bg="#f5f7fa",
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        for name, url, model in (
                ("DeepSeek", "https://api.deepseek.com/v1", "deepseek-chat"),
                ("硅基流动", "https://api.siliconflow.cn/v1", "Qwen/Qwen2.5-7B-Instruct"),
                ("智谱 GLM", "https://open.bigmodel.cn/api/paas/v4", "glm-4-flash"),
                ("本地 Ollama", "http://localhost:11434/v1", "qwen2.5:7b")):
            ttk.Button(presets, text=name, width=10,
                       command=lambda u=url, m=model: self._apply_preset(u, m)) \
                .pack(side="left", padx=2)

        df = self._frame(root, "其他")
        deepl = self.cfg.get("deepl")
        self.deepl_key = tk.StringVar(value=deepl.get("api_key", ""))
        self.deepl_free = tk.BooleanVar(value=deepl.get("free", True))
        self._row(df, "DeepL Key", ttk.Entry(df, textvariable=self.deepl_key,
                                             width=40, show="*"))
        ttk.Checkbutton(df, text="使用 DeepL Free 版（api-free.deepl.com）",
                        variable=self.deepl_free).pack(anchor="w", pady=2)
        self.autostart = tk.BooleanVar(value=bool(self.cfg.get("autostart")))
        ttk.Checkbutton(df, text="开机自动启动（写入 HKCU Run，无需管理员）",
                        variable=self.autostart).pack(anchor="w", pady=2)
        self.tray = tk.BooleanVar(value=bool(self.cfg.get("enable_tray")))
        ttk.Checkbutton(df, text="显示系统托盘图标",
                        variable=self.tray).pack(anchor="w", pady=2)
        self.ocr_lang_var = tk.StringVar(
            value=str(self.cfg.get("ocr_lang", "auto")))
        self._row(df, "OCR 语言",
                  ttk.Combobox(df, textvariable=self.ocr_lang_var, width=10,
                               values=["auto", "en-US", "en-GB", "zh-CN",
                                       "zh-TW", "ja", "ko", "fr", "de",
                                       "es", "ru"], state="readonly"),
                  "截图识别语言，auto=优先英语")

    def _apply_preset(self, url, model):
        self.llm_vars["base_url"].set(url)
        self.llm_vars["model"].set(model)

    # ------------------------------------------------------------ 界面
    def _section_ui(self, root):
        f = self._frame(root, "悬浮窗")
        p = self.cfg.get("popup")
        self.theme_var = tk.StringVar(value=p.get("theme", "dark"))
        self._row(f, "主题", ttk.Combobox(f, textvariable=self.theme_var,
                                          values=["dark", "light"],
                                          state="readonly", width=10))
        self.hide_var = tk.StringVar(value=str(p.get("auto_hide_ms", 0)))
        self._row(f, "自动隐藏(ms)", ttk.Entry(f, textvariable=self.hide_var, width=10),
                  "0 = 不自动隐藏")
        self.alpha_var = tk.DoubleVar(value=float(p.get("alpha", 0.97)))
        self._row(f, "不透明度", ttk.Scale(f, variable=self.alpha_var,
                                           from_=0.6, to=1.0, length=180))
        self.font_var = tk.IntVar(value=int(p.get("font_size", 13)))
        self._row(f, "正文字号", ttk.Scale(f, variable=self.font_var,
                                           from_=10, to=20, length=180))
        self.width_var = tk.IntVar(value=int(p.get("max_width", 520)))
        self._row(f, "最大宽度", ttk.Scale(f, variable=self.width_var,
                                           from_=320, to=900, length=180))
        self.follow_var = tk.BooleanVar(value=bool(p.get("follow_cursor", True)))
        ttk.Checkbutton(f, text="跟随鼠标光标定位（关闭则居中显示）",
                        variable=self.follow_var).pack(anchor="w", pady=2)
        self.mini_var = tk.BooleanVar(value=bool(self.cfg.get("mini_button", True)))
        ttk.Checkbutton(f, text="选中文字后自动显示『译』迷你按钮（点击即翻译）",
                        variable=self.mini_var).pack(anchor="w", pady=2)

    # ------------------------------------------------------------ 底部
    def _section_footer(self, root):
        f = tk.Frame(root, bg="#f5f7fa")
        f.pack(fill="x", padx=12, pady=12)
        ttk.Button(f, text="保存并应用", command=self._save).pack(side="left")
        ttk.Button(f, text="打开配置目录",
                   command=lambda: self.app.open_config_dir()).pack(side="left", padx=6)
        ttk.Button(f, text="退出程序", command=self.app.quit).pack(side="right")
        tk.Label(f, text="QuickTool v1.6.0 · 零第三方依赖",
                 fg="#a0a8b8", bg="#f5f7fa",
                 font=("Microsoft YaHei UI", 8)).pack(side="right", padx=10)

    # ------------------------------------------------------------ 保存
    def _save(self):
        cfg = self.cfg
        for key, var in self.hk_vars.items():
            value = var.get().strip()
            try:
                wa.HotkeySpec.parse(value)          # 先校验，非法就不让保存
            except Exception as exc:
                self._msg(str(exc), error=True)
                return
            cfg.set(key, value)

        cfg.set("engine", self.engine_var.get())
        cfg.set("fallback_chain", [n for n, v in self.fb_vars.items() if v.get()])
        cfg.set("target_lang", self.tgt_var.get())
        for k, v in self.llm_vars.items():
            cfg.set(f"llm.{k}", v.get().strip())
        cfg.set("deepl.api_key", self.deepl_key.get().strip())
        cfg.set("deepl.free", self.deepl_free.get())
        cfg.set("autostart", self.autostart.get())
        cfg.set("enable_tray", self.tray.get())
        cfg.set("ocr_lang", self.ocr_lang_var.get())
        cfg.set("popup.theme", self.theme_var.get())
        cfg.set("popup.auto_hide_ms", int(self.hide_var.get() or 0))
        cfg.set("popup.alpha", round(float(self.alpha_var.get()), 2))
        cfg.set("popup.font_size", int(self.font_var.get()))
        cfg.set("popup.max_width", int(self.width_var.get()))
        cfg.set("popup.follow_cursor", self.follow_var.get())
        cfg.set("mini_button", self.mini_var.get())
        cfg.save()

        ok, err = cfg.apply_autostart(self.autostart.get())
        self.app.reload_hotkeys()
        self.app.toggle_tray(self.tray.get())
        self._msg("已保存" if ok else f"已保存，但开机自启设置失败：{err}")

    def _msg(self, text, error=False):
        if hasattr(self, "test_out") and self.test_out.winfo_exists():
            self.test_out.configure(text=text, fg="#e0483c" if error else "#2563eb")

    def _test(self):
        self._save()
        self._msg("正在测试…")
        text = "Artificial intelligence is reshaping the software industry."
        try:
            out, engine, ok = self.app.translator.translate(text)
        except Exception as exc:
            out, engine, ok = str(exc), "-", False
        self._msg(("✔ " if ok else "✘ ") + f"{engine}：{out[:120]}", error=not ok)
