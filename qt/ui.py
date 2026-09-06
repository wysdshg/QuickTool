"""界面层：光标跟随悬浮窗 + 设置窗口。全部用 tkinter 标准库实现。"""
import base64
import math
import os
import struct
import time
import tkinter as tk
import zlib
from tkinter import font as tkfont, ttk
from tkinter.scrolledtext import ScrolledText

from . import winapi as wa
from .config import PROVIDER_LABELS, PROVIDER_ORDER
from .engines import ENGINES, engine_display_names

FONT_CN = "Microsoft YaHei UI"
FONT_MONO = "Consolas"

# v1.6.9 ⑥：所有窗口一律经 current_theme() 取色，禁止再写 THEMES["dark"]。
# win     = 常规窗口/页面底色（Settings 这类有正常标题栏的窗口背景）
# canvas  = 图像查看区底色（截图对照小窗的画布）
# on_mask = 半透明遮罩上的文字色 —— RegionSelector 的遮罩恒为半透明黑
#           （截图必须压暗屏幕内容），所以两个主题都用浅色，绝不能跟 fg
#           走：light 主题的 fg 是深色，写在黑遮罩上根本看不见。
# on_accent= accent 底色上的文字（便签搜索命中高亮）。dark 的 accent 是亮
#           蓝配深字，light 的 accent 是中蓝必须配白字——写死一个值必有一
#           个主题看不清，所以只能进主题表。
THEMES = {
    "dark": {"card": "#232936", "border": "#3a4256", "fg": "#e9edf5",
             "sub": "#93a0b5", "accent": "#5aa9ff", "hover": "#2e3648",
             "danger": "#ff6b6b", "win": "#1b202b", "canvas": "#1c212b",
             "on_mask": "#e8f0ff", "on_accent": "#0b1220"},
    "light": {"card": "#ffffff", "border": "#dfe5ee", "fg": "#1f2430",
              "sub": "#6b7688", "accent": "#2563eb", "hover": "#f2f5fa",
              "danger": "#e0483c", "win": "#f5f7fa", "canvas": "#e9edf3",
              "on_mask": "#e8f0ff", "on_accent": "#ffffff"},
}


def current_theme(app, fallback="dark"):
    """取当前生效的主题字典（v1.6.9 ⑥ 统一取色入口）。

    所有窗口都经此取色，杜绝写死 dark 主题。app 为空、配置缺失、主题名
    非法一律退回 fallback——**取色失败绝不能抛异常**：窗口构建期抛异常
    等于功能直接不可用，而主题只该影响外观。
    """
    try:
        p = app.cfg.get("popup") if app is not None else None
        name = (p or {}).get("theme", fallback)
    except Exception:
        name = fallback
    return THEMES.get(name, THEMES[fallback])


# ================================================================== 悬浮窗
class Popup:
    W_MIN, W_PAD = 280, 36

    def __init__(self, app, text, result, engine, ok=True, loading=False):
        self.app = app
        self.ok = ok
        self.closed = False
        self._close_job = None

        p = app.cfg.get("popup") or {}
        self.theme_name = p.get("theme", "dark")
        # v1.6.9 ⑥：统一走 current_theme()（含主题名非法回退）
        self.th = th = current_theme(app)
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

        if self.app.cfg.get("popup.follow_cursor", True):
            cx, cy = wa.get_cursor_pos()
            # 按鼠标所在显示器夹紧（v1.6.8 ⑤）：get_work_area() 只有主屏，
            # 副屏拖选出的气泡会被夹回主屏右缘。
            left, top, right, bottom = wa.get_work_area_at(cx, cy)
            x, y = cx + 16, cy + 26
            if y + h > bottom:
                y = max(top, cy - h - 18)
        else:
            left, top, right, bottom = wa.get_work_area()
            x, y = (left + right - w) // 2, (top + bottom - h) // 2
        x = max(left + 4, min(x, right - w - 4))
        y = max(top + 4, min(y, bottom - h - 4))
        self.win.geometry(f"{w}x{h}+{int(x)}+{int(y)}")

    # ------------------------------------------------------------ 交互
    def _bind(self):
        w = self.win
        w.bind("<Escape>", lambda e=None: self.close())
        w.bind("<FocusIn>", lambda e=None: self._cancel_close())
        w.bind("<FocusOut>", lambda e=None: self._schedule_close(260))
        for child in (w, self.card, self.body):
            child.bind("<ButtonPress-1>", self._drag_start)
            child.bind("<B1-Motion>", self._drag_move)
        w.after(30, lambda: wa.set_tool_window(w.winfo_id(), True))
        w.focus_force()

    def _drag_start(self, event=None):
        # 铁律(v1.6.4)：Tk 回调须容忍无参调用（销毁竞态）；无 event = 未发生拖动
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
    """『译』『便』双按钮：拖选文字后出现在鼠标旁。

    左『译』点击即翻译，右『便』点击把这段文字钉进置顶便签（等同 Ctrl+Alt+N，
    但文字已经在拖选时抓好了，不必再动一次剪贴板）。

    目标就是『小 + 不挡视野』：两个 26x26 圆形并排、半透明、置顶无边框，
    鼠标一进去就取消自动隐藏，点一下出译文/进便签、移开或等 2.2s 就消失。
    """

    SIZE = 26          # 单个圆形按钮直径
    GAP = 2            # 两圆间隙
    LABELS = ("译", "便")   # 顺序即回调分流顺序：0=翻译，1=便签
    HIDE_MS = 2200       # 出现后自动消失的时间（鼠标进入即暂停计时）
    FONT_SIZE = 11

    @property
    def width(self):
        """整个按钮条宽度（两个圆 + 间隙），供 geometry / 钩子忽略区使用。"""
        return self.SIZE * len(self.LABELS) + self.GAP * (len(self.LABELS) - 1)

    def __init__(self, app, x, y, text):
        self.app = app
        self.text = text
        self.closed = False
        self._hide_job = None
        # v1.6.9 ⑥：统一走 current_theme()（原先自己读 popup.theme 平铺键）
        self.th = th = current_theme(app)

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
        cv = tk.Canvas(self.win, width=self.width, height=s, highlightthickness=0,
                       bg=th["card"], cursor="hand2")
        cv.pack()
        for i, label in enumerate(self.LABELS):
            x0 = i * (s + self.GAP)
            cv.create_oval(x0 + 1, 1, x0 + s - 1, s - 1, fill=th["card"],
                           outline=th["border"], width=1,
                           tags=(f"bg{i}", "bg"))
            cv.create_text(x0 + s // 2, s // 2, text=label, fill=th["accent"],
                           tags=(f"txt{i}", "txt"),
                           font=("Microsoft YaHei UI", self.FONT_SIZE, "bold"))
        self.cv = cv
        self._hot = -1          # 当前悬停的按钮索引（-1 = 无）

    def _place(self, x, y):
        s, w = self.SIZE, self.width
        # 按鼠标所在显示器夹紧（v1.6.8 ⑤）：get_work_area() 只有主屏，副屏
        # 拖选后按钮会被夹回主屏右缘、离鼠标十万八千里。
        left, top, right, bottom = wa.get_work_area_at(x, y)
        px = min(x + 10, right - w - 6)
        py = y + 16
        if py + s > bottom:
            py = max(top + 6, y - s - 10)
        px = max(left + 6, px)
        self.win.geometry(f"{w}x{s}+{int(px)}+{int(py)}")
        self.win.update_idletasks()

    def _bind(self):
        self.win.bind("<Button-1>", self._click)
        # 鼠标进入即取消自动隐藏：按钮条变宽后，移到右侧『便』需要更多时间，
        # 若初始 2.2s 计时照跑，常常还没点到就消失了。
        self.win.bind("<Enter>",
                      lambda e=None: (self._hover(True), self._cancel_hide()))
        self.win.bind("<Leave>",
                      lambda e=None: (self._hover(False), self._schedule_hide(1200)))
        self.cv.bind("<Button-1>", self._click)
        self.cv.bind("<Motion>", lambda e=None: self._hot_move(e.x))

    def _index_at(self, x):
        """画布 x 坐标 -> 落在第几个按钮上（越界夹到 0..n-1）。"""
        n = len(self.LABELS)
        i = int(x // (self.SIZE + self.GAP))
        return 0 if i < 0 else (n - 1 if i > n - 1 else i)

    def _hot_move(self, x):
        i = self._index_at(x)
        if i == self._hot:
            return
        self._hot = i
        self._paint_hot()

    def _paint_hot(self):
        try:
            for i in range(len(self.LABELS)):
                self.cv.itemconfigure(
                    f"bg{i}",
                    fill=self.th["hover"] if i == self._hot else self.th["card"])
        except Exception:
            pass

    def _hover(self, on):
        if not on:
            self._hot = -1
        self._paint_hot()

    def _click(self, event=None):
        """按落点 x 分流：左『译』走翻译，右『便』加入便签。"""
        text = self.text
        idx = self._index_at(getattr(event, "x", 0) or 0)
        self.close()
        if idx == 0:
            self.app.mini_translate(text)
        else:
            self.app.mini_note(text)

    def get_rect(self):
        """屏幕坐标矩形 (left, top, right, bottom)，供鼠标钩子忽略自身区域。"""
        if self.closed:
            return None
        x, y = self.win.winfo_x(), self.win.winfo_y()
        return (x - 2, y - 2, x + self.width + 2, y + self.SIZE + 2)

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
        # 盖整个虚拟屏而非只盖主屏（v1.6.8 ⑤）：winfo_screenwidth() 只返回
        # 主屏尺寸，双屏下副屏根本框不到；且主屏不在虚拟原点时（副屏居左/
        # 上方），遮罩 +0+0 的 canvas 坐标与 BitBlt 的虚拟屏坐标错位，会
        # 抓到错区域。on_done 交付虚拟屏坐标，消费端 grab_screen_bmp 语义一致。
        vx, vy, sw, sh = wa.get_virtual_screen()
        self._vx, self._vy = vx, vy
        self.overrideredirect(True)
        # geometry 的 x/y 段必须用「+前缀 + 绝对值」连写（"+{vx}+{vy}"）：
        # 实测 Tk 把 "-1920"（无 + 前缀）当『距右缘偏移』语义，而 "+-1920"
        # 才是绝对坐标。副屏居左时 vx 为负，只能靠后者把遮罩左缘放到虚拟
        # 桌面 -1920 处（v1.6.8 ⑤）。
        self.geometry(f"{sw}x{sh}+{vx}+{vy}")
        self.configure(bg="black")
        self.attributes("-alpha", 0.32)
        self.attributes("-topmost", True)
        cv = tk.Canvas(self, bg="black", highlightthickness=0,
                       cursor="crosshair")
        cv.pack(fill="both", expand=True)
        # v1.6.9 ⑥：遮罩恒为半透明黑（截图要压暗屏幕内容），提示文字只能用
        # 浅色的 on_mask，绝不能跟 fg 走 —— light 主题的 fg 是深色，写在
        # 黑遮罩上根本看不见。
        th = current_theme(app)
        cv.create_text(sw // 2, 46,
                       text=title,
                       fill=th["on_mask"], font=("Microsoft YaHei UI", 12))
        self.cv = cv
        self._rect = None
        self._sx = self._sy = 0
        self._pressed = False          # 防御：只认 press→release 完整配对
        cv.bind("<ButtonPress-1>", self._press)
        cv.bind("<B1-Motion>", self._drag)
        cv.bind("<ButtonRelease-1>", self._release)
        self.bind("<Escape>", lambda e=None: self.close())
        self.bind("<Button-3>", lambda e=None: self.close())
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.deiconify()
        self.lift()
        self.focus_force()

    def _press(self, event=None):
        if event is None:
            return
        self._sx, self._sy = event.x, event.y
        self._pressed = True
        if self._rect:
            self.cv.delete(self._rect)

    def _drag(self, event=None):
        if event is None:
            return
        if self._rect:
            self.cv.delete(self._rect)
        self._rect = self.cv.create_rectangle(
            self._sx, self._sy, event.x, event.y,
            outline="#22c55e", width=2)

    def _release(self, event=None):
        # 孤儿 release（无 press 配对）直接忽略：遮罩弹出瞬间若鼠标残留
        # 按下状态，松手会带 _sx/_sy=0，从屏幕左上角(0,0)到鼠标位置生成
        # 一个巨大的"随机"选区直接截屏（v1.5.2 实测：1124x110/968x134
        # 两个左上角矩形，用户没动鼠标却完成截图）。
        if event is None:
            self._pressed = False    # 销毁竞态无配对事件：绝不触发截图
            return
        if not self._pressed:
            return
        self._pressed = False
        x0, x1 = sorted((self._sx, event.x))
        y0, y1 = sorted((self._sy, event.y))
        w, h = x1 - x0, y1 - y0
        if w < self.MIN_SIZE or h < self.MIN_SIZE:
            self.close()                     # 误触，取消
            return
        self.close(completed=True)           # 完成路径：_selecting 由回调清
        try:
            # canvas 坐标 → 虚拟屏坐标：主屏不在虚拟原点（副屏居左/上）时
            # 平移 (vx, vy) 后才是 BitBlt 能用的坐标（v1.6.8 ⑤）。
            self.on_done(x0 + self._vx, y0 + self._vy, w, h)
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

        # v1.6.9 ⑥：统一经 current_theme() 取色，不再写死 THEMES["dark"]
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
        self._build_bar(outer)
        self.cv = tk.Canvas(outer, bg=th["canvas"], highlightthickness=0,
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
        th = self.th                      # v1.6.9 ⑥
        bar = tk.Frame(parent, bg=th["card"])
        bar.pack(fill="x")
        self._bar = bar
        self.lbl_title = tk.Label(
            bar, text=f"对照 {self.index} · 滚轮缩放 · Esc 关闭",
            font=("Microsoft YaHei UI", 9), fg=th["sub"],
            bg=th["card"])
        self.lbl_title.pack(side="left", padx=8, pady=3)
        self.lbl_zoom = tk.Label(bar, text="100%", font=("Consolas", 9),
                                 fg=th["accent"],
                                 bg=th["card"])
        self.lbl_zoom.pack(side="right", padx=6)
        b_save = tk.Label(bar, text=" 存 PNG ", font=("Microsoft YaHei UI", 9),
                          fg=th["fg"], bg=th["card"],
                          cursor="hand2", padx=6)
        b_save.pack(side="right")
        b_save.bind("<Button-1>", lambda e=None: self._save_png())
        b = tk.Label(bar, text=" ✕ ", font=("Microsoft YaHei UI", 10),
                     fg=th["fg"], bg=th["card"],
                     cursor="hand2", padx=6)
        b.pack(side="right")
        b.bind("<Button-1>", lambda e=None: self.close())
        self._b_save, self._b_close = b_save, b   # refresh_theme 要改色

    def refresh_theme(self):
        """按当前配置重取主题并重刷本窗配色（v1.6.9 ⑥）。

        改主题时已开着的对照窗要跟着变，否则得关掉重开才生效。窗口可能
        正处于关闭流程中（widget 已销毁），故全程 try/except 兜住。
        """
        try:
            if not self.win.winfo_exists():
                return
        except Exception:
            return
        try:
            th = current_theme(self.app)
            self.th = th
            self.win.configure(bg=th["border"])
            self._outer.configure(bg=th["border"])
            self.cv.configure(bg=th["canvas"])
            self._bar.configure(bg=th["card"])
            self.lbl_title.configure(fg=th["sub"], bg=th["card"])
            self.lbl_zoom.configure(fg=th["accent"], bg=th["card"])
            self._b_save.configure(fg=th["fg"], bg=th["card"])
            self._b_close.configure(fg=th["fg"], bg=th["card"])
        except Exception:
            pass

    def _fit_size(self):
        """按落点显示器工作区 85% 上限取初始缩放倍数（只缩小，不放大）。

        v1.6.8 ⑤：原先按主屏工作区适配，副屏比主屏小时初始窗口会溢出副屏。
        """
        cx, cy = wa.get_cursor_pos()
        left, top, right, bottom = wa.get_work_area_at(cx, cy)
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
        # 按鼠标所在显示器夹紧（v1.6.8 ⑤），get_work_area() 只有主屏会把
        # 副屏上的对照窗夹回主屏。
        left, top, right, bottom = wa.get_work_area_at(cx, cy)
        # 级联偏移：序号越大越往右下错开，避免新窗口完全盖住旧窗口
        off = (self.index - 1) * self.CASCADE
        x = min(max(cx - w // 2 + off, left + 4), right - w - 4)
        y = min(max(cy - h // 2 + off, top + 4), bottom - h - 4)
        self.win.geometry(f"{w}x{h}+{int(x)}+{int(y)}")

    # ------------------------------------------------------------ 交互
    def _bind(self):
        win = self.win
        win.bind("<Escape>", lambda e=None: self.close())
        win.bind("<Button-3>", lambda e=None: self.close())
        win.bind("<Control-s>", lambda e=None: self._save_png())
        win.bind("<Control-S>", lambda e=None: self._save_png())
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

    def _drag_start(self, event=None):
        # 铁律(v1.6.4)：Tk 回调须容忍无参调用（销毁竞态）；无 event = 未发生拖动
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

    def _zoom(self, event=None):
        if event is None:
            return
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


class NoteWindow:
    """『置顶便签』：把选中的文字钉在屏幕一角，可反复累积、可直接编辑。

    解决的是「读长文 / 问 AI 时被术语打断，回来找不到读到哪」的痛点：
    选中那段话按热键，内容就搬到这个置顶小窗里，不占原文位置、不会丢。

    设计要点：
      - **单窗口累积**：一次阅读会话的所有片段追加到同一个窗口。截图是
        「一张图一窗口」（图没法合并），文本可以累积，多开反而挡视线。
        滚动顺序就是你的跳转历史 —— 窗口本身就是锚点栈的可视化。
      - **可编辑**：查完术语可以把解释直接贴回片段下面，几轮下来这份便签
        自动变成带原文上下文的学习笔记。
      - **搜索**：顶部「搜索」按钮弹出输入框，实时在便签全文里高亮命中并
        跳转到当前匹配（Enter / Shift+Enter 在命中间前后移动，Esc 关闭）。
        便签本身是「把散落片段攒到一起长期看」的工具，内部搜索比「跳回原
        应用搜」更直觉也更常用。
      - 零第三方依赖：ScrolledText 是 tkinter 自带的。

    和 PinWindow 的差异：正文换成可编辑文本，所以拖动只绑工具条 ——
    绑到 Toplevel 的话，点击正文会经 bindtags 传播触发拖动，选不中文字。
    """

    W, H = 520, 420        # 默认尺寸（cfg 里记过 note_w/note_h 则用记忆值）
    MIN_W, MIN_H = 320, 240
    MAX_W, MAX_H = 2400, 1600
    MAX_CHARS = 50000      # 累积上限，超出从头整段裁剪，防长时间使用爆内存
    HEAD_TAG = "seghead"

    def __init__(self, app, text="", source=""):
        self.app = app
        self.closed = False
        self._count = 0                 # 累计片段数
        self._first = 1                 # 现存最老的片段号（裁剪会推进）

        # 用户上一次调整过的窗口大小优先（无记忆/越界则回默认并夹紧）
        cfg = getattr(app, "cfg", None)
        self.W = self._clamp(int(cfg.get("note_w", self.W)) if cfg else self.W,
                             self.MIN_W, self.MAX_W)
        self.H = self._clamp(int(cfg.get("note_h", self.H)) if cfg else self.H,
                             self.MIN_H, self.MAX_H)

        # v1.6.9 ⑥：统一经 current_theme() 取色，不再写死 THEMES["dark"]
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
        top = tk.Frame(outer, bg=th["border"])
        top.pack(fill="x")
        self._outer, self._top = outer, top
        self._build_bar(top)

        self.txt = ScrolledText(
            outer, wrap="word", undo=True,
            bg=th["card"], fg=th["fg"],
            insertbackground=th["fg"],
            relief="flat", bd=0, padx=8, pady=6,
            font=(FONT_CN, 10), spacing1=2, spacing3=2)
        self.txt.pack(fill="both", expand=True)
        self._build_search_bar(top)
        self.txt.tag_configure(self.HEAD_TAG,
                               foreground=th["sub"],
                               font=(FONT_CN, 8))

        # 右下角 resize 手柄：无边框窗没有系统缩放边框，自己补一个。
        # place 叠在正文右下角（不占布局空间），便签类应用的惯例做法。
        grip = tk.Label(win, text="◢", font=(FONT_CN, 10),
                        fg=th["sub"], bg=th["card"],
                        cursor="bottom_right_corner")
        grip.place(relx=1.0, rely=1.0, anchor="se")
        grip.bind("<ButtonPress-1>", self._rs_start)
        grip.bind("<B1-Motion>", self._rs_move)
        self._grip = grip

        if text:
            self.append(text, source)
        self._place()
        self._bind()
        win.after(30, lambda: wa.set_tool_window(win.winfo_id(), True))
        win.focus_force()
        self.txt.focus_set()

    @staticmethod
    def _clamp(v, lo, hi):
        try:
            v = int(v)
        except Exception:
            return lo
        return max(lo, min(hi, v))

    @property
    def count(self):
        return self._count

    # ------------------------------------------------------------ 构建
    def _build_bar(self, parent):
        th = self.th                      # v1.6.9 ⑥
        # 从右往左 pack，所以列表顺序是「最右的先写」
        bar = tk.Frame(parent, bg=th["card"])
        bar.pack(fill="x")
        self._bar = bar
        self.lbl = tk.Label(bar, text="便签 · Esc 关闭", font=(FONT_CN, 9),
                            fg=th["sub"],
                            bg=th["card"])
        self.lbl.pack(side="left", padx=8, pady=3)
        self._bar_btns = []               # refresh_theme 要逐个改色
        for txt, cmd in ((" ✕ ", self.close), (" 清空 ", self._clear),
                         (" 存 txt ", self._save_txt),
                         (" 复制 ", self._copy_all),
                         (" 搜索 ", self._toggle_search)):
            b = tk.Label(bar, text=txt, font=(FONT_CN, 9),
                         fg=th["fg"], bg=th["card"],
                         cursor="hand2", padx=4)
            b.pack(side="right")
            # v1.6.4 铁律：Tk 回调必须容忍无参调用（widget 销毁竞态中 Tk 可能
            # 不带 event 调用绑定回调，`lambda e:` 会抛 TypeError 打断主循环）
            b.bind("<Button-1>", lambda e=None, c=cmd: c())
            self._bar_btns.append(b)

    def append(self, text, source=""):
        """追加一条片段。段头 = 序号 + 时间 + 来源窗口标题（灰色小字）。"""
        self._count += 1
        stamp = time.strftime("%H:%M")
        src = f" · {source}" if source else ""
        head = f"── {self._count} · {stamp}{src} " + "─" * 10 + "\n"
        self.txt.insert("end", head, self.HEAD_TAG)
        start = self.txt.index("end-1c")
        self.txt.insert("end", text.rstrip() + "\n\n")
        end = self.txt.index("end-1c")
        self.txt.tag_add(f"seg{self._count}", start, end)   # 片段定位 tag
        self._trim()
        self.txt.see("end")
        self.lbl.configure(text=f"便签 · {self._count} 段 · Esc 关闭")

    def _trim(self):
        try:
            total = self.txt.count("1.0", "end", "chars")[0]
        except Exception:
            return
        while total > self.MAX_CHARS and self._first < self._count:
            rng = self.txt.tag_ranges(f"seg{self._first}")
            if len(rng) < 2:
                self._first += 1
                continue
            # 连段头一起删：正文起点往上一行就是段头
            self.txt.delete(f"{str(rng[0])} linestart -1l", str(rng[1]))
            self._first += 1
            try:
                total = self.txt.count("1.0", "end", "chars")[0]
            except Exception:
                break

    # ------------------------------------------------------------ 搜索（便签内全文搜索）
    def _build_search_bar(self, parent):
        """顶部『搜索』按钮弹出的内联搜索条：输入框 + 计数 + 上/下一条 + 关闭。

        搜索条挂在 top 容器里（bar 之下、正文之上），默认 pack_forget 隐藏。
        """
        th = self.th                      # v1.6.9 ⑥
        sb = tk.Frame(parent, bg=th["card"])
        self._search_bar = sb
        self._search_matches = []
        self._search_idx = 0
        self._search_open = False

        ent = tk.Entry(sb, font=(FONT_CN, 10),
                       bg=th["border"], fg=th["fg"],
                       insertbackground=th["fg"],
                       relief="flat", bd=0,
                       highlightthickness=1,
                       highlightcolor=th["accent"])
        ent.pack(side="left", fill="x", expand=True, padx=6, pady=4)
        self._search_entry = ent

        lbl = tk.Label(sb, text="0/0", font=(FONT_CN, 9),
                       fg=th["sub"], bg=th["card"])
        lbl.pack(side="right", padx=(0, 4))
        self._search_label = lbl

        nxt = tk.Label(sb, text=" ▾ ", font=(FONT_CN, 9),
                       fg=th["sub"], bg=th["card"],
                       cursor="hand2")
        nxt.pack(side="right")
        nxt.bind("<Button-1>", lambda e=None: self._search_next())
        prv = tk.Label(sb, text=" ▴ ", font=(FONT_CN, 9),
                       fg=th["sub"], bg=th["card"],
                       cursor="hand2")
        prv.pack(side="right")
        prv.bind("<Button-1>", lambda e=None: self._search_prev())
        close = tk.Label(sb, text=" ✕ ", font=(FONT_CN, 9),
                         fg=th["sub"], bg=th["card"],
                         cursor="hand2")
        close.pack(side="right", padx=(4, 2))
        close.bind("<Button-1>", lambda e=None: self._toggle_search(force=False))
        self._search_next_btn, self._search_prev_btn = nxt, prv
        self._search_close_btn = close

        # 输入框事件：每次按键实时搜；Enter/Shift+Enter 前后跳；Esc 关搜索条
        ent.bind("<KeyRelease>", self._do_search)
        ent.bind("<Return>", self._search_next)
        ent.bind("<Shift-Return>", self._search_prev)
        ent.bind("<Escape>", lambda e=None: self._toggle_search(force=False))
        sb.pack_forget()

        # 高亮 tag：全部命中（accent 底）+ 当前命中（金底，更醒目）。
        # on_accent 随主题走（亮蓝底配深字 / 中蓝底配白字）；search_cur 是
        # 金色强调底，两个主题都配深字，故不随主题。
        self.txt.tag_configure("search_hit",
                               background=th["accent"],
                               foreground=th["on_accent"])
        self.txt.tag_configure("search_cur",
                               background="#ffd166", foreground="#0b1220")

    def refresh_theme(self):
        """按当前配置重取主题并重刷本窗配色（v1.6.9 ⑥）。

        便签是长期开着的窗口，改主题后必须立刻跟着变，否则得关掉重开才
        生效。窗口可能正处于关闭流程中（widget 已销毁），故全程 try/except
        兜住——主题只影响外观，绝不能把窗口搞崩。
        """
        try:
            if not self.win.winfo_exists():
                return
        except Exception:
            return
        try:
            th = current_theme(self.app)
            self.th = th
            self.win.configure(bg=th["border"])
            self._outer.configure(bg=th["border"])
            self._top.configure(bg=th["border"])
            self._bar.configure(bg=th["card"])
            self.lbl.configure(fg=th["sub"], bg=th["card"])
            for b in self._bar_btns:
                b.configure(fg=th["fg"], bg=th["card"])
            self.txt.configure(bg=th["card"], fg=th["fg"],
                               insertbackground=th["fg"])
            self.txt.tag_configure(self.HEAD_TAG, foreground=th["sub"])
            self._grip.configure(fg=th["sub"], bg=th["card"])
            self._search_bar.configure(bg=th["card"])
            self._search_entry.configure(bg=th["border"], fg=th["fg"],
                                         insertbackground=th["fg"],
                                         highlightcolor=th["accent"])
            self._search_label.configure(fg=th["sub"], bg=th["card"])
            for w in (self._search_next_btn, self._search_prev_btn,
                      self._search_close_btn):
                w.configure(fg=th["sub"], bg=th["card"])
            self.txt.tag_configure("search_hit", background=th["accent"],
                                   foreground=th["on_accent"])
        except Exception:
            pass

    def _toggle_search(self, force=None):
        """点击『搜索』切换搜索条；force=False 强制收起（关闭按钮 / Esc）。

        开合状态用显式布尔 `_search_open` 记录（而非依赖 winfo_ismapped，
        后者在无头/未映射窗口下不可靠）。
        """
        if force is False or self._search_open:
            self._search_bar.pack_forget()
            self._search_open = False
            self._clear_search()
            self.txt.focus_set()
            return
        self._search_bar.pack(fill="x")
        self._search_open = True
        self._search_entry.delete(0, "end")
        self._search_entry.focus_set()

    def _clear_search(self):
        self.txt.tag_remove("search_hit", "1.0", "end")
        self.txt.tag_remove("search_cur", "1.0", "end")
        self._search_matches = []
        self._search_idx = 0
        if getattr(self, "_search_label", None) is not None:
            self._search_label.configure(text="0/0")

    def _do_search(self, event=None):
        """实时全文搜索：高亮全部命中，跳到第一条，更新 n/m 计数。"""
        q = self._search_entry.get().strip()
        self.txt.tag_remove("search_hit", "1.0", "end")
        self.txt.tag_remove("search_cur", "1.0", "end")
        self._search_matches = []
        self._search_idx = 0
        if not q:
            self._search_label.configure(text="0/0")
            return
        n = len(q)
        start = "1.0"
        while True:
            pos = self.txt.search(q, start, stopindex="end", nocase=True)
            if not pos:
                break
            end = f"{pos}+{n}c"
            self.txt.tag_add("search_hit", pos, end)
            self._search_matches.append((pos, end))
            start = end                      # 跳过本命中，避免死循环
        total = len(self._search_matches)
        if total:
            self._search_idx = 0
            self._show_match()
            self._search_label.configure(text=f"1/{total}")
        else:
            self._search_label.configure(text="0/0")

    def _show_match(self):
        if not self._search_matches:
            return
        self._search_idx %= len(self._search_matches)
        pos, end = self._search_matches[self._search_idx]
        self.txt.tag_remove("search_cur", "1.0", "end")
        self.txt.tag_add("search_cur", pos, end)
        self.txt.see(pos)

    def _search_next(self, event=None):
        if not self._search_matches:
            return "break"
        self._search_idx = (self._search_idx + 1) % len(self._search_matches)
        self._show_match()
        self._search_label.configure(
            text=f"{self._search_idx + 1}/{len(self._search_matches)}")
        return "break"

    def _search_prev(self, event=None):
        if not self._search_matches:
            return "break"
        self._search_idx = (self._search_idx - 1) % len(self._search_matches)
        self._show_match()
        self._search_label.configure(
            text=f"{self._search_idx + 1}/{len(self._search_matches)}")
        return "break"

    def _copy_all(self):
        text = self.txt.get("1.0", "end-1c")
        if not text.strip():
            self._toast("复制", "便签是空的。")
            return
        wa.set_clipboard_text(text)
        self._toast("已复制全部", f"{len(text)} 字")

    def _save_txt(self):
        text = self.txt.get("1.0", "end-1c")
        if not text.strip():
            self._toast("保存", "便签是空的。")
            return None
        try:
            d = note_save_dir()
            os.makedirs(d, exist_ok=True)
            path = os.path.join(
                d, time.strftime("QuickTool_便签_%Y%m%d_%H%M%S.txt"))
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception as exc:
            self._toast("保存失败", str(exc))
            return None
        self._toast("便签已保存", path)
        return path

    def _clear(self):
        n = self._count
        self.txt.delete("1.0", "end")
        self._count, self._first = 0, 1
        self.lbl.configure(text="便签 · Esc 关闭")
        self._toast("已清空", f"清掉了 {n} 段")

    def _toast(self, title, text):
        q = getattr(self.app, "q", None)
        if q is not None:
            q.put(("toast", (title, text)))

    # ------------------------------------------------------------ 交互
    def _place(self):
        w, h = self.W, self.H
        # 位置记忆优先：上次位置落在哪个显示器就回哪个显示器。原先按主屏
        # 工作区夹紧——在副屏上关掉的便签重开会被拽回主屏（v1.6.8 ⑤）。
        # get_work_area_at 对『已拔出显示器上的记忆点』自动落到最近在用屏，
        # 与『夹紧 + 失效回退』等价且不丢副屏记忆。
        x = y = None
        cfg = getattr(self.app, "cfg", None)
        if cfg is not None:
            try:
                rx = cfg.get("note_x", -9999)
                ry = cfg.get("note_y", -9999)
                if isinstance(rx, int) and isinstance(ry, int):
                    left, top, right, bottom = wa.get_work_area_at(rx, ry)
                    x = self._clamp(rx, left, right - w - 4)
                    y = self._clamp(ry, top, bottom - h - 4)
            except Exception:
                x = y = None
        if x is None:
            cx, cy = wa.get_cursor_pos()
            left, top, right, bottom = wa.get_work_area_at(cx, cy)
            x = min(max(cx - w // 2, left + 4), right - w - 4)
            y = min(max(cy - h // 2, top + 4), bottom - h - 4)
        self.win.geometry(f"{w}x{h}+{int(x)}+{int(y)}")

    def _rs_start(self, event=None):
        """右下角手柄按下：记录拖拽起点与当前窗口尺寸。"""
        if event is None:
            return
        self._rs = (event.x_root, event.y_root,
                    self.win.winfo_width(), self.win.winfo_height())

    def _rs_move(self, event=None):
        """右下角拖拽缩放：左上角不动，只改宽高（夹紧到 MIN/MAX）。"""
        if event is None:
            return
        try:
            x0, y0, w0, h0 = self._rs
        except AttributeError:
            return
        w = self._clamp(w0 + event.x_root - x0, self.MIN_W, self.MAX_W)
        h = self._clamp(h0 + event.y_root - y0, self.MIN_H, self.MAX_H)
        self.W, self.H = w, h             # 同步逻辑尺寸（保存时兜底用）
        self.win.geometry(f"{w}x{h}")

    def _save_geometry(self):
        """把当前大小和位置写进配置（关窗时调用，下次原样恢复）。

        winfo 读数在窗口未布局完时是 1，低于 MIN 说明不可信，用逻辑尺寸兜底
        ——否则「打开即关」的便签会把记忆尺寸错误写成最小值，越关越小。
        """
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
            cfg.set("note_w", int(w))
            cfg.set("note_h", int(h))
            cfg.set("note_x", int(self.win.winfo_x()))
            cfg.set("note_y", int(self.win.winfo_y()))
            cfg.save()
        except Exception:
            pass

    def _bind(self):
        win = self.win
        win.bind("<Escape>", lambda e=None: self.close())
        win.bind("<Control-s>", lambda e=None: self._save_txt())
        win.bind("<Control-S>", lambda e=None: self._save_txt())
        # 拖动只绑工具条：绑到 Toplevel 的话点击正文会经 bindtags 传播过来
        self._bar.bind("<ButtonPress-1>", self._drag_start)
        self._bar.bind("<B1-Motion>", self._drag_move)
        win.bind("<ButtonPress-1>", self._raise, add="+")

    def _raise(self, e=None):
        try:
            self.win.lift()
        except Exception:
            pass

    def _drag_start(self, event=None):
        # 铁律(v1.6.4)：Tk 回调须容忍无参调用（销毁竞态）；无 event = 未发生拖动
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

    def close(self):
        if self.closed:
            return
        self.closed = True
        self._save_geometry()          # 大小/位置记忆，下次原样恢复
        try:
            self.win.destroy()
        except Exception:
            pass
        if getattr(self.app, "note_win", None) is self:
            self.app.note_win = None


def note_save_dir():
    r"""便签导出目录：%USERPROFILE%\Documents\QuickTool\（文本文件不进图片库）。"""
    return os.path.join(os.path.expanduser("~"), "Documents", "QuickTool")


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
    r"""截图文件默认保存目录：%USERPROFILE%\Pictures\QuickTool\（同系统截屏惯例）。"""
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
        # v1.6.9 ⑥：设置窗口跟随主题（原先整窗写死浅色 #f5f7fa）
        self.th = current_theme(app)
        self.title("QuickTool 设置")
        self.geometry("680x620")
        self.resizable(True, True)
        self.configure(bg=self.th["win"])
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
        self._apply_ttk_theme(ttk.Style(self))

        th = self.th
        wrap = tk.Frame(self, bg=th["win"])
        wrap.pack(fill="both", expand=True)
        canvas = tk.Canvas(wrap, bg=th["win"], highlightthickness=0)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(canvas, bg=th["win"])
        inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                   lambda e=None: canvas.configure(scrollregion=canvas.bbox("all")))
        # 内容宽度跟随窗口：不然 inner 以自然宽度渲染，超宽部分被横向裁掉
        canvas.bind("<Configure>",
                    lambda e=None: canvas.itemconfigure(inner_id, width=e.width))
        # 滚轮必须绑在 Settings 自身 Toplevel 而非 bind_all：
        # bind_all 挂在 app 级 bindtag 'all' 上，窗口销毁后 handler 不随窗口移除，
        # 仍引用已销毁的 canvas——此后任何窗口滚轮都会对死 canvas 调 yview_scroll
        # 抛 TclError（真实症状：关闭设置后日志持续刷 UNCAUGHT-TK，2026-09-03 实证）。
        # Toplevel 级绑定经 bindtags 对全部子控件生效，且随窗口销毁自动清除。
        def _scroll_canvas(delta):
            canvas.yview_scroll(int(-1 * (delta / 120)), "units")
        self._scroll_canvas = _scroll_canvas
        self.bind("<MouseWheel>", lambda e: _scroll_canvas(e.delta))

        self._section_engine(inner)
        self._section_hotkey(inner)
        self._section_llm(inner)
        self._section_rag(inner)
        self._section_misc(inner)
        self._section_ui(inner)
        self._section_footer(inner)
        # 必须在全部 section 建完后跑：给每个 Combobox 拦滚轮（见函数注释）
        self._tame_combo_wheel(self)

    def _tame_combo_wheel(self, w):
        """滚轮悬停在 ttk.Combobox 上时「滚页面、不改值」（v1.7.2）。

        Windows 的 TCombobox 类绑定把 <MouseWheel> 直接变成「改选中项」——
        用户想滚设置页，鼠标扫过『生成服务商』就把智谱滚成了 DeepSeek
        （真实报障）。控件级 bindtag 先于类绑定执行，转发给页面滚动后
        return "break" 即可拦掉类绑定；下拉列表展开后的滚轮由 popdown
        自己的列表处理，不受影响。
        """
        for c in w.winfo_children():
            if c.winfo_class() == "TCombobox":
                c.bind("<MouseWheel>", self._on_combo_wheel)
            self._tame_combo_wheel(c)

    def _on_combo_wheel(self, e):
        self._scroll_canvas(e.delta)
        return "break"                          # 拦截：类绑定不再改选中项

    def _apply_ttk_theme(self, style):
        """把 ttk 控件刷成当前主题色（v1.6.9 ⑥）。

        Windows 默认主题（vista / xpnative）由系统绘制，**不接受自定义背景
        色**——要跟随主题只能切到完全可定制的 clam。代价是控件变成扁平的
        跨平台风格，这是「跟随主题」的必要交换。

        ttk 改 style 后所有已存在的控件会自动跟随，所以切换主题时只需重跑
        一次本方法，不必逐个控件改色（原生 tk widget 没这机制，见
        _refresh_theme 的递归重刷）。
        """
        th = self.th
        # 必须最先做：切主题会清空此前所有 configure
        try:
            style.theme_use("clam")
        except Exception:
            pass

        F = "System"
        for f in ("Microsoft YaHei UI", "Microsoft YaHei"):
            try:
                tkfont.Font(family=f, size=9).actual()
                F = f
                break
            except Exception:
                continue

        style.configure("TLabel", font=(F, 9),
                        background=th["win"], foreground=th["fg"])
        style.configure("TButton", font=(F, 9), background=th["hover"],
                        foreground=th["fg"], bordercolor=th["border"],
                        lightcolor=th["card"], darkcolor=th["border"])
        style.map("TButton",
                  background=[("active", th["accent"]),
                              ("pressed", th["accent"])],
                  foreground=[("active", th["on_accent"])])
        style.configure("TLabelframe", background=th["win"],
                        bordercolor=th["border"])
        style.configure("TLabelframe.Label", font=(F, 9, "bold"),
                        background=th["win"], foreground=th["accent"])
        style.configure("TEntry", fieldbackground=th["card"],
                        foreground=th["fg"], insertcolor=th["fg"],
                        bordercolor=th["border"], lightcolor=th["card"],
                        darkcolor=th["border"])
        style.configure("TCombobox", fieldbackground=th["card"],
                        background=th["card"], foreground=th["fg"],
                        arrowcolor=th["fg"], bordercolor=th["border"],
                        lightcolor=th["card"], darkcolor=th["border"])
        style.configure("TCheckbutton", background=th["win"],
                        foreground=th["fg"], indicatorcolor=th["card"],
                        bordercolor=th["border"])
        style.map("TCheckbutton",
                  background=[("active", th["win"])],
                  indicatorcolor=[("selected", th["accent"])])
        style.configure("TScale", background=th["win"],
                        troughcolor=th["hover"], bordercolor=th["border"],
                        lightcolor=th["accent"], darkcolor=th["accent"])
        style.configure("TScrollbar", background=th["hover"],
                        troughcolor=th["win"], bordercolor=th["border"],
                        arrowcolor=th["fg"])
        # Combobox 的下拉列表是原生 Listbox，ttk style 管不到，只能 option_add
        # （否则 dark 主题下会弹出一个刺眼的白列表）
        try:
            self.option_add("*TCombobox*Listbox.background", th["card"])
            self.option_add("*TCombobox*Listbox.foreground", th["fg"])
            self.option_add("*TCombobox*Listbox.selectBackground", th["accent"])
            self.option_add("*TCombobox*Listbox.selectForeground",
                            th["on_accent"])
        except Exception:
            pass

    def _frame(self, parent, title):
        lf = ttk.LabelFrame(parent, text=" " + title + " ", padding=10)
        lf.pack(fill="x", padx=12, pady=6)
        return lf

    def _row(self, parent, label, widget, hint=""):
        # widget 的 master 是 parent（LabelFrame），但必须 pack 到行 Frame r 里：
        # 用 in_=r 指定几何父（Tk 允许 master 是 in_ 目标的父级）。
        # 之前直接 widget.pack(side="left") pack 到了 LabelFrame —— 行 Frame（top）
        # 与 widget（left）交错 pack，空洞逐行右移：每行被上一行的 Entry 推右
        # 一个 Entry 宽度，整个内容区被撑到 ~2000px 宽，热键区只能看到前两行
        # 且全部错位（用户报"只有划词翻译和截图翻译俩个 + 显示 bug"）。
        r = tk.Frame(parent, bg=parent.cget("bg") if "bg" in parent.keys() else self.th["win"])
        r.pack(fill="x", pady=3)
        tk.Label(r, text=label, width=12, anchor="w", bg=self.th["win"],
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        widget.pack(in_=r, side="left", fill="x", expand=True)
        # in_ 只改几何归属，不改 z-order：widget 创建早于 r（二者是兄弟窗口），
        # 后创建的 r 会盖住 widget —— 输入框看不见值也点不到（v1.6.1 报障：
        # 所有输入框点不了 + 不显示当前快捷键）。把行 Frame 压到 stacking
        # 最底层，让 widget 露出来。
        r.lower()
        if hint:
            tk.Label(r, text=hint, fg=self.th["sub"], bg=self.th["win"],
                     font=("Microsoft YaHei UI", 8)).pack(in_=r, side="left", padx=6)

    # ------------------------------------------------------------ 引擎
    def _section_engine(self, root):
        f = self._frame(root, "翻译引擎")
        names = [(n, lab) for n, lab, _ in engine_display_names()]
        self.engine_var = tk.StringVar(value=self.cfg.get("engine"))
        self._row(f, "主引擎", ttk.Combobox(
            f, textvariable=self.engine_var, values=[n for n, _ in names],
            state="readonly", width=14))
        tk.Label(f, text=" · ".join(f"{n}={lab}" for n, lab in names),
                 fg=self.th["sub"], bg=self.th["win"], wraplength=500, justify="left",
                 font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(2, 6))

        self.fb_vars = {}
        fb = tk.Frame(f, bg=self.th["win"])
        fb.pack(fill="x")
        tk.Label(fb, text="失败回退", width=12, anchor="w", bg=self.th["win"],
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

        btns = tk.Frame(f, bg=self.th["win"])
        btns.pack(fill="x", pady=(6, 0))
        ttk.Button(btns, text="测试当前引擎", command=self._test).pack(side="left")
        self.test_out = tk.Label(btns, text="", fg=self.th["accent"], bg=self.th["win"],
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
                           ("hotkey_note", "置顶便签"),
                           ("hotkey_rag", "快捷提问(RAG)"),
                           ("hotkey_settings", "打开设置"),
                           ("hotkey_quit", "退出程序")):
            var = tk.StringVar(value=self.cfg.get(key))
            self.hk_vars[key] = var
            e = ttk.Entry(f, textvariable=var, width=18)
            self._row(f, label, e)
            e.bind("<KeyPress>", lambda ev=None, v=var: self._capture(ev, v))
        tk.Label(f, text="注意：录制时全局热键也会同时触发一次，属正常现象。\n"
                         "若默认组合被其他程序占用，启动时会自动改用下方显示的备用组合。",
                 fg=self.th["sub"], bg=self.th["win"],
                 font=("Microsoft YaHei UI", 8),
                 justify="left", anchor="w").pack(anchor="w")

    def _capture(self, event=None, var=None):
        # 热键录制同样遵守铁律：销毁竞态下 Tk 可能无参调用，直接忽略即可
        if event is None:
            return
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

    # ------------------------------------------------------------ 凭据中心
    def _section_llm(self, root):
        """凭据中心：五家厂商 Key 统一填写（翻译 LLM 与 RAG 生成共用）。

        - 生成服务商：翻译 + RAG 问答生成走这一家（llm.provider）
        - 生成模型：可下拉选该厂商的历史模型（models_for），也可手输新模型；
          切厂商时下拉实时跟随（v1.7.2）——之前 Entry 要手打全名、换过也不记
        - 各厂商 API Key：落盘自动 DPAPI 加密（_SECRET_FIELDS）
        - 检索（embed/rerank）固定硅基流动，Key 就是「硅基流动」那一行
        """
        f = self._frame(root, "凭据中心 · 大模型（翻译 / 问答共用 Key）")
        providers = self.cfg.get("providers") or {}
        llm = self.cfg.get("llm") or {}
        cur = llm.get("provider") or "siliconflow"
        if cur not in PROVIDER_ORDER:
            cur = "siliconflow"
        self._lab2key = {PROVIDER_LABELS[k]: k for k in PROVIDER_ORDER}
        self.gen_provider_var = tk.StringVar(value=PROVIDER_LABELS[cur])
        prov_box = ttk.Combobox(f, textvariable=self.gen_provider_var,
                                values=[PROVIDER_LABELS[k] for k in PROVIDER_ORDER],
                                state="readonly", width=20)
        self._row(f, "生成服务商", prov_box, "翻译与 RAG 生成共用此家")
        self.llm_model_var = tk.StringVar(value=llm.get("model", ""))
        self.model_box = ttk.Combobox(f, textvariable=self.llm_model_var,
                                      values=self.cfg.models_for(cur),
                                      width=30)
        self._row(f, "生成模型", self.model_box,
                  "下拉=历史；留空=厂商默认")
        prov_box.bind("<<ComboboxSelected>>", self._on_provider_change)
        tk.Label(f, text="— 各厂商 API Key（保存后 DPAPI 加密）—",
                 fg=self.th["sub"], bg=self.th["win"],
                 font=("Microsoft YaHei UI", 8)).pack(anchor="w",
                                                      padx=12, pady=(4, 0))
        self.prov_key_vars = {}
        for p in PROVIDER_ORDER:
            pv = providers.get(p) or {}
            v = tk.StringVar(value=pv.get("api_key", ""))
            self.prov_key_vars[p] = v
            # 行标签列宽只有 12 字符，「自定义（OpenAI 兼容） Key」会被截断
            # （v1.7.2 前用户截图可见），自定义用短标签
            label = "自定义 Key" if p == "custom" else f"{PROVIDER_LABELS[p]} Key"
            self._row(f, label,
                      ttk.Entry(f, textvariable=v, show="*", width=30))
        # 自定义厂商额外：Base URL + 默认模型
        cu = providers.get("custom") or {}
        self.prov_custom_url = tk.StringVar(value=cu.get("base_url", ""))
        self._row(f, "自定义 Base URL",
                  ttk.Entry(f, textvariable=self.prov_custom_url, width=30),
                  "仅『自定义』需要")
        self.prov_custom_model = tk.StringVar(value=cu.get("chat_model", ""))
        self._row(f, "自定义默认模型",
                  ttk.Entry(f, textvariable=self.prov_custom_model, width=30),
                  "留空则用生成模型指定")

    def _on_provider_change(self, _event=None):
        """切厂商（v1.7.2）：模型下拉实时换成该厂商的历史列表。

        原模型值若不属于新厂商（跨厂商残留下 old 厂商的模型名），回退为
        空 = 新厂商默认——之前要手动清空再手打新模型全名，两步并一步。
        """
        key = self._lab2key.get(self.gen_provider_var.get(), "siliconflow")
        models = self.cfg.models_for(key)
        self.model_box.configure(values=models)
        if self.llm_model_var.get().strip() not in models:
            self.llm_model_var.set("")

    # ------------------------------------------------------------ RAG（v1.7）
    def _section_rag(self, root):
        f = self._frame(root, "RAG 快捷问答（本地知识库检索）")
        sf = (self.cfg.get("providers") or {}).get("siliconflow") or {}
        self.rag_embed_var = tk.StringVar(
            value=sf.get("embed_model", "BAAI/bge-m3"))
        self._row(f, "向量模型", ttk.Entry(f, textvariable=self.rag_embed_var,
                                           width=30),
                  "检索固定走硅基流动")
        self.rag_rerank_var = tk.StringVar(
            value=sf.get("rerank_model", "BAAI/bge-reranker-v2-m3"))
        self._row(f, "重排模型", ttk.Entry(f, textvariable=self.rag_rerank_var,
                                           width=30),
                  "无重排端点可留空")
        # 检索参数（v1.7.2：之前只能改配置文件，UI 不可调）
        retr = self.cfg.get("rag.retrieval") or {}
        self.rag_variants_var = tk.StringVar(
            value=str(int(retr.get("fusion_variants", 3))))
        self._row(f, "检索变体数",
                  ttk.Combobox(f, textvariable=self.rag_variants_var,
                               values=["1", "2", "3", "4", "5"],
                               state="readonly", width=4),
                  "1=关 Fusion，省一次调用")
        self.rag_recall_var = tk.StringVar(
            value=str(int(retr.get("bm25_top_k", 30))))
        self._row(f, "每路召回TopK",
                  ttk.Entry(f, textvariable=self.rag_recall_var, width=6),
                  "10~100，多概念问题调大")
        self.rag_rerank_k_var = tk.StringVar(
            value=str(int(retr.get("rerank_top_k", 5))))
        self._row(f, "精排 TopK",
                  ttk.Entry(f, textvariable=self.rag_rerank_k_var, width=6),
                  "进回答的资料条数 1~30")
        presets = tk.Frame(f, bg=self.th["win"])
        presets.pack(fill="x", pady=2)
        tk.Label(presets, text="", width=12, bg=self.th["win"]).pack(side="left")
        ttk.Button(presets, text="打开文档库…", width=14,
                   command=lambda: getattr(self.app, "open_kb_manager",
                                           lambda: None)()).pack(side="left", padx=2)
        tk.Label(f,
                 text="用法：先在『打开文档库』导入 .txt/.md 资料，"
                      "再选中文字按 Ctrl+Alt+Y 提问；回答的生成模型 = "
                      "凭据中心的生成服务商",
                 fg=self.th["sub"], bg=self.th["win"],
                 font=("Microsoft YaHei UI", 8), wraplength=430,
                 justify="left", anchor="w").pack(anchor="w",
                                                  padx=12, pady=(2, 4))

    # ------------------------------------------------------------ 其他
    def _section_misc(self, root):
        df = self._frame(root, "其他")
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
        ttk.Checkbutton(f, text="选中文字后自动显示『译』『便』迷你按钮（翻译 / 加便签）",
                        variable=self.mini_var).pack(anchor="w", pady=2)

    # ------------------------------------------------------------ 底部
    def _section_footer(self, root):
        f = tk.Frame(root, bg=self.th["win"])
        f.pack(fill="x", padx=12, pady=12)
        ttk.Button(f, text="保存并应用", command=self._save).pack(side="left")
        ttk.Button(f, text="打开配置目录",
                   command=lambda: self.app.open_config_dir()).pack(side="left", padx=6)
        ttk.Button(f, text="退出程序", command=self.app.quit).pack(side="right")
        tk.Label(f, text="QuickTool v1.7.2 · 零第三方依赖",
                 fg=self.th["sub"], bg=self.th["win"],
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

        # 凭据中心（v1.7.0 A）：生成厂商 / 生成模型 / 五家 Key / 检索模型。
        # Key 与自定义端点存到 providers.*，save() 时 _SECRET_FIELDS 自动
        # DPAPI 加密；非 UI 字段（thinking、各家 base_url 等）保持不动。
        prov_key = self._lab2key.get(self.gen_provider_var.get(), "siliconflow")
        before_ep = (str(self.cfg.get("llm.provider")),
                     str(self.cfg.get("llm.model") or ""),
                     str((self.cfg.get("providers") or {})
                         .get("siliconflow", {}).get("embed_model", "")),
                     str((self.cfg.get("providers") or {})
                         .get("siliconflow", {}).get("rerank_model", "")),
                     str((self.cfg.get("rag") or {})
                         .get("retrieval", {}).get("rerank_top_k", "")))
        cfg.set("llm.provider", prov_key)
        model_val = self.llm_model_var.get().strip()
        cfg.set("llm.model", model_val)
        # 模型历史记忆（v1.7.2）：生效模型（覆盖值，留空则厂商默认）记入该
        # 厂商的 models 列表——换过一次下次下拉直选，不再手打全名。
        if not model_val:
            model_val = str((cfg.get("providers") or {})
                            .get(prov_key, {}).get("chat_model", "") or "").strip()
        if model_val:
            cfg.remember_model(prov_key, model_val)
        for p, v in self.prov_key_vars.items():
            cfg.set(f"providers.{p}.api_key", v.get().strip())
        cfg.set("providers.custom.base_url",
                self.prov_custom_url.get().strip())
        cfg.set("providers.custom.chat_model",
                self.prov_custom_model.get().strip())
        cfg.set("providers.siliconflow.embed_model",
                self.rag_embed_var.get().strip() or "BAAI/bge-m3")
        cfg.set("providers.siliconflow.rerank_model",
                self.rag_rerank_var.get().strip()
                or "BAAI/bge-reranker-v2-m3")
        # 检索参数（v1.7.2）：非法输入夹紧回默认，绝不让保存失败
        cfg.set("rag.retrieval.fusion_variants",
                self._clamp_int(self.rag_variants_var, 1, 5, 3))
        recall_k = self._clamp_int(self.rag_recall_var, 10, 100, 30)
        cfg.set("rag.retrieval.bm25_top_k", recall_k)
        cfg.set("rag.retrieval.vector_top_k", recall_k)
        cfg.set("rag.retrieval.rerank_top_k",
                self._clamp_int(self.rag_rerank_k_var, 1, 30, 5))
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
        self._apply_theme_change()      # v1.6.9 ⑥：主题改了立刻生效
        # 生成/检索端点热切换（v1.7.2）：RagApi 在首问时固化了端点三元组，
        # 不重建的话改完设置仍旧发旧厂商——此前「切模型要重启」的根因。
        self.app.invalidate_rag_engine()
        after_ep = (str(cfg.get("llm.provider")),
                    str(cfg.get("llm.model") or ""),
                    str((cfg.get("providers") or {})
                        .get("siliconflow", {}).get("embed_model", "")),
                    str((cfg.get("providers") or {})
                        .get("siliconflow", {}).get("rerank_model", "")),
                    str(cfg.get("rag.retrieval", {}).get("rerank_top_k", "")))
        switched = after_ep != before_ep
        base_msg = "已保存" if ok else f"已保存，但开机自启设置失败：{err}"
        self._msg(base_msg + ("；生成/检索模型下一条问答生效" if switched else ""))

    @staticmethod
    def _clamp_int(var, lo, hi, default):
        """读 Entry 的整数并夹紧到 [lo, hi]；非数字回 default（提示行不报错）。"""
        try:
            v = int(str(var.get()).strip())
        except (TypeError, ValueError):
            return default
        return max(lo, min(hi, v))

    def _msg(self, text, error=False):
        if hasattr(self, "test_out") and self.test_out.winfo_exists():
            self.test_out.configure(text=text,
                                     fg=self.th["danger"] if error
                                     else self.th["accent"])

    # ------------------------------------------------------------ 换肤
    def _apply_theme_change(self):
        """保存后若主题变了，就地重刷设置窗 + 给已开窗口换肤（v1.6.9 ⑥）。

        current_theme() 返回的是 THEMES 里的同一个字典对象，所以 `is` 判断
        就是最可靠的"主题有没有变"检测——没变直接返回，省掉一次全窗遍历。
        换肤失败绝不能影响"已保存"这个结果，故整体 try/except 兜住。
        """
        try:
            new_th = current_theme(self.app)
            if new_th is self.th:
                return
            old, self.th = self.th, new_th
            self.configure(bg=new_th["win"])
            self._apply_ttk_theme(ttk.Style(self))
            # 原生 tk widget 没有 style 机制，只能按「旧主题色 -> 新主题色」
            # 的映射递归重刷。建窗时颜色全部取自主题表，故映射是完备的。
            cmap = {old[k]: new_th[k] for k in ("win", "card", "border")}
            fmap = {old[k]: new_th[k] for k in ("fg", "sub", "accent", "danger")}
            self._recolor(self, cmap, fmap)
            for w in self._themed_windows():
                w.refresh_theme()
        except Exception:
            pass

    @classmethod
    def _recolor(cls, w, cmap, fmap):
        """递归把 widget 树里命中旧主题色的 bg / fg 换成新色。

        ttk 控件的颜色由 style 管，cget 读不到这些值，自然不会命中映射表，
        所以这里只影响原生 tk widget —— 不需要区分控件类型。
        """
        try:
            keys = w.keys()
        except Exception:
            return
        for opt, mapping in (("bg", cmap), ("fg", fmap)):
            if opt in keys:
                try:
                    cur = str(w.cget(opt))
                    if cur in mapping:
                        w.configure(**{opt: mapping[cur]})
                except Exception:
                    pass
        try:
            for child in w.winfo_children():
                cls._recolor(child, cmap, fmap)
        except Exception:
            pass

    def _themed_windows(self):
        """当前还开着的、支持换肤的窗口（便签 + 所有截图对照窗）。"""
        app = self.app
        wins = []
        note = getattr(app, "note_win", None)
        if note is not None and not getattr(note, "closed", False):
            wins.append(note)
        for w in (getattr(app, "pin_wins", None) or []):
            if w is not None and not getattr(w, "closed", False):
                wins.append(w)
        return wins

    def _test(self):
        self._save()
        self._msg("正在测试…")
        text = "Artificial intelligence is reshaping the software industry."
        try:
            out, engine, ok = self.app.translator.translate(text)
        except Exception as exc:
            out, engine, ok = str(exc), "-", False
        self._msg(("✔ " if ok else "✘ ") + f"{engine}：{out[:120]}", error=not ok)
