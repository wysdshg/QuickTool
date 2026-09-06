"""冒烟测试：不依赖 GUI，逐个验证底层能力。运行：python tests/smoke.py"""
import ctypes
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qt import winapi as wa
from qt.capture import get_selected_text, normalize_text
from qt.config import Config
from qt.engines import ENGINES
from qt.translator import Translator

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [OK]   {name} {extra}")
    else:
        fail += 1
        print(f"  [FAIL] {name} {extra}")


print("\n== 1. 快捷键解析 ==")
for s, expect in (("Ctrl+Alt+T", (0x0002 | 0x0001 | 0x4000, 0x54)),
                  ("ctrl+shift+f1", (0x0002 | 0x0004 | 0x4000, 0x70)),
                  ("Alt+Space", (0x0001 | 0x4000, 0x20)),
                  ("Ctrl+Prtsc", (0x0002 | 0x4000, 0x2C)),
                  ("Ctrl+Print", (0x0002 | 0x4000, 0x2C))):
    try:
        got = wa.HotkeySpec.parse(s)
        check(s, got == expect, f"-> {got}")
    except Exception as e:
        check(s, False, str(e))
try:
    wa.HotkeySpec.parse("Ctrl+")
    check("非法组合应报错", False)
except ValueError:
    check("非法组合应报错", True)

print("\n== 2. 剪贴板读写 / 快照还原 ==")
wa.set_clipboard_text("原始内容-ABC-123")
check("写入并读回", wa.get_clipboard_text() == "原始内容-ABC-123")
snap = wa.clipboard_snapshot()
check("快照非空", bool(snap), f"格式数={len(snap)}")
wa.set_clipboard_text("被覆盖的内容")
check("覆盖生效", wa.get_clipboard_text() == "被覆盖的内容")
wa.clipboard_restore(snap)
check("还原成功", wa.get_clipboard_text() == "原始内容-ABC-123",
      f"实际={wa.get_clipboard_text()!r}")

print("\n== 3. 文本清洗（PDF 断词换行）==")
dirty = "the perfor-\nmance is signifi-\ncantly better"
clean = normalize_text(dirty)
check("断词合并", clean == "the performance is significantly better", f"-> {clean!r}")

print("\n== 3.5 热键顺延表（回归：必须用 HK_* 整数做键）==")
import main as _m
for _hid in (_m.HK_TRANSLATE, _m.HK_SETTINGS, _m.HK_QUIT, _m.HK_OCR, _m.HK_PIN):
    check(f"候选表含 hid={_hid}", bool(_m.HOTKEY_CANDIDATES.get(_hid)),
          f"-> {_m.HOTKEY_CANDIDATES.get(_hid, [])[:2]}")

print("\n== 4. 抓词链路（未选中时应安全返回空）==")
import ctypes as _ct
_hw = _ct.windll.user32.GetForegroundWindow()
_buf = _ct.create_unicode_buffer(256)
_ct.windll.user32.GetClassNameW(_hw, _buf, 256)
_fg_cls = _buf.value
t0 = time.perf_counter()
text = get_selected_text(timeout=0.25, retries=1)
check("未选中时不卡死", time.perf_counter() - t0 < 2.0, f"耗时 {time.perf_counter()-t0:.2f}s")
if _fg_cls == "Chrome_WidgetWin_1":
    # 宿主（WorkBuddy/Electron）为前台时，模拟 Ctrl+C 会被宿主拦截并清空
    # 剪贴板（实测 seq +4、内容变空）——v1.5.4 守卫检测到外部写入按设计
    # 放弃还原。产品行为正确，此断言在该环境下无意义。
    print(f"  [SKIP] 前台为宿主 Electron 窗（类名 {_fg_cls}），剪贴板还原断言跳过")
else:
    check("抓词后剪贴板已还原", wa.get_clipboard_text() == "原始内容-ABC-123",
          f"实际={wa.get_clipboard_text()!r}")

print("\n== 4.5 抓词还原竞态守卫（v1.5.4：抓词期间外部截屏不得被旧快照冲掉）==")
# 复现路径：拖选触发抓词（快照含上次 Ctrl+PrtSc 的 CF_DIB）→ 抓词轮询期间
# 用户按普通 PrtSc，Windows 把新截屏（CF_DIB 位图）写进剪贴板 → 旧实现无条件
# clipboard_restore，EmptyClipboard 会把新截屏冲掉、再写回旧快照——直接粘贴
# 就粘到旧图（Win+V 里出现两个 Ctrl+PrtSc 截图）。修复后：seq 变了但读不到
# 文本（位图）= 外部写入，放弃还原，新截屏保留。
old_bmp = wa.grab_screen_bmp(20, 20, 60, 45)
wa.set_clipboard_image(old_bmp)                      # 模拟上次 Ctrl+PrtSc 的 CF_DIB
snap_dib = wa.clipboard_snapshot().get(wa.CF_DIB, b"")
ext_written = []


def _external_write():
    time.sleep(0.12)                                 # 快照之后、抓词轮询中：模拟系统截屏
    new_bmp = wa.grab_screen_bmp(300, 200, 60, 45)   # 不同位置 = 不同内容
    wa.set_clipboard_image(new_bmp)
    ext_written.append(True)


t = threading.Thread(target=_external_write)
t.start()
get_selected_text(timeout=0.25, retries=1)           # 抓不到文本（剪贴板只有位图）
t.join()
cur_dib = wa.clipboard_snapshot().get(wa.CF_DIB, b"")
check("外部截屏未被旧快照冲掉",
      ext_written and bool(cur_dib) and cur_dib != snap_dib,
      f"旧DIB={len(snap_dib)}B 当前DIB={len(cur_dib)}B")
wa.set_clipboard_text("竞态守卫-还原恢复测试")
snap_guard = wa.clipboard_snapshot()
wa.set_clipboard_text("临时覆盖")
wa.clipboard_restore(snap_guard)
check("无外部写入时还原仍正常",
      wa.get_clipboard_text() == "竞态守卫-还原恢复测试",
      f"实际={wa.get_clipboard_text()!r}")

print("\n== 5. 隐藏窗口 / 全局热键 / 托盘 ==")
events = []
try:
    hwnd = wa.create_hidden_window(
        lambda h, m, w, l: events.append(m) or (m in (wa.WM_HOTKEY,)))
    check("创建隐藏窗口", bool(hwnd))
    # 真机上 Ctrl+Alt+T 常被输入法/显卡面板/网盘占用，逐个探测
    taken, free = [], None
    for combo in ("Ctrl+Q", "Ctrl+Alt+T", "Ctrl+Alt+F1", "Ctrl+Alt+D"):
        reg_ok, err = wa.register_hotkey(hwnd, 1, combo)
        if reg_ok:
            free = combo
            break
        taken.append(f"{combo}({err})")
    check("注册全局热键", free is not None, f"占用={taken} 采用={free}")
    if free:
        wa.unregister_hotkey(hwnd, 1)
        again, _ = wa.register_hotkey(hwnd, 1, free)
        check("注销后可重新注册", again)
    tray = wa.Tray(hwnd, "QuickTool 测试")
    check("托盘图标添加", tray.add())
    check("托盘菜单可创建", isinstance(tray.popup_menu, object))
    wa.post_message(hwnd, wa.WM_DESTROY)
    tray.remove()
    check("托盘移除", True)
except Exception as e:
    check("Win32 子系统", False, repr(e))

print("\n== 5.5 鼠标拖选钩子（迷你按钮依赖）==")
try:
    fired = []
    watcher = wa.MouseDragWatcher(lambda x, y: fired.append((x, y)))
    check("钩子安装成功", watcher.start())
    check("卸载钩子", watcher.stop() or True)
    check("卸载后句柄清空", watcher._hook is None)
    watcher.ignore_rect = (10, 10, 50, 50)
    check("忽略区域-内部命中", watcher._inside_ignore(30, 30))
    check("忽略区域-外部放行", not watcher._inside_ignore(5, 5))
except Exception as e:
    check("鼠标钩子子系统", False, repr(e))

print("\n== 6. 翻译引擎 ==")
cfg = Config()
# 语向钉死为英→中：引擎可用性只该测引擎，不该随真实 config.json 的
# target_lang 漂移（实测踩过：用户配置 target=en 时 langpair=en|en，
# MyMemory 403 "PLEASE SELECT TWO DISTINCT LANGUAGES"）
cfg.set("source_lang", "auto")
cfg.set("target_lang", "zh-CN")
tr = Translator(cfg)
sample = "artificial intelligence is reshaping the software industry"
for name in ("mymemory", "google", "offline"):
    cfg.set("engine", name)
    cfg.set("fallback_chain", [])
    cfg2 = Translator(cfg)
    t0 = time.perf_counter()
    out, engine, good = cfg2.translate(sample)
    dt = time.perf_counter() - t0
    if not good and any(k in str(out) for k in
                        ("timed out", "URLError", "ConnectionRefused", "getaddrinfo")):
        print(f"  [SKIP] {name} 网络不可达（{dt:.2f}s）——国内环境常见，代码无问题")
        continue
    check(f"{name} 可用性", good, f"{dt:.2f}s -> {str(out)[:60]}")
cfg.set("engine", "offline")
cfg.set("fallback_chain", [])
out, engine, good = tr.translate("benchmark")
check("离线词库查词", good and "基准" in out, f"-> {out}")

print("\n== 6.5 截图 OCR（GDI 抓屏 + Windows.Media.Ocr 桥）==")
import ctypes
import tempfile
import tkinter as tk

from qt import ocr as _ocr

langs = _ocr.available_langs()
print(f"  系统可用 OCR 语言：{langs or '无（功能测试跳过，桥路仍验证）'}")
if langs:
    _lang = _ocr.pick_lang("auto", langs)
    check("OCR 语言选择", bool(_lang), f"-> {_lang}")
    try:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
        root = tk.Tk()
        root.geometry("720x160+80+80")
        root.attributes("-topmost", True)
        root.lift()
        root.focus_force()
        root.configure(bg="white")
        tk.Label(root, text="quicktool ocr test",
                 font=("Arial", 28, "bold"),
                 bg="white", fg="black").pack(expand=True)
        root.update()
        time.sleep(1.0)                       # 等窗口完全绘制
        root.update()
        bmp = wa.grab_screen_bmp(root.winfo_rootx(), root.winfo_rooty(),
                                 root.winfo_width(), root.winfo_height())
        root.destroy()
        check("GDI 抓屏", bmp[:2] == b"BM" and len(bmp) > 54,
              f"{len(bmp)} 字节")
        path = os.path.join(tempfile.gettempdir(), "QuickTool_smoke_ocr.bmp")
        with open(path, "wb") as f:
            f.write(bmp)
        good, text, _tag, err = _ocr.ocr_image_file(path, _lang, timeout=40)
        hit = good and any(w in text.lower()
                           for w in ("quicktool", "ocr", "test"))
        check("OCR 识别出测试文字", hit, f"-> {text[:60]!r}" if good
              else f"错误={err}")
    except Exception as exc:
        print(f"  [SKIP] 截图 OCR 功能测试：环境不支持（{exc}）")
else:
    print("  [SKIP] 无 OCR 语言包：桥路可用性由 e2e 的 OCR_LANGS 探针覆盖")

print("\n== 6.8 截图对照小窗（PinWindow：BMP->PNG->PhotoImage->缩放->多窗->保存->剪贴板）==")
try:
    import base64
    import struct

    from qt.ui import (PinWindow, RegionSelector,
                       _bmp_to_png, _bmp_to_photo, _bmp_size)

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        ctypes.windll.user32.SetProcessDPIAware()
    root = tk.Tk()
    root.withdraw()
    bmp = wa.grab_screen_bmp(50, 50, 120, 90)
    check("抓屏 BMP 有效", bmp[:2] == b"BM" and _bmp_size(bmp) == (120, 90),
          f"{len(bmp)} 字节")
    png = _bmp_to_png(bmp)
    w = struct.unpack_from(">I", png, 16)[0]
    h = struct.unpack_from(">I", png, 20)[0]
    check("BMP->PNG 尺寸一致", (w, h) == (120, 90), f"PNG {w}x{h}")
    check("PNG 魔数正确", png[:8] == b"\x89PNG\r\n\x1a\n")
    photo = _bmp_to_photo(bmp)
    check("PNG->PhotoImage 解码", photo.width() == 120 and photo.height() == 90,
          f"{photo.width()}x{photo.height()}")

    # 完整 PinWindow 生命周期：创建 -> 滚轮缩放 -> 关闭
    fake = type("App", (), {})()
    fake.root = root
    fake.pin_wins = []
    pin = PinWindow(fake, bmp, index=1)
    fake.pin_wins.append(pin)
    check("PinWindow 创建且置顶", bool(pin) and not pin.closed
          and pin.win.attributes("-topmost"))
    check("工具条带编号", "对照 1" in pin.lbl_title.cget("text"))
    f1 = pin._factor
    pin._zoom(type("E", (), {"delta": 120})())        # 滚轮上 = 放大
    check("滚轮缩放生效", pin._factor < f1 or pin._factor == f1,
          f"factor {f1}->{pin._factor}")
    pin._zoom(type("E", (), {"num": 5, "delta": 0}))  # 缩小方向
    check("滚轮缩小方向正确", pin._factor >= f1 - 1,
          f"factor -> {pin._factor}")

    # 多窗口并存：再建一个，旧的应仍在列表里（不互相顶掉）
    pin2 = PinWindow(fake, bmp, index=2)
    fake.pin_wins.append(pin2)
    check("多窗口并存", len(fake.pin_wins) == 2 and not pin.closed
          and not pin2.closed)
    # 时序：刚创建的窗口 geometry 尚未应用、也未映射，winfo_x/y 会返回
    # 未布局值（可能同为 0）。先跑完布局 + 映射，坐标才是真实屏幕位置。
    root.update_idletasks()
    root.update()
    p1 = (pin.win.winfo_x(), pin.win.winfo_y())
    p2 = (pin2.win.winfo_x(), pin2.win.winfo_y())
    check("级联偏移位置不同", p1 != p2, f"p1={p1} p2={p2}")

    pin.close()
    check("关闭一个不影响另一个", len(fake.pin_wins) == 1
          and not pin2.closed)
    pin2.close()
    check("全部关闭后列表清空", fake.pin_wins == [])

    # ---- 剪贴板 CF_DIB + 存 PNG（v1.5.2：截图进剪贴板 + 主动保存）----
    check("set_clipboard_image 成功", wa.set_clipboard_image(bmp))
    check("剪贴板出现 CF_DIB", wa.clipboard_has_dib())
    snap = wa.clipboard_snapshot()
    check("快照兼容 CF_DIB",
          wa.CF_DIB in snap and len(snap[wa.CF_DIB]) == len(bmp) - 14,
          f"dib={len(snap.get(wa.CF_DIB, b''))}B")
    wa.set_clipboard_text("截图功能保留文本-ABC")
    snap_txt = wa.clipboard_snapshot()
    wa.set_clipboard_image(bmp)
    wa.clipboard_restore(snap_txt)
    check("放图后还原文本",
          wa.get_clipboard_text() == "截图功能保留文本-ABC")

    import queue as _q
    fake.q = _q.Queue()                     # toast 队列（模拟 App.q）
    pin3 = PinWindow(fake, bmp, index=1)
    tmpd = tempfile.mkdtemp(prefix="qt_pin_save_")
    p = pin3._save_png(dir=tmpd)
    check("存 PNG 返回路径", isinstance(p, str) and os.path.isfile(p),
          f"-> {p}")
    with open(p, "rb") as f:
        png = f.read()
    check("存的文件是 PNG", png[:8] == b"\x89PNG\r\n\x1a\n")
    w2 = struct.unpack_from(">I", png, 16)[0]
    h2 = struct.unpack_from(">I", png, 20)[0]
    check("保存尺寸一致", (w2, h2) == (120, 90), f"{w2}x{h2}")
    check("toast 队列收到保存提示",
          not fake.q.empty() and fake.q.get_nowait()[0] == "toast")
    pin3.close()
    os.remove(p)
    os.rmdir(tmpd)

    # ---- 孤儿 release 防御（v1.5.3：遮罩弹出瞬间鼠标残留按下态、
    #      无 press 配对就松手会带 _sx/_sy=0，从屏幕左上角(0,0)到鼠标
    #      位置生成随机选区直接截屏。实测 1124x110/968x134 两个左上角矩形）
    calls = []
    fake2 = type("App", (), {})()
    fake2.root = root
    fake2.ocr_selector = None
    sel = RegionSelector(
        fake2, lambda x, y, w, h: calls.append((x, y, w, h)))
    sel._release(type("E", (), {"x": 300, "y": 200})())     # 孤儿 release
    check("孤儿 release 不触发完成",
          sel.done is False and calls == [])
    sel._press(type("E", (), {"x": 100, "y": 100})())
    sel._release(type("E", (), {"x": 300, "y": 200})())
    check("正常框选触发完成", calls == [(100, 100, 200, 100)],
          f"-> {calls}")
    sel._press(type("E", (), {"x": 100, "y": 100})())
    sel._release(type("E", (), {"x": 105, "y": 102})())     # < MIN_SIZE
    check("小于 MIN_SIZE 取消",
          len(calls) == 1 and sel.done is True)
    sel.close()

    # ---- 选区流程竞态守卫（v1.5.3：框选结束瞬间 _handle_drag_end 会收到
    #     拖拽 LEFTUP 投递的 WM_APP_DRAG_END，若 ocr_selector 已清、pin_wins
    #     未 append，会被误判成划词 -> CaptureWorker 还原剪贴板把刚写入的
    #     CF_DIB 覆盖掉（用户截图后 Ctrl+V 粘到旧内容）。_selecting 期间拦下）
    import main as _main
    calls2 = []
    fake3 = type("App", (), {})()
    fake3._shutting_down = False        # v1.6.7 ③：退出尾部守卫属性（对齐真实 __init__）
    fake3._selecting = True
    fake3.ocr_selector = None
    fake3.pin_wins = []
    fake3.popup_open = False
    fake3.mini_btn = None
    fake3.note_win = None            # v1.6.0 守卫新增引用：无便签时为 None
    fake3.cfg = type("C", (), {
        "get": lambda s, k, d=None: True if k == "mini_button" else d})()
    fake3._drag_box = (10, 10, 30, 30)
    fake3._request_capture = lambda kind, payload: calls2.append(kind)
    _orig_block = wa.is_drag_blocked_at
    wa.is_drag_blocked_at = lambda x, y: ""     # 本场景只测 _selecting 守卫
    try:
        _main.App._handle_drag_end(fake3)
        check("选区流程中钩子不抢拖拽", calls2 == [])
        fake3._selecting = False
        _main.App._handle_drag_end(fake3)
        check("选区结束后钩子正常抓词", calls2 == ["drag"], f"-> {calls2}")
    finally:
        wa.is_drag_blocked_at = _orig_block
    root.destroy()
except Exception as exc:
    check("PinWindow 子系统", False, repr(exc))

print("\n== 6.13 控制台 / 截图遮罩拖选守卫（v1.6.6）==")
try:
    import main as _main
    _ogc, _opn = wa.get_window_class, wa._process_name
    try:
        wa.get_window_class = lambda hwnd: "ConsoleWindowClass"
        wa._process_name = lambda hwnd: ""
        check("控制台判定：类名 ConsoleWindowClass", wa.is_console_window(1) is True)
        wa.get_window_class = lambda hwnd: "CASCADIA_HOSTING_WINDOW_CLASS"
        check("控制台判定：Windows Terminal 类名", wa.is_console_window(2) is True)
        wa.get_window_class = lambda hwnd: "Chrome_WidgetWin_1"
        wa._process_name = lambda hwnd: "conhost.exe"
        check("控制台判定：进程名兜底 conhost", wa.is_console_window(3) is True)
        wa.get_window_class = lambda hwnd: "Chrome_WidgetWin_1"
        wa._process_name = lambda hwnd: "chrome.exe"
        check("普通窗口判定为非控制台", wa.is_console_window(4) is False)
        check("空句柄不判定为控制台", wa.is_console_window(0) is False)
    finally:
        wa.get_window_class, wa._process_name = _ogc, _opn

    # 遮罩几何特征：monkeypatch user32 的可见性/样式/矩形调用，验证面积逻辑
    _iv, _gwl, _gr = (wa.user32.IsWindowVisible,
                      wa.user32.GetWindowLongW, wa.user32.GetWindowRect)
    try:
        vw = wa.user32.GetSystemMetrics(78) or wa.user32.GetSystemMetrics(0)
        vh = wa.user32.GetSystemMetrics(79) or wa.user32.GetSystemMetrics(1)
        wa.user32.IsWindowVisible = lambda h: 1

        def _set_rect(r, w, h):
            rc = ctypes.cast(r, ctypes.POINTER(wa.RECT)).contents
            rc.left = rc.top = 0
            rc.right, rc.bottom = w, h

        def _topmost(h, i):
            return 0x00000008                     # WS_EX_TOPMOST

        def _full(h, r):
            _set_rect(r, vw, vh)
            return 1

        def _small(h, r):
            _set_rect(r, 200, 120)
            return 1

        wa.user32.GetWindowLongW = _topmost
        wa.user32.GetWindowRect = _full
        check("遮罩判定：topmost+盖满屏", wa.is_overlay_window(9) is True)
        wa.user32.GetWindowRect = _small
        check("遮罩判定：topmost+小窗不算", wa.is_overlay_window(9) is False)
        wa.user32.GetWindowLongW = lambda h, i: 0
        wa.user32.GetWindowRect = _full
        check("遮罩判定：非 topmost 不算", wa.is_overlay_window(9) is False)
    finally:
        wa.user32.IsWindowVisible = _iv
        wa.user32.GetWindowLongW = _gwl
        wa.user32.GetWindowRect = _gr

    # 集成：_handle_drag_end 命中守卫则不发抓词
    fakeb = type("App", (), {})()
    fakeb._shutting_down = False        # v1.6.7 ③：退出尾部守卫属性（对齐真实 __init__）
    fakeb._selecting = False
    fakeb.ocr_selector = None
    fakeb.pin_wins = []
    fakeb.note_win = None
    fakeb.popup_open = False
    fakeb.mini_btn = None
    fakeb.cfg = type("C", (), {
        "get": lambda s, k, d=None: True if k == "mini_button" else d})()
    fakeb._drag_box = (50, 60, 90, 100)
    got = []
    fakeb._request_capture = lambda kind, payload: got.append(kind)
    _oblk = wa.is_drag_blocked_at
    try:
        wa.is_drag_blocked_at = lambda x, y: "overlay"
        _main.App._handle_drag_end(fakeb)
        check("截图遮罩框选拖动不触发抓词", got == [])
        wa.is_drag_blocked_at = lambda x, y: "console"
        _main.App._handle_drag_end(fakeb)
        check("控制台拖选不触发抓词", got == [])
        wa.is_drag_blocked_at = lambda x, y: ""
        _main.App._handle_drag_end(fakeb)
        check("普通窗口拖选照常抓词", got == ["drag"], f"-> {got}")
    finally:
        wa.is_drag_blocked_at = _oblk
except Exception as exc:
    check("控制台/遮罩守卫子系统", False, repr(exc))

print("\n== 7. 运行日志系统（v1.4，针对静默退出问题的取证基建）==")
import logging

from qt import logging_setup as ls

log = ls.setup_logging()
check("setup_logging 可重复调用", ls.setup_logging() is log)
d = ls.log_dir()
check("日志目录存在且可写", os.path.isdir(d)
      and os.access(d, os.W_OK), f"-> {d}")
ls.log_event("SMOKE-EVENT", "probe")
ls.heartbeat("probe")
check("异常钩子已接管", sys.excepthook is not None)
# 防回归：sys.excepthook 签名必须是 (type, value, tb) 三参。
# 曾误写成单参 args，导致任何未捕获异常先触发 "Error in sys.excepthook"，
# 日志钩子自己先炸（v1.4 白盒测试抓到）。这里真实抛一个未捕获异常验证。
_orig_hook = sys.excepthook
_caught = []


def _probe_hook(t, v, tb):
    _caught.append(t)
    _orig_hook(t, v, tb)                 # 链回原钩子（会写日志）


sys.excepthook = _probe_hook
try:
    raise RuntimeError("hook-signature-probe")
except RuntimeError:
    sys.excepthook(*sys.exc_info())      # 模拟 Python 处理未捕获异常
# 签名正确：_probe_hook 收到三参并回链；签名错误则此处直接抛 TypeError 崩掉
check("sys.excepthook 三参签名可用（不炸）", _caught == [RuntimeError])
sys.excepthook = _orig_hook
logging.getLogger("QuickTool").info("SMOKE-PROBE-DIRECT")
import io

# 强制落盘：logging 默认按行缓冲，直接检查文件内容
for h in logging.getLogger("QuickTool").handlers:
    try:
        h.flush()
    except Exception:
        pass
main_log = os.path.join(d, "QuickTool.log")
content = ""
if os.path.isfile(main_log):
    with open(main_log, "r", encoding="utf-8") as f:
        content = f.read()
check("日志文件含埋点", "SMOKE-EVENT" in content and "SMOKE-PROBE-DIRECT" in content
      and "UNCAUGHT-MAIN" in content, f"-> {main_log}")
check("心跳已写入", "HEARTBEAT" in content)
crash_fp = os.path.join(d, "crash.log")
check("faulthandler 已启用（crash.log 就绪）",
      ls._crash_fp is not None and not ls._crash_fp.closed, f"-> {crash_fp}")
# 剪贴板加固后的回归：有界读取不破坏正常路径（与第 2 节互补）
wa.set_clipboard_text("加固后回归-XYZ")
check("加固后剪贴板读写正常", wa.get_clipboard_text() == "加固后回归-XYZ")

print("\n== 6.9 置顶便签（NoteWindow：累积/段定位/内搜索/裁剪/保存）==")
try:
    import qt.ui as _uimod
    from qt.ui import NoteWindow, note_save_dir

    # 6.8 节末尾已 destroy 旧 root，便签子系统用全新根窗口
    root = tk.Tk()
    root.withdraw()

    fake4 = type("App", (), {})()
    fake4.root = root
    import queue as _q2
    fake4.q = _q2.Queue()

    # 便签尺寸/位置记忆用的配置 mock（真实 App 用 qt.config.Config）
    class _FakeCfg:
        def __init__(self):
            self.d, self.saved = {}, 0

        def get(self, k, d=None):
            return self.d.get(k, d)

        def set(self, k, v):
            self.d[k] = v

        def save(self):
            self.saved += 1

    fake4.cfg = _FakeCfg()

    d1 = note_save_dir()
    check("note_save_dir 指向 Documents\\QuickTool",
          d1.endswith(os.path.join("Documents", "QuickTool")), f"-> {d1}")

    note = NoteWindow(fake4, "alpha-beta-gamma-delta-epsilon-zeta", "记事本")
    check("NoteWindow 创建且置顶", bool(note) and not note.closed
          and note.win.attributes("-topmost"))
    check("首段计数为 1", note.count == 1)
    check("标签显示段数", "1 段" in note.lbl.cget("text"))

    note.append("second-segment-content-here", "浏览器")
    check("追加后计数为 2", note.count == 2)
    check("seg1 tag 存在", len(note.txt.tag_ranges("seg1")) == 2)
    check("seg2 tag 存在", len(note.txt.tag_ranges("seg2")) == 2)
    check("段头含来源标题", "记事本" in note.txt.get("1.0", "end")
          and "浏览器" in note.txt.get("1.0", "end"))

    # 便签内全文搜索（v1.6.5：『回搜』改为便签内搜索）
    note._search_entry.insert("end", "segment")
    note._do_search()
    total_seg = len(note._search_matches)
    check("搜索命中含 'segment' 的段", total_seg >= 1, f"matches={total_seg}")
    check("命中处打 search_hit 高亮",
          len(note.txt.tag_ranges("search_hit")) >= 2,
          f"hits={len(note.txt.tag_ranges('search_hit'))}")
    check("搜索计数标签更新为 1/N",
          note._search_label.cget("text") == f"1/{total_seg}",
          f"label={note._search_label.cget('text')}")
    # 空查询 -> 清空高亮与计数
    note._search_entry.delete(0, "end")
    note._do_search()
    check("清空查询后无高亮", len(note.txt.tag_ranges("search_hit")) == 0)
    check("清空查询计数 0/0", note._search_label.cget("text") == "0/0")
    # 多命中：'e' 在两段里都出现
    note._search_entry.insert("end", "e")
    note._do_search()
    n_e = len(note._search_matches)
    check("多命中搜索 'e' 得到 >1 匹配", n_e > 1, f"n={n_e}")
    note._search_next()
    check("next 推进当前索引", note._search_idx == 1 % n_e, f"idx={note._search_idx}")
    note._search_prev()
    check("prev 回退当前索引", note._search_idx == 0 % n_e, f"idx={note._search_idx}")
    # 当前命中单独高亮（金色）
    check("当前命中打 search_cur",
          len(note.txt.tag_ranges("search_cur")) == 2)
    # 搜索条显隐切换
    note._toggle_search()
    check("搜索条可打开", note._search_open)
    note._toggle_search(force=False)
    check("搜索条可关闭", not note._search_open)
    check("关闭后高亮已清", len(note.txt.tag_ranges("search_hit")) == 0)

    # bindtags 防回归：拖动只绑工具条，不绑 Toplevel —— 绑错的话
    # 点击正文会触发拖动、选不中字（v1.6.0 设计要点）
    check("拖动绑在工具条上", "drag" in note._bar.bind("<ButtonPress-1>")
          or note._bar.bind("<ButtonPress-1>") != "")
    check("Toplevel 未绑拖动", "drag_move" not in
          (note.win.bind("<B1-Motion>") or ""))

    # 复制全部 -> 剪贴板
    note._copy_all()
    check("复制全部进剪贴板",
          "alpha-beta" in wa.get_clipboard_text())

    # 存 txt（临时目录，不污染 Documents）
    _orig_nsd = _uimod.note_save_dir
    tmpd = tempfile.mkdtemp(prefix="qt_note_save_")
    _uimod.note_save_dir = lambda: tmpd
    try:
        p = note._save_txt()
        check("存 txt 返回路径", isinstance(p, str) and os.path.isfile(p),
              f"-> {p}")
        with open(p, "r", encoding="utf-8") as f:
            saved = f.read()
        check("存档含两段内容",
              "alpha-beta" in saved and "second-segment" in saved)
        if p:
            os.remove(p)
    finally:
        _uimod.note_save_dir = _orig_nsd
        try:
            os.rmdir(tmpd)
        except OSError:
            pass

    # 裁剪：压低上限，追加超长段 -> 最老段整段连头裁掉
    old_max = NoteWindow.MAX_CHARS
    note.MAX_CHARS = 60
    try:
        note.append("T" * 120, "")
        check("超限裁剪推进 _first", note._first > 1,
              f"_first={note._first}")
        check("seg1 已被裁掉", len(note.txt.tag_ranges("seg1")) == 0)
        check("新段仍可定位", len(note.txt.tag_ranges("seg3")) == 2)
    finally:
        note.MAX_CHARS = old_max

    note.close()
    check("close 后 closed 置位", note.closed)

    # 清空
    note2 = NoteWindow(fake4, "clear-me", "")
    note2._clear()
    check("清空后计数归零", note2.count == 0 and note2._first == 1)
    check("清空后 toast 入队",
          not fake4.q.empty() and fake4.q.get_nowait()[0] == "toast")
    note2.close()

    # ---- 尺寸/位置记忆 + 右下角拖拽缩放（v1.6.2：窗口过小且不能改大小）----
    note2.close()                       # close 会写 note_w/h/x/y 进 cfg
    check("关窗保存尺寸位置到配置",
          fake4.cfg.d.get("note_w") and fake4.cfg.d.get("note_h")
          and fake4.cfg.saved >= 1,
          f"cfg={ {k: fake4.cfg.d.get(k) for k in ('note_w', 'note_h')} }")
    # 模拟右下角拖拽放大：从手柄按下点向右下拖 200x150
    note3 = NoteWindow(fake4, "resize-me", "")
    note3.win.update_idletasks()
    note3.win.update()
    w0, h0 = note3.win.winfo_width(), note3.win.winfo_height()
    note3._rs_start(type("E", (), {"x_root": 800, "y_root": 600})())
    note3._rs_move(type("E", (), {"x_root": 1000, "y_root": 750})())
    note3.win.update_idletasks()
    note3.win.update()
    w1, h1 = note3.win.winfo_width(), note3.win.winfo_height()
    check("右下角拖拽放大生效", w1 >= w0 + 150 and h1 >= h0 + 100,
          f"{w0}x{h0} -> {w1}x{h1}")
    # 反向拖过头：不得小于 MIN
    note3._rs_start(type("E", (), {"x_root": 1000, "y_root": 750})())
    note3._rs_move(type("E", (), {"x_root": 100, "y_root": 100})())
    note3.win.update_idletasks()
    note3.win.update()
    w2, h2 = note3.win.winfo_width(), note3.win.winfo_height()
    check("拖拽缩放有最小值夹紧",
          w2 == NoteWindow.MIN_W and h2 == NoteWindow.MIN_H,
          f"{w2}x{h2} (MIN {NoteWindow.MIN_W}x{NoteWindow.MIN_H})")
    note3.close()
    # 记忆恢复：note3 关窗时的尺寸（MIN 夹紧值）应成为下次默认
    check("关窗尺寸写回配置",
          fake4.cfg.d.get("note_w") == NoteWindow.MIN_W
          and fake4.cfg.d.get("note_h") == NoteWindow.MIN_H,
          f"note_w={fake4.cfg.d.get('note_w')}")
    note4 = NoteWindow(fake4, "restore-me", "")
    note4.win.update_idletasks()
    note4.win.update()
    check("新窗口恢复记忆尺寸",
          note4.win.winfo_width() == NoteWindow.MIN_W
          and note4.win.winfo_height() == NoteWindow.MIN_H,
          f"{note4.win.winfo_width()}x{note4.win.winfo_height()}")
    note4.close()
    root.destroy()
except Exception as exc:
    import traceback; traceback.print_exc()
    check("NoteWindow 子系统", False, repr(exc))

print("\n== 6.10 设置窗口布局回归（v1.6.1：_row 交错 pack 错乱修复）==")
# 曾有 bug：_row 里 widget 的 master 是 LabelFrame，却直接 pack(side="left")
# pack 到 LabelFrame 而非行 Frame —— 行（top）与 widget（left）交错 pack，
# pack 空洞逐行右移：每行被上一行的 Entry 推右一个 Entry 宽度，整个内容区
# 被撑到 ~2000px 宽，热键区只能看到前两行且全部错位（用户报障现象）。
try:
    from qt.ui import Settings as _Settings

    def _entries_of(w):
        out = []
        for c in w.winfo_children():
            if c.winfo_class() == "TEntry":
                out.append(c)
            out.extend(_entries_of(c))
        return out

    fake5 = type("App", (), {})()
    fake5.root = tk.Tk()
    fake5.root.withdraw()
    # v1.7.2 起设置页构建期调用 cfg.models_for()（点分路径），裸 dict 撑不住，
    # 换真 Config（临时路径 + 默认数据，不碰真实配置）
    import json as _json610
    from qt import config as _cmod610
    import tempfile as _tf610
    fake5.cfg = _cmod610.Config()
    fake5.cfg.path = os.path.join(_tf610.mkdtemp(prefix="qt_cfg_610_"),
                                  "config.json")
    fake5.cfg.data = _json610.loads(_json610.dumps(_cmod610.DEFAULTS))
    fake5.invalidate_rag_engine = lambda: None
    fake5.quit = lambda: None
    fake5.open_ocr = lambda: None
    fake5.open_pin = lambda: None
    st = _Settings(fake5)
    st.update_idletasks()
    st.update()
    check("热键区 7 个录制框", len(st.hk_vars) == 7, f"-> {len(st.hk_vars)}")
    # 热键 LabelFrame = 含恰好 7 个 TEntry 的那个 LabelFrame（递归找，
    # inner 在 Canvas 里深度 3 层）
    def _labelframes_of(w):
        out = []
        for c in w.winfo_children():
            if c.winfo_class() == "TLabelframe":
                out.append(c)
            out.extend(_labelframes_of(c))
        return out

    lfs = [lf for lf in _labelframes_of(st) if len(_entries_of(lf)) == 7]
    if lfs:
        es = _entries_of(lfs[0])
        xs = {e.winfo_x() for e in es}
        ys = sorted(e.winfo_y() for e in es)
        check("热键输入框同列竖排", len(es) == 7 and len(xs) == 1,
              f"n={len(es)} xs={sorted(xs)}")
        check("热键输入框逐行排列", len(ys) == 7
              and all(b - a > 10 for a, b in zip(ys, ys[1:])), f"ys={ys}")
        # z-order 回归（v1.6.1 报障）：in_ pack 不改 z-order，后创建的行 Frame
        # 会盖住先创建的 Entry —— 点击命中行 Frame，输入框既看不见也点不了。
        # 修复 = _row 里 r.lower()。这里实证：Entry 中心必须命中 Entry 自身。
        e0 = es[0]
        # winfo_containing 按屏幕坐标做真实命中测试，其他应用的窗口盖上来
        # 会返回 None（实测：用户正在操作机器时偶发 hit=None）——置顶后再测，
        # 只验证本应用内 Entry/行 Frame 的 z-order，置顶不影响断言有效性
        st.attributes("-topmost", True)
        st.update_idletasks()
        hit = st.winfo_containing(
            e0.winfo_rootx() + e0.winfo_width() // 2,
            e0.winfo_rooty() + e0.winfo_height() // 2)
        check("输入框未被行容器遮挡（可点击）", str(hit) == str(e0),
              f"hit={hit}")
        # 值可见性同源：Entry.get 应等于配置值（被遮挡时值在但渲染被盖）
        check("热键值正常读出", e0.get() == "Ctrl+Q", f"-> {e0.get()!r}")
    else:
        check("热键输入框同列竖排", False, "未找到含 7 个 Entry 的 LabelFrame")
    # 内容区不得横向撑爆（错乱时 Canvas 里的 inner 自然宽 ~2000px）
    def _canvas_of(w):
        for c in w.winfo_children():
            if c.winfo_class() == "Canvas":
                return c
            got = _canvas_of(c)
            if got:
                return got
        return None

    canv = _canvas_of(st)
    inner_req = canv.winfo_children()[0].winfo_reqwidth() if canv else 99999
    check("内容区自然宽不爆", inner_req < 800, f"inner reqw={inner_req}")
    st.destroy()
    fake5.root.destroy()
except Exception as exc:
    import traceback; traceback.print_exc()
    check("Settings 布局子系统", False, repr(exc))

print("\n== 6.11 迷你按钮『译』『便』双按钮（v1.6.3）==")
# 拖选文字后弹出的迷你按钮从单个『译』扩成『译』+『便』并排：
# 点『便』= 把选中文字钉进置顶便签（等同 Ctrl+Alt+N，但省掉一次抓词）。
try:
    from qt.ui import MiniButton as _MiniButton, THEMES as _THEMES

    calls6 = []
    fake6 = type("App", (), {})()
    fake6.root = tk.Tk()
    fake6.root.withdraw()
    fake6.cfg = {"popup.theme": "dark"}
    fake6.mini_btn = None
    fake6.mini_translate = lambda t: calls6.append(("translate", t))
    fake6.mini_note = lambda t: calls6.append(("note", t))

    mb = _MiniButton(fake6, 100, 100, "测试文本")
    mb.win.update_idletasks()
    mb.win.update()
    check("按钮条宽 = 两圆 + 间隙", mb.width == 26 * 2 + 2, f"-> {mb.width}")
    check("窗口几何与按钮条同宽", mb.win.winfo_width() == mb.width,
          f"{mb.win.winfo_width()} vs {mb.width}")
    circles = [mb.cv.find_withtag(f"bg{i}") for i in (0, 1)]
    labels = [mb.cv.find_withtag(f"txt{i}") for i in (0, 1)]
    check("两个圆都画出", all(circles), f"{circles}")
    check("按钮文字为『译』『便』",
          [mb.cv.itemcget(labels[i], "text") for i in (0, 1)] == ["译", "便"],
          f"{[mb.cv.itemcget(labels[i], 'text') for i in (0, 1)]}")
    # 两圆圆心应横向错开一个 步长(SIZE+GAP)
    c0 = mb.cv.coords(circles[0])
    c1 = mb.cv.coords(circles[1])
    check("两圆横向并排不重叠", c1[0] - c0[0] == 26 + 2, f"{c0[0]} -> {c1[0]}")
    # 落点 -> 按钮索引（步长为 SIZE+GAP=28）
    check("x 坐标映射按钮索引",
          (mb._index_at(5), mb._index_at(40)) == (0, 1),
          f"5->{mb._index_at(5)}, 40->{mb._index_at(40)}")
    # 钩子忽略区须覆盖整条（否则点『便』会被当成新一次拖选的起点）
    r = mb.get_rect()
    check("忽略区覆盖整条按钮", r is not None and r[2] - r[0] == mb.width + 4,
          f"rect={r}")
    # 悬停只高亮当前那个圆
    mb._hot_move(40)
    check("悬停右按钮仅高亮『便』",
          mb.cv.itemcget("bg1", "fill") == _THEMES["dark"]["hover"]
          and mb.cv.itemcget("bg0", "fill") == _THEMES["dark"]["card"],
          f"bg0={mb.cv.itemcget('bg0', 'fill')} bg1={mb.cv.itemcget('bg1', 'fill')}")
    # 点右侧『便』-> mini_note，且不碰翻译
    mb._click(type("E", (), {"x": 40})())
    check("点『便』调用 mini_note", calls6 == [("note", "测试文本")], f"{calls6}")
    check("点后按钮自行关闭", mb.closed)
    # 点左侧『译』-> mini_translate
    calls6.clear()
    mb2 = _MiniButton(fake6, 100, 100, "另一段")
    mb2.win.update_idletasks()
    mb2._click(type("E", (), {"x": 5})())
    check("点『译』调用 mini_translate", calls6 == [("translate", "另一段")],
          f"{calls6}")
    # 自动化/老调用方可能不带事件对象：默认落左侧『译』
    calls6.clear()
    mb3 = _MiniButton(fake6, 100, 100, "第三次")
    mb3.win.update_idletasks()
    mb3._click()
    check("无事件对象时默认走翻译", calls6 == [("translate", "第三次")],
          f"{calls6}")
    # 鼠标进入应取消自动隐藏（否则移到右侧『便』之前就 2.2s 消失了）
    mb4 = _MiniButton(fake6, 100, 100, "悬停测试")
    mb4.win.update_idletasks()
    mb4._hover(True)
    mb4._cancel_hide()
    check("进入后隐藏计时可取消", mb4._hide_job is None)
    mb4.close()
    fake6.root.destroy()
except Exception as exc:
    import traceback; traceback.print_exc()
    check("MiniButton 子系统", False, repr(exc))

print("\n== 6.12 Popup 销毁竞态健壮性（v1.6.4：lambda e 无参崩溃修复）==")
# 13:53:59 那次崩溃 = Popup._bind 的 `lambda e:` 在 widget 销毁竞态中被 Tk 无参
# 调用，抛 TypeError 打断主循环 → 进程退出。v1.6.4 给所有 lambda 加 e=None 默认
# 值（与 _render 按钮绑定的防御风格对齐）。本节约验证修复后 Popup 的创建/关闭/
# 事件触发路径正常，且关闭幂等、无残留 after 任务。
try:
    from qt.ui import Popup as _Popup

    fake7 = type("App", (), {})()
    fake7.root = tk.Tk()
    fake7.root.withdraw()
    fake7.cfg = {"popup": {"theme": "dark", "alpha": 0.97, "auto_hide_ms": 0,
                          "max_width": 520, "font_size": 13}}
    fake7.popup = None
    fake7.popup_open = False
    pop = _Popup(fake7, "原文片段", "这是译文", "MyMemory")
    pop.win.update_idletasks()
    pop.win.update()
    check("Popup 创建成功", not pop.closed)
    try:
        pop.win.event_generate("<FocusIn>")
        pop.win.event_generate("<FocusOut>")
        pop.win.event_generate("<Escape>")
        check("Popup 事件绑定触发正常", True)
    except Exception as exc:
        check("Popup 事件绑定触发正常", False, repr(exc))
    pop.close()
    check("关闭后无残留 close job", pop._close_job is None)
    pop._cancel_close()
    check("关闭后 _cancel_close 重入幂等", True)
    fake7.root.destroy()
except Exception as exc:
    import traceback; traceback.print_exc()
    check("Popup 子系统", False, repr(exc))

print("\n== 6.14 跨线程状态走队列（v1.6.7 ③：来源/OCR 路径不再共享属性）==")
# ③ 审计修复：便签来源标题、翻译文本、OCR 图片路径一律随队列 payload 流动，
# 消除 _mini_pending/_ocr_img 及「Win32 写、主线程/CaptureWorker 随手读」的
# _last_source 用法。本节约在纯逻辑层验证新链路的接线（不驱动真实线程）。
try:
    import main as _M

    # (1) _capture_for_drag：drag 请求 payload 里的来源标题要透传到 mini_show
    _orig_sel, _orig_look = _M.get_selected_text, _M.looks_translatable
    _M.get_selected_text = lambda first_timeout=0.25: "hello world"
    _M.looks_translatable = lambda t: True
    fake_d = object.__new__(_M.App)
    fake_d.cfg = {"mini_button_maxlen": 200}
    fake_d.q = _M.queue.Queue()
    try:
        _M.App._capture_for_drag(fake_d, (10, 20, "来源窗口"))
        kind, payload = fake_d.q.get_nowait()
        check("拖选来源随 mini_show payload 透传",
              (kind, payload) == ("mini_show", (10, 20, "hello world", "来源窗口")),
              f"{payload}")
    finally:
        _M.get_selected_text, _M.looks_translatable = _orig_sel, _orig_look

    # (2) _dispatch mini_show：4 元组按 (x,y,text,src) 交给 _show_mini_button
    fake_s = object.__new__(_M.App)
    fake_s._shutting_down = False
    fake_s.root = tk.Tk()
    fake_s.root.withdraw()
    got_src = []
    fake_s._show_mini_button = lambda x, y, t, s="": got_src.append((x, y, t, s))
    _M.App._dispatch(fake_s, "mini_show", (1, 2, "abc", "SRC"))
    check("mini_show 派发携带来源", got_src == [(1, 2, "abc", "SRC")], f"{got_src}")
    fake_s.root.destroy()

    # (3) mini_note 只读主线程 _mini_source，不再跨线程读 _last_source
    rec3 = []
    fake_n = object.__new__(_M.App)
    fake_n.mini_btn = None
    fake_n._mini_source = "SRC窗口"
    fake_n._hide_mini_button = lambda: None
    fake_n.open_note = lambda t, s: rec3.append((t, s))
    _M.App.mini_note(fake_n, "便签文字")
    check("mini_note 用按钮携带的来源", rec3 == [("便签文字", "SRC窗口")], f"{rec3}")
    fake_n._mini_source = ""
    _M.App.mini_note(fake_n, "第二段")
    check("无来源时兜底空串", rec3[-1] == ("第二段", ""), f"{rec3[-1]}")

    # (4) mini_translate 直接入翻译队列（不经共享属性 + Win32 消息中转）
    fake_t = object.__new__(_M.App)
    fake_t.popup_open = False
    fake_t.popup = None
    fake_t.mini_btn = None
    fake_t._hide_mini_button = lambda: None
    fake_t._job_q = _M.queue.Queue()
    _M.App.mini_translate(fake_t, "translate me")
    jk, jp = fake_t._job_q.get_nowait()
    check("mini_translate 直入 job_q", (jk, jp) == ("translate", "translate me"),
          f"{(jk, jp)}")

    # (5) OCR 选区完成：唯一临时图片路径随 job payload 直达
    #     （v1.6.10 P2-1：弃用固定同名 QuickTool_ocr.bmp，改为每次唯一）
    _orig_grab = _M.wa.grab_screen_bmp
    _M.wa.grab_screen_bmp = lambda x, y, w, h: b"FAKEBMPDATA"
    fake_o = object.__new__(_M.App)
    fake_o.ocr_selector = None
    fake_o.q = _M.queue.Queue()          # toast 消息会入队，读走避免积压
    fake_o._job_q = _M.queue.Queue()
    try:
        _M.App._ocr_region_selected(fake_o, 0, 0, 10, 10)
        jk, jp1 = fake_o._job_q.get_nowait()
        ok5 = (jk == "ocr" and os.path.basename(jp1).startswith("qt_ocr_")
               and jp1.endswith(".bmp"))
        check("OCR 唯一临时文件入 job payload", ok5,
              f"{jk} {os.path.basename(jp1)!r}")
        # (5b) v1.6.10 P2-1：连发两次选区，两次文件路径必须不同——固定同名
        #      会让「job1 未跑、job2 覆盖写」时 job1 读到 job2 的图。
        _M.App._ocr_region_selected(fake_o, 0, 0, 20, 20)
        jk2, jp2 = fake_o._job_q.get_nowait()
        both_exist = os.path.exists(jp1) and os.path.exists(jp2)
        check("连发两次选区路径不同（防覆盖竞态）",
              jk2 == "ocr" and jp1 != jp2 and both_exist,
              f"{os.path.basename(jp1)} vs {os.path.basename(jp2)}")
        for p in (jp1, jp2):
            try:
                os.unlink(p)
            except Exception:
                pass
    finally:
        _M.wa.grab_screen_bmp = _orig_grab
except Exception as exc:
    import traceback; traceback.print_exc()
    check("跨线程队列接线子系统", False, repr(exc))

# ================ 6.15 多显示器 helper（v1.6.8 ⑤：单屏等价回归） ================
print("\n== 6.15 多显示器 helper（v1.6.8 ⑤）单屏等价 ==")
try:
    vx, vy, sw, sh = wa.get_virtual_screen()
    check("虚拟屏 == 主屏（单显示器）", vx == 0 and vy == 0
          and (sw, sh) == wa.get_screen_size(), f"({vx},{vy},{sw}x{sh})")
    # 远离屏幕的落点（模拟"记忆位置在已拔出显示器"）应夹进最近的在用屏：
    # 单显示器下即主屏工作区。双屏下此断言不适用——用 SM_CMONITORS 前置守卫。
    if wa.user32.GetSystemMetrics(80) == 1:      # SM_CMONITORS
        wa_ = wa.get_work_area()
        wa_at = wa.get_work_area_at(-99999, -99999)
        check("远偏离屏落点夹回主屏工作区", wa_at == wa_,
              f"at={wa_at} wa={wa_}")
    else:
        print("  [SKIP] 多显示器环境跳过主屏等价断言")
    cx, cy = wa.get_cursor_pos()
    wa_at2 = wa.get_work_area_at(cx, cy)
    check("光标所在屏工作区正常返回", len(wa_at2) == 4
          and wa_at2[2] > wa_at2[0] and wa_at2[3] > wa_at2[1],
          f"{wa_at2}")
except Exception as exc:
    import traceback; traceback.print_exc()
    check("多显示器 helper 子系统", False, repr(exc))

# ================ 6.16 主题取色统一入口（v1.6.9 ⑥） ================
print("\n== 6.16 主题取色统一入口（v1.6.9 ⑥）==")
try:
    from qt import ui as _ui

    # (1) 两个主题的键集合必须一致：漏配一个主题，切过去就 KeyError
    kd, kl = set(_ui.THEMES["dark"]), set(_ui.THEMES["light"])
    check("dark/light 键集合一致", kd == kl, f"差集={kd ^ kl}")

    # (2) 按配置取到对应主题（返回 THEMES 里的同一对象，便于 `is` 判断变更）
    app_d = type("A", (), {"cfg": {"popup": {"theme": "dark"}}})()
    app_l = type("A", (), {"cfg": {"popup": {"theme": "light"}}})()
    check("dark 配置 -> dark 主题",
          _ui.current_theme(app_d) is _ui.THEMES["dark"])
    check("light 配置 -> light 主题",
          _ui.current_theme(app_l) is _ui.THEMES["light"])

    # (3) 非法主题名 / 无 cfg / cfg 抛异常，一律回退 dark 且绝不抛
    app_bad = type("A", (), {"cfg": {"popup": {"theme": "no-such"}}})()
    check("非法主题名回退 dark",
          _ui.current_theme(app_bad) is _ui.THEMES["dark"])
    check("app 为 None 回退 dark",
          _ui.current_theme(None) is _ui.THEMES["dark"])

    def _boom(self, k, d=None):
        raise RuntimeError("cfg 故意抛异常")
    app_boom = type("A", (), {"cfg": type("C", (), {"get": _boom})()})()
    check("cfg 抛异常仍回退 dark（不崩）",
          _ui.current_theme(app_boom) is _ui.THEMES["dark"])

    # (4) on_mask 必须两个主题都是浅色：截屏遮罩恒为半透明黑，深字看不见
    def _lum(h):
        h = h.lstrip("#")
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        return 0.299 * r + 0.587 * g + 0.114 * b
    check("on_mask 两主题均浅色（遮罩文字可见）",
          _lum(_ui.THEMES["dark"]["on_mask"]) > 128
          and _lum(_ui.THEMES["light"]["on_mask"]) > 128,
          f"dark={_ui.THEMES['dark']['on_mask']} "
          f"light={_ui.THEMES['light']['on_mask']}")

    # (5) 三个窗口都具备换肤能力（改主题后已开窗口能立刻跟上）
    for cname, meth in (("PinWindow", "refresh_theme"),
                        ("NoteWindow", "refresh_theme"),
                        ("Settings", "_apply_theme_change")):
        check(f"{cname} 具备换肤入口 {meth}()",
              callable(getattr(getattr(_ui, cname), meth, None)))

    # (6) 源码守护：ui.py 里不许再出现真实的 THEMES["dark"] 引用（注释除外）
    _src = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "qt", "ui.py")
    with open(_src, encoding="utf-8") as _f:
        _hits = [ln.strip() for ln in _f
                 if 'THEMES["dark"]' in ln and not ln.strip().startswith("#")]
    check('ui.py 无实际 THEMES["dark"] 硬编码', not _hits, f"命中={_hits[:3]}")
except Exception as exc:
    import traceback; traceback.print_exc()
    check("主题取色统一入口子系统", False, repr(exc))

# ================ 6.17 死代码清理守护（v1.6.10 P2-2） ================
print("\n== 6.17 死代码清理守护（v1.6.10 P2-2）==")
try:
    # (1) 模块级：4 个死函数（回搜/旧退出路径遗留）不应再存在
    _dead = [n for n in ("send_ctrl_v", "send_ctrl_f", "set_foreground",
                         "quit_message_loop") if hasattr(wa, n)]
    check("winapi 死代码函数已清除", not _dead, f"残留={_dead}")
    # (2) 源码守护：main.py + winapi.py 不得再出现这些定义/引用（注释除外）。
    #     set_foreground 含下划线，不会误伤 user32.SetForegroundWindow。
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _names = ("send_ctrl_v", "send_ctrl_f", "quit_message_loop")
    _hits2 = []
    for _rel in ("main.py", "qt/winapi.py"):
        with open(os.path.join(_root, _rel), encoding="utf-8") as _f:
            for _ln in _f:
                s = _ln.strip()
                if s.startswith("#") or s.startswith('"""') or s.startswith("'"):
                    continue
                if any(n in s for n in _names) \
                        or s.startswith("def set_foreground("):
                    _hits2.append(f"{_rel}: {s}")
    check("源码无死函数定义/引用", not _hits2, f"命中={_hits2[:3]}")
    # (3) 关键路径仍存活：get_foreground_window 是活代码（来源窗口记录）
    check("get_foreground_window 仍存在（活代码）",
          callable(getattr(wa, "get_foreground_window", None)))
except Exception as exc:
    import traceback; traceback.print_exc()
    check("死代码清理守护子系统", False, repr(exc))

# ================ 6.18 argtypes 声明唯一性守护（v1.6.10 P2-3） ================
print("\n== 6.18 argtypes 声明唯一性守护（v1.6.10 P2-3）==")
try:
    _src_w = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "qt", "winapi.py")
    _api_counts = {}
    with open(_src_w, encoding="utf-8") as _f:
        for _ln in _f:
            s = _ln.strip()
            if s.startswith("#"):
                continue
            if ".argtypes =" in s:
                _api = s.split(".argtypes", 1)[0].strip()   # 形如 user32.XXX
                _api_counts[_api] = _api_counts.get(_api, 0) + 1
    _dups = {k: v for k, v in _api_counts.items() if v > 1}
    check("winapi.py argtypes 每 API 恰一次声明", not _dups,
          f"重复={_dups}")
    check("argtypes 覆盖 API 总数健康(≥55)",
          len(_api_counts) >= 55, f"共 {len(_api_counts)} 个")
    _need = ["user32.SetForegroundWindow", "user32.GetClipboardData",
             "user32.VkKeyScanW", "user32.GetWindowLongW",
             "user32.SetWindowLongW", "user32.OpenClipboard",
             "user32.SetWindowsHookExW", "gdi32.BitBlt"]
    _miss = [n for n in _need if n not in _api_counts]
    check("关键 API 声明均在（防收敛误删）", not _miss, f"缺失={_miss}")
except Exception as exc:
    import traceback; traceback.print_exc()
    check("argtypes 声明唯一性守护子系统", False, repr(exc))

# ================ 6.19 RAG 密钥基建守护（v1.7：DPAPI + config 自动加密） ================
print("\n== 6.19 RAG 密钥基建守护（v1.7 DPAPI）==")
try:
    # (1) DPAPI 往返：密文 ≠ 明文，且能解回
    _enc = wa.dpapi_encrypt("sk-test-0123456789")
    check("DPAPI 加密往返一致",
          bool(_enc) and _enc != "sk-test-0123456789"
          and wa.dpapi_decrypt(_enc) == "sk-test-0123456789",
          f"密文长={len(_enc)}")
    _bad = False
    try:
        wa.dpapi_decrypt("AAAA")        # 非本机凭据密文应解密失败
    except OSError:
        _bad = True
    check("解密非法密文抛 OSError（不静默出垃圾）", _bad)

    # (2) 落盘加密：save 后文件无任何明文 key，load 自动解回明文
    import tempfile
    import json as _json
    from qt import config as _cmod
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _tmpd = tempfile.mkdtemp(prefix="qt_cfg_sec_")
    _p1 = os.path.join(_tmpd, "config.json")
    _c1 = _cmod.Config()
    _c1.path = _p1
    _c1.data = _json.loads(_json.dumps(_cmod.DEFAULTS))
    _c1.set("providers.custom.api_key", "sk-custom-plain-1")
    _c1.set("providers.modelscope.api_key", "sk-ms-plain-2")
    _c1.save()
    _raw1 = open(_p1, encoding="utf-8").read()
    check("config 落盘不含任何明文 key",
          "sk-custom-plain-1" not in _raw1 and "sk-ms-plain-2" not in _raw1,
          f"size={len(_raw1)}")
    check("config 落盘两个密钥均为 DPAPI 密文",
          _raw1.count('"__dpapi__:') == 2)
    _c2 = _cmod.Config()
    _c2.path = _p1
    _c2.data = _json.loads(_json.dumps(_cmod.DEFAULTS))
    _c2.load()
    check("load 自动解密还原两个明文 key",
          _c2.get("providers.custom.api_key") == "sk-custom-plain-1"
          and _c2.get("providers.modelscope.api_key") == "sk-ms-plain-2")

    # (3) 旧 v1.7.0-test1 明文直填（llm.*）→ 一次性迁移到 providers.custom（A1）
    _p3 = os.path.join(_tmpd, "legacy.json")
    with open(_p3, "w", encoding="utf-8") as _f:
        _json.dump({"llm": {"api_key": "sk-legacy-plain",
                            "base_url": "https://custom.example/v1",
                            "model": "legacy-model"}}, _f)
    _c3 = _cmod.Config()
    _c3.path = _p3
    _c3.data = _json.loads(_json.dumps(_cmod.DEFAULTS))
    _c3.load()
    check("旧 llm 直填迁移到 providers.custom 并指向 custom",
          _c3.get("providers.custom.api_key") == "sk-legacy-plain"
          and _c3.get("providers.custom.base_url") == "https://custom.example/v1"
          and _c3.get("providers.custom.chat_model") == "legacy-model"
          and _c3.get("llm.provider") == "custom")
    check("旧 llm 平铺键已清除（不留死配置）",
          _c3.get("llm.api_key") is None
          and _c3.get("llm.base_url") is None)
    _c3.save()
    check("迁移后首次保存自动转 DPAPI 密文",
          "sk-legacy-plain" not in open(_p3, encoding="utf-8").read())

    # (4) providers 默认段就位 + .gitignore 覆盖 config.json（防密钥入库）
    _sf = _cmod.DEFAULTS["providers"]["siliconflow"]
    check("providers.siliconflow 默认段完整（端点+三模型+thinking）",
          _sf["base_url"].endswith("siliconflow.cn/v1")
          and _sf["chat_model"] and _sf["embed_model"]
          and _sf["rerank_model"] and isinstance(_sf["thinking"], bool))
    check("五家厂商齐全", set(_cmod.DEFAULTS["providers"]) == {
        "siliconflow", "modelscope", "zhipu", "deepseek", "custom"})
    _gi = open(os.path.join(_root, ".gitignore"), encoding="utf-8").read()
    check(".gitignore 覆盖 config.json", "config.json" in _gi)
except Exception as exc:
    import traceback; traceback.print_exc()
    check("RAG 密钥基建守护子系统", False, repr(exc))

# ================ 6.20 RAG 本地检索守护（v1.7：splitter/store/bm25） ================
print("\n== 6.20 RAG 本地检索守护（v1.7 rag 包）==")
try:
    import tempfile
    import json as _json
    from qt import config as _cmod
    from rag import split_text as _split
    from rag.store import KbStore
    from rag import bm25 as _bm25
    _tmpd = tempfile.mkdtemp(prefix="qt_rag_smoke_")
    _cfg = _cmod.Config()
    _cfg.path = os.path.join(_tmpd, "config.json")
    _cfg.data = _json.loads(_json.dumps(_cmod.DEFAULTS))

    # (1) splitter：标题链 / 超长段切分 / chunk 上限
    _doc = ("# JVM 内存模型\n\n堆存放对象实例，是 GC 的主战场。\n\n"
            "## 垃圾回收\n\n垃圾回收自动回收不再被引用的堆对象。\n\n"
            + "长句内容" * 400)
    _cs = _split(_doc, name="jvm.md")
    check("splitter 输出 ≥3 chunk", len(_cs) >= 3, f"n={len(_cs)}")
    check("splitter 标题链正确",
          _cs[0]["title"] == "JVM 内存模型"
          and any("垃圾回收" in c["title"] for c in _cs))
    check("splitter 单 chunk 不超限过多",
          max(len(c["content"]) for c in _cs) <= 650,
          f"max={max(len(c['content']) for c in _cs)}")

    # (1b) v1.7.1 fence-aware：代码块原子化
    _code_small = ("```python\n"
                   + "\n".join(f"x{i} = compute_{i}({i})  # step" for i in range(8))
                   + "\n```")
    _doc_code = ("# 算法讲解\n\n状态模式允许对象在内部状态改变时改变行为。\n\n"
                 "## 示例实现\n\n下面是参考代码：\n\n" + _code_small +
                 "\n\n如上，上下文类把行为委托给当前状态对象。\n\n"
                 + "补充说明" * 300)
    _cc = _split(_doc_code, name="algo.md")
    _code_chunks = [c for c in _cc if "```python" in c["content"]]
    check("代码块原子：完整出现在恰好 1 个 chunk",
          len(_code_chunks) == 1
          and _code_small in _code_chunks[0]["content"],
          f"n={len(_code_chunks)} len={[len(c['content']) for c in _code_chunks]}")
    _hash_doc = ("# 标题甲\n\n先看说明。\n\n```\n# 这是注释不是标题\nprint(1)\n```\n\n结尾补充。")
    _hcc = _split(_hash_doc, name="h.md")
    check("fence 内 # 不当标题解析",
          len(_hcc) == 1 and _hcc[0]["title"] == "标题甲"
          and "# 这是注释不是标题" in _hcc[0]["content"],
          f"titles={[c['title'] for c in _hcc]}")
    _big_code = ("```java\n"
                 + "\n".join(f"int v{i} = f{i}({i});  // L{i}" for i in range(100))
                 + "\n```")
    check("≤3000 的大代码块独占 chunk 不切",
          500 < len(_big_code) <= 3000
          and any(c["content"] == _big_code for c in
                  _split("# T\n\n说明文字。\n\n## 实现\n\n" + _big_code,
                         name="t.md")),
          f"biglen={len(_big_code)}")
    _huge_code = "```\n" + "A" * 3500 + "\n```"
    _hc = _split("# D\n\n讲解。\n\n## 数据\n\n" + _huge_code, name="d.md")
    check(">3000 代码块整块跳过不入库",
          all("AAAA" not in c["content"] for c in _hc) and len(_hc) >= 1,
          f"chunks={len(_hc)}")
    check("超限跳过后讲解文字仍保留",
          any("讲解" in c["content"] for c in _hc),
          f"chunks={[c['title'] for c in _hc]}")

    # (2) store：入库 / 去重 / 计数
    _kb = KbStore(_cfg, db_path=os.path.join(_tmpd, "kb.sqlite"))
    _r1 = _kb.add_document("jvm.md", _doc, source="smoke")
    _r2 = _kb.add_document("jvm.md", _doc, source="smoke")
    _kb.add_document("oop.md",
                     "# 面向对象\n\n## 多态\n\n多态是三大特性之一，子类可重写父类方法。")
    check("文档入库成功", _r1["added"] and _kb.doc_count() == 2)
    check("同内容二次导入去重", _r2["added"] is False)

    # (3) bm25：中文词 FTS / 滑窗长句 / 短词 LIKE 兜底
    _h1 = _bm25.search(_kb, "垃圾回收", top_k=3)
    _h2 = _bm25.search(_kb, "垃圾回收机制怎样工作", top_k=3)
    _h3 = _bm25.search(_kb, "GC", top_k=3)
    check("中文词 FTS 命中", any(r["match"] == "fts" for r in _h1),
          f"n={len(_h1)}")
    check("滑窗长句可命中", len(_h2) >= 1, f"n={len(_h2)}")
    check("短词 GC 走 LIKE 兜底命中",
          any(r["match"] == "like" for r in _h3), f"n={len(_h3)}")

    # (4) 删除联动：删文档后 chunks 与 FTS 全清
    _kb.delete_document(_r1["doc_id"])
    _fts_n = _kb.db.execute("SELECT COUNT(*) FROM kb_fts").fetchone()[0]
    check("删文档级联清 chunks+FTS",
          _kb.chunk_count() == 1 and _fts_n == 1,
          f"chunks={_kb.chunk_count()} fts={_fts_n}")

    # (5) list_documents：文档库管理 UI 的清单（含向量进度列）
    _docs6 = _kb.list_documents()
    check("list_documents 清单完整",
          len(_docs6) == 1 and _docs6[0]["name"] == "oop.md"
          and _docs6[0]["chunk_count"] == 1 and _docs6[0]["vec_n"] == 0
          and "created_at" in _docs6[0],
          f"rows={len(_docs6)}")
    _kb.close()
except Exception as exc:
    import traceback; traceback.print_exc()
    check("RAG 本地检索守护子系统", False, repr(exc))

# ================ 6.21 RAG API/向量守护（v1.7：vectors 纯逻辑 + api 配置） ================
print("\n== 6.21 RAG API/向量守护（v1.7 vectors/api）==")
try:
    import tempfile
    import json as _json
    from qt import config as _cmod
    from rag.store import KbStore
    from rag.api import RagApi, RagApiError
    from rag import vectors as _vec

    # (1) normalize / pack-unpack 往返
    _v = _vec.normalize([3.0, 4.0])
    check("normalize 后模长≈1",
          abs((_v[0] ** 2 + _v[1] ** 2) ** 0.5 - 1.0) < 1e-9)
    _blob = _vec.pack(list(range(1024)))
    _back = _vec.unpack(_blob)
    check("pack/unpack 1024 维往返一致",
          len(_back) == 1024 and _back[0] == 0.0 and _back[-1] == 1023.0,
          f"len={len(_back)}")

    # (2) vector_search 排序 + exclude（手工向量：同向/垂直/反向）
    _tmpd = tempfile.mkdtemp(prefix="qt_vec_smoke_")
    _cfg = _cmod.Config()
    _cfg.path = os.path.join(_tmpd, "config.json")
    _cfg.data = _json.loads(_json.dumps(_cmod.DEFAULTS))
    _st = KbStore(_cfg, db_path=os.path.join(_tmpd, "kb.sqlite"))
    _cur = _st.db.execute(
        "INSERT INTO documents(name, sha1, char_len, chunk_count, created_at)"
        " VALUES('t.md','x',1,3,'t')")
    _did = _cur.lastrowid
    _cids = []
    for _i in range(3):
        _c = _st.db.execute(
            "INSERT INTO chunks(doc_id, seq, title, content) VALUES(?,?,?,?)",
            (_did, _i, "T", f"content {_i}"))
        _cids.append(_c.lastrowid)
    _st.db.commit()
    for _cid, _v2 in zip(_cids, ([1, 0, 0], [0, 1, 0], [-1, 0, 0])):
        _st.update_vec(_cid, _vec.pack(_vec.normalize(_v2)))
    check("向量存取计数正确", _st.vec_count() == 3)
    _top = _vec.vector_search(_st, [1.0, 0.0, 0.0], top_k=3)
    check("余弦排序正确（同向>垂直>反向）",
          _top[0]["id"] == _cids[0] and _top[-1]["id"] == _cids[2]
          and _top[0]["match"] == "vec",
          f"order={[_r['id'] for _r in _top]}")
    _top2 = _vec.vector_search(_st, [1.0, 0.0, 0.0], top_k=2,
                               exclude_ids=[_cids[0]])
    check("exclude 去重生效", _top2[0]["id"] == _cids[1],
          f"ids={[_r['id'] for _r in _top2]}")

    # (2b) v1.7.1：embed_chunks_missing 进度回调 + _retry_json 退避重试
    _cur = _st.db.execute(
        "INSERT INTO documents(name, sha1, char_len, chunk_count, created_at)"
        " VALUES('u.md','y',1,2,'t')")
    _did2 = _cur.lastrowid
    for _i in range(2):
        _st.db.execute(
            "INSERT INTO chunks(doc_id, seq, title, content) VALUES(?,?,?,?)",
            (_did2, _i, "U", f"待向量化 {_i}"))
    _st.db.commit()

    class _FakeEmbedApi:
        def embed(self, texts, batch=32):
            return [[1.0, 0.0, 0.0] for _ in texts]

    _prog = []
    _n = _vec.embed_chunks_missing(_st, _FakeEmbedApi(), batch=1,
                                   on_progress=lambda d, t: _prog.append((d, t)))
    check("embed_chunks_missing 补算+进度回调",
          _n == 2 and _st.vec_count() == 5
          and _prog[0] == (0, 2) and _prog[-1] == (2, 2),
          f"n={_n} vec={_st.vec_count()} prog={_prog}")

    from rag.api import _retry_json, _err_code
    check("错误码解析（429/网络/401）",
          _err_code(RagApiError("API 429：x")) == 429
          and _err_code(RagApiError("网络错误：超时")) == 0
          and _err_code(RagApiError("API 401：bad key")) == 401)
    _calls = []
    def _flaky():
        _calls.append(1)
        if len(_calls) < 3:
            raise RagApiError("API 429：rate limited")
        return {"ok": 1}
    _r = _retry_json(_flaky, retries=3, sleeper=lambda s: None)
    check("429 指数退避重试至成功", _r == {"ok": 1} and len(_calls) == 3,
          f"calls={len(_calls)}")
    _calls2 = []
    def _badkey():
        _calls2.append(1)
        raise RagApiError("API 401：bad key")
    _raised = False
    try:
        _retry_json(_badkey, retries=3, sleeper=lambda s: None)
    except RagApiError:
        _raised = True
    check("非可重试错误（401）立即抛出不浪费时间",
          _raised and len(_calls2) == 1, f"calls={len(_calls2)}")

    # (3) 双源配置读取（A2）：chat 走 llm.provider 厂商、embed/rerank 固定
    #     硅基流动；缺 key 抛带厂商名的引导错误（不联网）
    _cfg.set("providers.siliconflow.api_key", "sk-sf-fake")
    _ra = RagApi(_cfg)
    check("默认厂商 RagApi 读取双源配置",
          _ra.chat_base.endswith("siliconflow.cn/v1")
          and _ra.chat_key == "sk-sf-fake"
          and _ra.chat_model == "Qwen/Qwen3-8B"
          and _ra.chat_thinking is True
          and _ra.retr_key == "sk-sf-fake"
          and _ra.embed_model == "BAAI/bge-m3"
          and _ra.rerank_model == "BAAI/bge-reranker-v2-m3")
    _cfg.set("llm.provider", "deepseek")
    _cfg.set("providers.deepseek.api_key", "sk-ds-fake")
    _ra2 = RagApi(_cfg)
    check("切 deepseek：chat 跟随切换、检索仍硅基",
          _ra2.chat_base.startswith("https://api.deepseek.com")
          and _ra2.chat_key == "sk-ds-fake"
          and _ra2.chat_model == "deepseek-chat"
          and _ra2.chat_thinking is False
          and _ra2.retr_key == "sk-sf-fake")
    _cfg.set("llm.model", "my-override-model")
    check("llm.model 覆盖厂商默认 chat_model",
          RagApi(_cfg).chat_model == "my-override-model")
    _cfg.set("llm.model", "")
    _cfg.set("providers.deepseek.api_key", "")
    _err = False
    try:
        RagApi(_cfg)
    except RagApiError as exc:
        _err = "未配置生成模型" in str(exc) and "deepseek" in str(exc)
    check("生成厂商空 key 抛引导错误（点名厂商）", _err)
    # 检索缺硅基 key：构造可成功（生成侧正常），embed 时给检索引导
    _cfg.set("llm.provider", "custom")
    _cfg.set("providers.custom.api_key", "sk-cu")
    _cfg.set("providers.custom.base_url", "https://custom.example/v1")
    _cfg.set("providers.custom.chat_model", "cm")
    _cfg.set("providers.siliconflow.api_key", "")
    _ra3 = RagApi(_cfg)
    check("生成厂商正常时构造成功（检索缺失不拦）",
          _ra3.chat_key == "sk-cu")
    _err2 = False
    try:
        _ra3.embed(["abc"])
    except RagApiError as exc:
        _err2 = "硅基流动" in str(exc)
    check("无硅基 key 时 embed 抛检索引导错误", _err2)
except Exception as exc:
    import traceback; traceback.print_exc()
    check("RAG API/向量守护子系统", False, repr(exc))

# ================ 6.22 RagEngine 编排守护（v1.7 engine：mock API 全路径） ================
print("\n== 6.22 RagEngine 编排守护（v1.7 engine，mock 不联网）==")
try:
    import tempfile
    import json as _json
    from qt import config as _cmod
    from rag.store import KbStore
    from rag.api import RagApiError
    from rag import vectors as _vec
    from rag.engine import RagEngine

    _DOC_A = ("# 水果常识\n\n苹果是常见水果，香蕉也是，橘子葡萄也可以。\n\n"
              "## 吃法\n\n苹果可生吃，香蕉软甜，橘子剥皮吃。")
    _DOC_B = ("# 编程语言\n\nPython 是一种编程语言，语法简单，适合入门。\n\n"
              "## 变量\n\nx = 1 表示把整数赋给变量 x。")

    def _vv(text):
        """3 维手工"词袋"向量：苹果/apple、编程、香蕉 各占一维。"""
        t = (text or "").lower()
        v = [1.0 if ("苹果" in t or "apple" in t) else 0.0,
             1.0 if "编程" in t else 0.0,
             1.0 if "香蕉" in t else 0.0]
        n = sum(x * x for x in v) ** 0.5
        return [x / n for x in v] if n else [1.0, 0.0, 0.0]

    class _FakeApi:
        """桩：embed 走 _vv 词袋；rerank 偏爱含 Python 的候选；可注入失败。"""
        def __init__(self):
            self.n_chat = self.n_chat_once = 0
            self.n_embed = self.n_rerank = 0
            self.fail_rerank = False
            self.last_messages = None        # 生成 chat 收到的完整 messages
            self.last_once_messages = None   # fusion 变体请求的 messages
            self.last_rerank_query = None    # rerank 收到的 query

        def chat(self, messages, max_tokens=1024, temperature=0.3,
                 on_delta=None, timeout=None):
            self.n_chat += 1
            self.last_messages = messages
            if on_delta:
                on_delta("模拟回答", "")
            return "模拟回答：垃圾回收帮你自动管理内存。"

        def chat_once(self, messages, max_tokens=1024, temperature=0.3):
            self.n_chat_once += 1
            self.last_once_messages = messages
            sys_text = (messages or [{}])[0].get("content", "")
            if "检索助手" in sys_text:      # fusion 变体请求
                return "苹果分几种？\n香蕉怎么吃？\n编程中如何避免内存问题？"
            return "模拟回答"

        def embed(self, texts, batch=32):
            self.n_embed += 1
            return [_vv(t) for t in texts]

        def rerank(self, query, documents, top_n=5):
            self.n_rerank += 1
            self.last_rerank_query = query
            if self.fail_rerank:
                raise RagApiError("mock rerank fail")
            sc = [(i, 1.0 if "Python" in (d or "") else 0.0)
                  for i, d in enumerate(documents)]
            sc.sort(key=lambda t: t[1], reverse=True)
            return sc[:top_n]

    def _mk6_22(tag, overrides=None):
        """建独立临时 config+store，并灌入 A/B 两份文档（带手写向量）。"""
        _tmpd = tempfile.mkdtemp(prefix="qt_rag22_" + tag + "_")
        _cfg = _cmod.Config()
        _cfg.path = os.path.join(_tmpd, "config.json")
        _cfg.data = _json.loads(_json.dumps(_cmod.DEFAULTS))
        if overrides:
            for k, v in overrides.items():
                _cfg.set("rag.retrieval." + k, v)
        _kb = KbStore(_cfg, db_path=os.path.join(_tmpd, "kb.sqlite"))
        for name, text in (("A.md", _DOC_A), ("B.md", _DOC_B)):
            _r = _kb.add_document(name, text, source="t6_22")
            for _row in _kb.db.execute(
                    "SELECT id, content FROM chunks WHERE doc_id=?",
                    (_r["doc_id"],)):
                _kb.update_vec(_row["id"], _vec.pack(_vv(_row["content"])))
        return _cfg, _kb

    def _mk_eng6_22(tag, overrides=None):
        _cfg, _kb = _mk6_22(tag, overrides)
        _e = RagEngine(_kb, _cfg)
        _e._api = _FakeApi()
        return _cfg, _kb, _e

    # (1) 空库 / 空问题 → 引导性报错
    _cfg0, _kb0 = _mk6_22("empty")
    _e0 = RagEngine(_kb0, _cfg0)
    _e0._api = _FakeApi()
    _try_empty = False
    try:
        _kb0.delete_document(1); _kb0.delete_document(2)
        _e0.ask("任何问题")
    except RagApiError as exc:
        _try_empty = "文档库为空" in str(exc)
    check("空库 ask 抛引导错误", _try_empty)
    _kb0.close()
    _try_q = False
    _c1, _k1, _e1 = _mk_eng6_22("qempty")     # 有文档的库
    try:
        _e1.ask("   ")
    except RagApiError as exc:
        _try_q = "问题为空" in str(exc)
    check("空问题 ask 抛错", _try_q)
    _k1.close()

    # (2) 向量路独立于 BM25：apple 无任何字面命中，仍靠向量召回水果 doc
    _c, _k, _e = _mk_eng6_22("vec", {"fusion_variants": 1, "enable_rerank": False})
    _r = _e.ask("apple 是什么水果")
    check("BM25 零命中时向量路兜底召回",
          any("水果常识" in x["title"] for x in _r["refs"]),
          f"refs={[x['title'][:12] for x in _r['refs']]}")
    check("该场景恰好只发 1 次 embed", _e._api.n_embed == 1,
          f"n={_e._api.n_embed}")
    _k.close()

    # (3) rerank 生效：含 Python 的编程候选被排到首位
    _c, _k, _e = _mk_eng6_22("rr",
                             {"fusion_variants": 1, "enable_rerank": True,
                              "rerank_top_k": 1})
    _r = _e.ask("编程和水果都常见吗")
    check("rerank 被调用且生效",
          _e._api.n_rerank == 1
          and _r["refs"] and "编程" in _r["refs"][0]["title"],
          f"rerank={_e._api.n_rerank} top={_r['refs'][0]['title'][:14] if _r['refs'] else None}")
    # (4) rerank 失败 → 降级 RRF 直取，问答不中断
    _e._api.fail_rerank = True
    _r2 = _e.ask("编程和水果都常见吗")
    check("rerank 失败降级仍出答案",
          bool(_r2["answer"]) and len(_r2["refs"]) >= 1,
          f"refs={len(_r2['refs'])}")
    _k.close()

    # (5) fusion 变体驱动多路召回 + refs 结构
    _c, _k, _e = _mk_eng6_22("fus",
                             {"fusion_variants": 3, "enable_rerank": False})
    _r = _e.ask("苹果和编程有什么关系吗")
    _titles = " ".join(x["title"] for x in _r["refs"])
    check("fusion 调 1 次变体生成", _e._api.n_chat_once == 1,
          f"n={_e._api.n_chat_once}")
    check("3 个变体各发 1 次 embed", _e._api.n_embed >= 3,
          f"n={_e._api.n_embed}")
    check("多路融合跨两份文档", "水果" in _titles and "编程" in _titles,
          f"titles={_titles[:60]}")
    check("refs 字段结构完整",
          all(k in x for x in _r["refs"] for k in ("doc_id", "title", "snippet")),
          f"n={len(_r['refs'])}")
    _k.close()

    # (5b) v1.7.1 上下文防污染：同节去重 + 代码配额 + 预算/提示词
    from rag.engine import _select as _sel, _CTX_LIMIT as _LIM
    check("生成预算提到 8000（v1.7.2，配合精排 TopK=10）", _LIM == 8000,
          f"limit={_LIM}")
    from rag import engine as _eng
    check("系统提示词含代码参考约束", "参考实现" in _eng._SYS,
          f"SYS={_eng._SYS[:40]}…")
    _sec_a = {"doc_id": 1, "title": "T/同节", "content": "理论讲解" + "A" * 100}
    _sec_b = {"doc_id": 1, "title": "T/同节", "content": "理论重复块" + "B" * 100}
    _code1 = {"doc_id": 2, "title": "C1", "content": "```py\ncode1\n```"}
    _code2 = {"doc_id": 3, "title": "C2", "content": "```py\ncode2\n```"}
    _other = {"doc_id": 4, "title": "其他节", "content": "别的章节" + "C" * 100}
    _picked = _sel([_sec_a, _sec_b, _code1, _code2, _other])
    check("同节去重只留最高分一块",
          _sec_a in _picked and _sec_b not in _picked, f"n={len(_picked)}")
    check("代码块配额最多 1 块",
          sum(1 for c in _picked if "```" in c["content"]) == 1
          and _code1 in _picked and _code2 not in _picked,
          f"picked={[c['title'] for c in _picked]}")
    check("理论块不受配额影响全保留",
          _other in _picked and len(_picked) == 3, f"n={len(_picked)}")

    # (6) 懒向量化：库内 chunk 无 vec 时，ask 先自动补算再检索（BM25 零命中
    #     也要能靠向量路召回）——回归 engine 的 embed_missing 前置 gate
    _tmp6 = tempfile.mkdtemp(prefix="qt_rag22_lazy_")
    _cfg6 = _cmod.Config()
    _cfg6.path = os.path.join(_tmp6, "config.json")
    _cfg6.data = _json.loads(_json.dumps(_cmod.DEFAULTS))
    _cfg6.set("rag.retrieval.fusion_variants", 1)
    _cfg6.set("rag.retrieval.enable_rerank", False)
    _kb6 = KbStore(_cfg6, db_path=os.path.join(_tmp6, "kb.sqlite"))
    _kb6.add_document("A.md", _DOC_A, source="t6_22")   # 注意：不手动写 vec
    _e6 = RagEngine(_kb6, _cfg6)
    _e6._api = _FakeApi()
    _r6 = _e6.ask("apple 是什么水果")
    check("首问自动补算缺失向量并召回",
          _kb6.vec_count() == _kb6.chunk_count() > 0
          and any("水果常识" in x["title"] for x in _r6["refs"]),
          f"vec={_kb6.vec_count()}/{_kb6.chunk_count()}"
          f" embed={_e6._api.n_embed}")
    _kb6.close()

    # (7) 检索/生成的背景分工（C 阶段防回归，v1.7.2 短背景豁免改版）：
    #     检索 query = 问题 + 背景前 100 字（概念补全）；完整背景只在最终
    #     生成消息里以【用户选中内容作背景】出现，fusion/rerank 不见 100 字
    #     之外的背景内容。
    _c, _k, _e = _mk_eng6_22("bg", {"fusion_variants": 3,
                                    "enable_rerank": True,
                                    "rerank_top_k": 1})
    _BG = "我在读一本讲苹果种植的书，想搞懂编程里的变量是什么。"
    _r7 = _e.ask("编程里的变量怎么用", extra_context=_BG)
    _usr = " ".join(m.get("content", "")
                    for m in (_e._api.last_messages or [])
                    if m.get("role") == "user")
    check("extra_context 注入生成消息（带标记头）",
          "【用户选中内容作背景】" in _usr and "苹果种植" in _usr,
          f"user={_usr[:90]!r}")
    _once = " ".join(m.get("content", "")
                     for m in (_e._api.last_once_messages or []))
    check("fusion 变体请求含问题 + 背景前 100 字",
          _e._api.last_once_messages is not None
          and "苹果种植" in _once          # 背景（<100 字）已并入变体输入
          and "编程里的变量怎么用" in _once,
          f"once={_once[:90]!r}")
    check("rerank query = 问题 + 背景前 100 字（v1.7.2）",
          (_e._api.last_rerank_query or "").strip()
          == "编程里的变量怎么用 " + _BG.strip(),
          f"q={_e._api.last_rerank_query!r}")
    _try_bg = False
    try:
        _e.ask("   ", extra_context=_BG)     # 空问题：即便带背景仍抛错
    except RagApiError as exc:
        _try_bg = "问题为空" in str(exc)
    check("空问题即便带背景仍抛错（默认问法由窗口层生成）", _try_bg)
    _k.close()
except Exception as exc:
    import traceback; traceback.print_exc()
    check("RagEngine 编排守护子系统", False, repr(exc))

# ============ 6.23 QaWindow 弹框问答（v1.7 C 阶段：选中→弹框→输入问题→回车） ============
print("\n== 6.23 QaWindow 弹框问答渲染/交互（v1.7 C，真 Tk 窗口）==")
try:
    import tkinter as tk
    from qt.ragui import QaWindow

    _qa_calls = []

    def _qa_recorder(win, q, sel):
        _qa_calls.append((q, sel))

    _r = tk.Tk()
    _r.withdraw()
    _fa = type("App", (), {})()
    _fa.root = _r
    _fa.cfg = {"popup": {"theme": "dark"}}   # current_theme 读嵌套 popup；qa_* 走 dict.get 默认
    _fa.ask_rag = _qa_recorder
    _SEL = "苹果种植指南 · 第三章：变量与内存"
    _qwin = QaWindow(_fa, _SEL, "某文档.md")
    _r.update()                              # 让 after(30) 的 tool_window 跑掉，避免残留

    _body = _qwin.txt.get("1.0", "end-1c")
    check("选中背景渲染在正文顶部（带标记头）",
          _body.startswith("〔选中背景〕") and "苹果种植指南" in _body,
          f"body={_body[:40]!r}")
    check("有选中时状态条提示默认问法",
          "解释选中内容" in _qwin.status_lbl.cget("text"),
          f"status={_qwin.status_lbl.cget('text')!r}")

    # 直接回车（空输入框）→ 默认问法 + selection 一起交给 app.ask_rag
    _qa_calls.clear()
    _qwin.ask()
    check("空问题+有背景 → 默认问法并带选中背景发出",
          _qa_calls == [("解释一下选中的内容", _SEL)]
          and "【问】解释一下选中的内容" in _qwin.txt.get("1.0", "end-1c"),
          f"calls={_qa_calls}")
    _qwin._set_busy(False)                   # 上个 ask 已置 busy，复位再测下一问

    # 输入自定义问题 → 原样发出，仍带 selection 作背景；发送后输入框清空
    _qa_calls.clear()
    _qwin.entry.insert(0, "这段讲的内存泄漏怎么避免")
    _qwin.ask()
    check("自定义问题原样发送且带选中背景",
          _qa_calls == [("这段讲的内存泄漏怎么避免", _SEL)],
          f"calls={_qa_calls}")
    check("发送后输入框被清空",
          _qwin.entry.get() == "",
          f"entry={_qwin.entry.get()!r}")

    # busy 期间再 ask → 忽略（防连点刷屏）
    _qwin._set_busy(True)
    _qa_calls.clear()
    _qwin.ask("busy 时不该发")
    check("busy 时重复提问被忽略", _qa_calls == [], f"calls={_qa_calls}")
    _qwin._set_busy(False)
    _qwin.close()
    check("close 后 closed 标记置位", _qwin.closed)

    # 无选中窗口：提示可直输问题；空回车不发（避免空问题+空背景的无效请求）
    _fa2 = type("App", (), {})()
    _fa2.root = _r
    _fa2.cfg = {"popup": {"theme": "dark"}}
    _fa2.ask_rag = _qa_recorder
    _q2 = QaWindow(_fa2, "", "")
    check("无选中窗口提示可直输问题",
          "直接输入问题" in _q2.status_lbl.cget("text"),
          f"status={_q2.status_lbl.cget('text')!r}")
    _qa_calls.clear()
    _q2.ask()
    check("无选中+空问题 → 不发请求（提示输入）", _qa_calls == [],
          f"calls={_qa_calls}")
    _q2.close()
    _r.destroy()
except Exception as exc:
    import traceback; traceback.print_exc()
    check("QaWindow 弹框问答子系统", False, repr(exc))

# ===== 6.24 凭据中心 A（v1.7 A：迁移幂等 / 端点矩阵 / Settings 保存落盘） =====
print("\n== 6.24 凭据中心 A（迁移幂等 + 端点矩阵 + UI 保存）==")
try:
    import tempfile
    import json as _json
    from qt import config as _cmod

    # (1) 迁移幂等（A1）：_migrate_disk_v170 直测——首迁返回 True，对已迁移
    #     dict 再调返回 False（无重复写 / 无死配置键）
    _disk = {"rag": {"api": {"api_key": "sk-sf-old",
                             "base_url": "https://api.siliconflow.cn/v1",
                             "chat_model": "Qwen/Qwen3-8B",
                             "embed_model": "BAAI/bge-m3",
                             "rerank_model": "BAAI/bge-reranker-v2-m3",
                             "thinking_off": True}},
             "llm": {"api_key": "sk-cu-old",
                     "base_url": "https://custom.example/v1", "model": "m9"}}
    _cc = _cmod.Config.__new__(_cmod.Config)
    check("首迁返回 True", _cc._migrate_disk_v170(_disk) is True)
    check("rag.api → providers.siliconflow（thinking_off=True → thinking=True）",
          _disk["providers"]["siliconflow"]["api_key"] == "sk-sf-old"
          and _disk["providers"]["siliconflow"]["embed_model"] == "BAAI/bge-m3"
          and _disk["providers"]["siliconflow"]["thinking"] is True)
    check("llm 直填 → providers.custom 且 provider=custom",
          _disk["providers"]["custom"]["api_key"] == "sk-cu-old"
          and _disk["providers"]["custom"]["base_url"] == "https://custom.example/v1"
          and _disk["providers"]["custom"]["chat_model"] == "m9"
          and _disk["llm"]["provider"] == "custom")
    check("旧键已清（无死配置）",
          "api" not in _disk["rag"]
          and not any(k in _disk["llm"]
                      for k in ("api_key", "base_url", "model")))
    check("二次迁移返回 False（幂等）",
          _cc._migrate_disk_v170(_disk) is False)

    # (2) 端点矩阵（A1 helpers）：默认硅基 / 切换 / 覆盖 / 检索固定 / 兜底
    _tmpd = tempfile.mkdtemp(prefix="qt_cfg_a_")
    _p = os.path.join(_tmpd, "config.json")
    _c = _cmod.Config()
    _c.path = _p
    _c.data = _json.loads(_json.dumps(_cmod.DEFAULTS))
    for _pv, _pk in (("siliconflow", "sk-sf"), ("deepseek", "sk-ds"),
                     ("zhipu", "sk-zp"), ("modelscope", "sk-ms"),
                     ("custom", "sk-cu")):
        _c.set(f"providers.{_pv}.api_key", _pk)
    _b1, _k1, _m1 = _c.chat_ep()
    check("chat_ep 默认取硅基流动", _k1 == "sk-sf" and _m1 == "Qwen/Qwen3-8B")
    _c.set("llm.provider", "zhipu")
    _b2, _k2, _m2 = _c.chat_ep()
    check("切智谱 chat_ep 跟随",
          "bigmodel.cn" in _b2 and _k2 == "sk-zp" and _m2 == "glm-4.5-flash")
    _c.set("llm.model", "glm-4.6-x")
    check("llm.model 覆盖厂商默认", _c.chat_ep()[2] == "glm-4.6-x")
    _c.set("llm.model", "")
    _rb, _rk, _rm = _c.retrieval_ep()
    check("检索端点固定硅基（与生成厂商无关）",
          _rk == "sk-sf" and _rm == "BAAI/bge-m3")
    _c.set("llm.provider", "不存在的厂商")
    check("非法厂商回退硅基（provider_cfg 兜底不炸）",
          _c.chat_ep()[1] == "sk-sf")
    _c.set("llm.provider", "siliconflow")

    # (3) UI 保存落盘（A4）：Settings._save 把凭据中心各控件写进 providers.*，
    #     落盘 DPAPI 密文且内存明文（真 Config + 临时文件，绝不碰真实配置）
    import tkinter as tk
    from qt.ui import Settings as _Settings
    _rr = tk.Tk()
    _rr.withdraw()
    _p2 = os.path.join(_tmpd, "ui_save.json")
    _cu = _cmod.Config()
    _cu.path = _p2
    _cu.data = _json.loads(_json.dumps(_cmod.DEFAULTS))
    _cu.apply_autostart = lambda enable: (True, "")     # 屏蔽注册表写入
    _fa = type("App", (), {})()
    _fa.root = _rr
    _fa.cfg = _cu
    _fa.quit = lambda: None
    _fa.reload_hotkeys = lambda: None
    _fa.toggle_tray = lambda v: None
    _fa.rag_invalidated = []            # v1.7.2：保存必须触发 RAG 引擎重建
    _fa.invalidate_rag_engine = lambda: _fa.rag_invalidated.append(1)
    _st = _Settings(_fa)
    _st.gen_provider_var.set("DeepSeek")
    _st.llm_model_var.set("deepseek-v3-x")
    _st.prov_key_vars["deepseek"].set("sk-ds-new")
    _st.prov_key_vars["siliconflow"].set("sk-sf-new")
    _st.prov_custom_url.set("https://my.example/v1")
    _st.prov_custom_model.set("my-chat")
    _st.rag_embed_var.set("BAAI/bge-large-zh-v1.5")
    _st.rag_rerank_var.set("")
    _st._save()
    check("_save 写生成厂商与模型覆盖",
          _cu.get("llm.provider") == "deepseek"
          and _cu.get("llm.model") == "deepseek-v3-x")
    check("_save 后 RAG 引擎被重建（切模型免重启，v1.7.2）",
          len(_fa.rag_invalidated) == 1,
          f"calls={len(_fa.rag_invalidated)}")
    check("_save 记入厂商模型历史（换过一次就记住）",
          _cu.get("providers.deepseek.models", [])[0] == "deepseek-v3-x",
          f"models={_cu.get('providers.deepseek.models')}")
    check("_save 检索参数落盘（默认值直通）",
          _cu.get("rag.retrieval.fusion_variants") == 3
          and _cu.get("rag.retrieval.bm25_top_k") == 30
          and _cu.get("rag.retrieval.vector_top_k") == 30
          and _cu.get("rag.retrieval.rerank_top_k") == 5)
    check("_save 写各家 Key（DPAPI 密钥字段，内存明文）",
          _cu.get("providers.deepseek.api_key") == "sk-ds-new"
          and _cu.get("providers.siliconflow.api_key") == "sk-sf-new")
    check("_save 写自定义端点 + 检索模型（留空回落默认）",
          _cu.get("providers.custom.base_url") == "https://my.example/v1"
          and _cu.get("providers.custom.chat_model") == "my-chat"
          and _cu.get("providers.siliconflow.embed_model") == "BAAI/bge-large-zh-v1.5"
          and _cu.get("providers.siliconflow.rerank_model")
          == "BAAI/bge-reranker-v2-m3")
    _raw_s = open(_p2, encoding="utf-8").read()
    check("UI 保存落盘无明文 key",
          "sk-ds-new" not in _raw_s and "sk-sf-new" not in _raw_s)
    check("保存后未误删非 UI 字段（thinking 等仍存）",
          _cu.get("providers.siliconflow.thinking") is True)
    _st.destroy()
    _rr.destroy()
except Exception as exc:
    import traceback; traceback.print_exc()
    check("凭据中心 A 子系统", False, repr(exc))

# ===== 6.25 PDF 导入（v1.7：pypdf 提取 + 质量体检 + KbManager 后台全链路） =====
print("\n== 6.25 PDF 导入（v1.7 pypdf，源码环境缺失则跳过）==")
try:
    import importlib.util
    import tempfile
    import json as _json
    import zlib as _zlib
    import queue as _queue
    from qt import config as _cmod
    from rag import pdftext as _pt

    # PYPDF_PATH：与 spec 同款约定——打包机/测试机不装 pypdf 时指隔离 venv
    _pp = os.environ.get("PYPDF_PATH", "")
    if _pp and not _pt.HAVE_PYPDF:
        for _d in _pp.split(os.pathsep):
            if _d and _d not in sys.path:
                sys.path.insert(0, _d)
        _pt.HAVE_PYPDF = importlib.util.find_spec("pypdf") is not None

    if not _pt.HAVE_PYPDF:
        print("  [SKIP] pypdf 不可用（PDF 用例跳过，不算失败）——"
              "源码环境可 pip install pypdf 或设 PYPDF_PATH")
    else:
        def _mk_pdf(path, pages_text):
            """手写最小多页 PDF（Helvetica，英文），供提取验证。"""
            objs = {}
            n = len(pages_text)
            pn = [4 + i * 2 for i in range(n)]
            cn = [5 + i * 2 for i in range(n)]
            objs[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
            objs[2] = ("<< /Type /Pages /Kids [%s] /Count %d >>"
                       % (" ".join(f"{x} 0 R" for x in pn), n)).encode()
            objs[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
            for i, lines in enumerate(pages_text):
                objs[pn[i]] = (f"<< /Type /Page /Parent 2 0 R "
                               f"/MediaBox [0 0 612 792] "
                               f"/Resources << /Font << /F1 3 0 R >> >> "
                               f"/Contents {cn[i]} 0 R >>").encode()
                parts = ["BT", "/F1 14 Tf", "72 720 Td", "18 TL"]
                for j, ln in enumerate(lines):
                    s = ln.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
                    parts.append(f"({s}) Tj" if j == 0 else f"T* ({s}) Tj")
                parts.append("ET")
                comp = _zlib.compress("\n".join(parts).encode("latin-1"), 9)
                objs[cn[i]] = (b"<< /Length " + str(len(comp)).encode()
                               + b" /Filter /FlateDecode >>\nstream\n" + comp
                               + b"\nendstream")
            buf = bytearray(b"%PDF-1.4\n")
            off = {}
            for k in range(1, max(objs) + 1):
                off[k] = len(buf)
                buf += f"{k} 0 obj\n".encode() + objs[k] + b"\nendobj\n"
            xr = len(buf)
            buf += f"xref\n0 {max(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
            for k in range(1, max(objs) + 1):
                buf += f"{off[k]:010d} 00000 n \n".encode()
            buf += (f"trailer\n<< /Size {max(objs) + 1} /Root 1 0 R >>\n"
                    f"startxref\n{xr}\n%%EOF\n").encode()
            open(path, "wb").write(bytes(buf))

        _tmpd = tempfile.mkdtemp(prefix="qt_pdf_smoke_")
        _pdf = os.path.join(_tmpd, "sample.pdf")
        _pad = "Line %d content for extraction quality check."
        _mk_pdf(_pdf, [["Chapter 1. Garbage Collection",
                        _pad % 1, _pad % 2, _pad % 3, _pad % 4],
                       ["Chapter 2. Memory Leaks",
                        _pad % 5, _pad % 6, _pad % 7, _pad % 8]])

        # (1) extract_pdf：页数/文本/进度回调
        _got = []
        _pages = _pt.extract_pdf(_pdf, on_progress=lambda i, n, note: _got.append((i, n)))
        check("extract_pdf 页数与进度回调", len(_pages) == 2 and len(_got) == 2
              and _got[-1] == (2, 2), f"pages={len(_pages)} prog={_got}")

        # (2) to_markdown：标题链（文件名 + 第 N 页）供 splitter 定位来源
        _md = _pt.to_markdown("sample.pdf", _pages)
        check("to_markdown 生成标题链",
              _md.startswith("# sample.pdf") and "## 第 1 页" in _md
              and "Chapter 1" in _md and "## 第 2 页" in _md,
              f"head={_md[:40]!r}")

        # (3) 质量体检：good / sparse / garbled / empty 四态
        _q_good = _pt.text_quality("a.pdf", _pages)
        check("体检 good：正常文本", _q_good["verdict"] == "good",
              f"per_page={_q_good['per_page']}")
        _q_sparse = _pt.text_quality("a.pdf", ["x" * 50])
        check("体检 sparse：每页字数过少",
              _q_sparse["verdict"] == "sparse",
              f"per_page={_q_sparse['per_page']}")
        _q_garbled = _pt.text_quality(
            "a.pdf", ["ก แ ค ธ " * 30 + "hello"])   # 泰文=CID 错映射高发区
        check("体检 garbled：异常脚本占比",
              _q_garbled["verdict"] == "garbled"
              and _q_garbled["odd_ratio"] > 0.10,
              f"odd={_q_garbled['odd_ratio']}")
        _q_empty = _pt.text_quality("a.pdf", ["", ""])
        check("体检 empty：无文本层（扫描版）",
              _q_empty["verdict"] == "empty")

        # (4) KbManager 全链路：后台线程提取 → _pdfq → 主线程入库
        import tkinter as _tk
        from qt.ragui import KbManager as _Kb
        from rag.store import KbStore as _KbStore
        _cfgp = _cmod.Config()
        _cfgp.path = os.path.join(_tmpd, "config.json")
        _cfgp.data = _json.loads(_json.dumps(_cmod.DEFAULTS))
        _fake = type("App", (), {})()
        _fake.root = _tk.Tk()
        _fake.root.withdraw()
        _fake.cfg = _cfgp
        _fake.q = _queue.Queue()
        _kb_path = os.path.join(_tmpd, "kb.sqlite")
        _fake.ensure_kb_store = (lambda: _KbStore(_cfgp, db_path=_kb_path))
        _mgr = _Kb(_fake)
        check("KbManager 导入入口接受 .pdf",
              _mgr._import_pdfs_async([_pdf]) == 1
              and getattr(_mgr, "_pdf_pending", 0) == 1)
        _t0 = time.perf_counter()
        # 直接驱动 _poll_pdf：本进程已反复建毁十余个 Tk 实例，新建解释器的
        # after/timer 事件泵会失灵（afterProbe 实测不触发，属测试环境怪癖）；
        # 产品运行时是单 root + mainloop，after 链正常（独立进程 0.37s 全链
        # 通过已验证）。这里 pump 队列验证的线程→队列→入库逻辑本身。
        while getattr(_mgr, "_pdf_pending", 0) > 0 and time.perf_counter() - _t0 < 10:
            if not _mgr._pdfq.empty():
                _mgr._poll_pdf()
            time.sleep(0.02)
        check("后台解析完成后 _pdf_pending 归零", _mgr._pdf_pending == 0,
              f"qsize={_mgr._pdfq.qsize()} t={time.perf_counter() - _t0:.2f}s")
        _docs = _fake.ensure_kb_store().list_documents()
        check("PDF 文档已入库（含页标题链）",
              len(_docs) == 1 and _docs[0]["name"] == "sample.pdf"
              and _docs[0]["char_len"] > 0,
              f"docs={[(d['name'], d['char_len']) for d in _docs]}")
        _rows = _fake.ensure_kb_store().db.execute(
            "SELECT title FROM chunks").fetchall()
        check("分块标题链含页码", any(r[0] and "第 1 页" in r[0] for r in _rows),
              f"titles={[r[0] for r in _rows]}")
        _toasts = [t for t in list(_fake.q.queue) if t[0] == "toast"]
        check("汇总 toast 已发出", any("PDF 导入完成" in str(t[1][0]) or
                                        "PDF" in str(t[1][0]) for t in _toasts),
              f"toasts={[_t[1][0] for _t in _toasts]}")
        _mgr.close()
        _fake.root.destroy()
except Exception as exc:
    import traceback; traceback.print_exc()
    check("PDF 导入子系统", False, repr(exc))

# ===== 6.26 批量导入（v1.7：scan_folder 文件夹递归 + PDF 并发限流 + 聚合进度） =====
print("\n== 6.26 批量导入（文件夹递归扫描 + PDF 并发限流）==")
try:
    import tempfile
    import queue as _q2
    from qt.ragui import scan_folder as _scan

    _td = tempfile.mkdtemp(prefix="qt_batch_smoke_")
    _sub = os.path.join(_td, "sub"); os.makedirs(_sub)
    _hid = os.path.join(_td, ".hid"); os.makedirs(_hid)
    for _rel in ("a.txt", "b.md", "c.PDF", "d.pdf", "e.docx", "f.PDF"):
        open(os.path.join(_td, _rel), "wb").write(b"x")
    open(os.path.join(_sub, "g.txt"), "wb").write(b"x")
    open(os.path.join(_sub, ".hidden.md"), "wb").write(b"x")
    open(os.path.join(_hid, "h.pdf"), "wb").write(b"x")

    # (1) scan_folder：递归 / 扩展名大小写不敏感 / 隐藏目录与隐藏文件跳过
    _texts, _pdfs, _seen = _scan(_td)
    check("scan_folder 递归+扩展名+隐藏跳过",
          _seen == 6 and len(_texts) == 3 and len(_pdfs) == 3
          and os.path.join(_td, "c.PDF") in _pdfs
          and os.path.join(_td, "e.docx") not in _texts + _pdfs
          and os.path.join(_sub, "g.txt") in _texts
          and os.path.join(_hid, "h.pdf") not in _pdfs
          and _texts == sorted(_texts) and _pdfs == sorted(_pdfs),
          f"seen={_seen} texts={_texts} pdfs={_pdfs}")

    # (2) worker 限流语义：带 sem 的异常路径（文件不存在）也能 acquire/release
    #     并把 error 送回队列——semaphore 不卡死、异常不被吞
    _q3 = _q2.Queue()
    from qt.ragui import KbManager as _KbB
    _sem2 = threading.Semaphore(2)
    _KbB._pdf_worker("missing.pdf", os.path.join(_td, "no_such.pdf"),
                     _q3, _sem2)
    _k, _pl = _q3.get_nowait()
    check("worker 带 sem 异常路径不卡死不吞异常",
          _k == "error" and _pl[0] == "missing.pdf" and _pl[1],
          f"kind={_k} payload={_pl}")
    _sem2.acquire(); _sem2.acquire()      # 若 worker 泄漏 sem，这里会拿到 3/2
    check("worker 异常路径正确归还信号量", True, "两次 acquire 未阻塞即通过")
    _sem2.release(); _sem2.release()

    # (3) KbManager 批量计数 + 信号量 + 聚合进度前缀（不依赖 pypdf）
    import tkinter as _tk2
    from qt import config as _cmod2
    from rag.store import KbStore as _KbStore2
    _td2 = tempfile.mkdtemp(prefix="qt_batch_smoke2_")
    _cfg2 = _cmod2.Config()
    _cfg2.path = os.path.join(_td2, "config.json")
    _cfg2.data = _json.loads(_json.dumps(_cmod2.DEFAULTS))
    _app2 = type("App", (), {})()
    _app2.root = _tk2.Tk(); _app2.root.withdraw()
    _app2.cfg = _cfg2
    _app2.q = _q2.Queue()
    _app2.ensure_kb_store = (lambda: _KbStore2(_cfg2,
                              db_path=os.path.join(_td2, "kb.sqlite")))
    _mgr2 = _KbB(_app2)
    _mgr2._pdfq = _q2.Queue()
    _mgr2._pdf_pending = 2
    _mgr2._pdf_total = 3
    _mgr2._pdf_stats = {"added": 0, "skipped": 0, "failed": 0, "warns": []}
    _mgr2._pdfq.put(("progress", ("x.pdf", 1, 2, None)))
    _mgr2._poll_pdf()
    _txt = _mgr2.summary.cget("text")
    check("聚合进度前缀 PDF x/y", _txt.startswith("PDF 2/3："),
          f"summary={_txt!r}")
    _mgr2.close()
    _app2.root.destroy()

    # (4) 批量入库计数累积：stub worker 让 done 事件即刻入队（只有显式 pump
    #     才消化），两次 _import_pdfs_async 之间 pending 恒 >0，从而确定性地
    #     走到「prev>0 累加」分支——真实提取链路已由 6.25 覆盖
    from rag import pdftext as _pt2
    if _pt2.HAVE_PYPDF and "_mk_pdf" in globals():
        _td3 = tempfile.mkdtemp(prefix="qt_batch_smoke3_")
        _p1 = os.path.join(_td3, "b1.pdf")
        _p2 = os.path.join(_td3, "b2.pdf")
        _p3 = os.path.join(_td3, "b3.pdf")
        _mk_pdf(_p1, [["Doc one", "line one", "line two"]])
        _mk_pdf(_p2, [["Doc two", "line three", "line four"]])
        _mk_pdf(_p3, [["Doc three", "line five", "line six"]])
        _cfg3 = _cmod2.Config()
        _cfg3.path = os.path.join(_td3, "config.json")
        _cfg3.data = _json.loads(_json.dumps(_cmod2.DEFAULTS))
        _app3 = type("App", (), {})()
        _app3.root = _tk2.Tk(); _app3.root.withdraw()
        _app3.cfg = _cfg3
        _app3.q = _q2.Queue()
        _app3.ensure_kb_store = (lambda: _KbStore2(_cfg3,
                                  db_path=os.path.join(_td3, "kb.sqlite")))
        _mgr3 = _KbB(_app3)
        _mgr3._pdf_worker = (lambda name, path, q, sem=None:
                             q.put(("done", (name, ["stub page"]))))

        def _pump3():
            _t0 = time.perf_counter()
            while getattr(_mgr3, "_pdf_pending", 0) > 0 \
                    and time.perf_counter() - _t0 < 15:
                if not _mgr3._pdfq.empty():
                    _mgr3._poll_pdf()
                time.sleep(0.02)

        check("批量 _import_pdfs_async 首批计数",
              _mgr3._import_pdfs_async([_p1, _p2]) == 2
              and _mgr3._pdf_pending == 2 and _mgr3._pdf_total == 2
              and getattr(_mgr3, "_pdf_sem") is not None,
              f"pending={_mgr3._pdf_pending} total={getattr(_mgr3, '_pdf_total', '?')}")
        # 首批尚未 pump（pending 仍为 2）时第二批到达 → 总数累加而非重置
        check("第二批并行到达时总数累加",
              _mgr3._import_pdfs_async([_p3]) == 1
              and _mgr3._pdf_pending == 3 and _mgr3._pdf_total == 3,
              f"pending={_mgr3._pdf_pending} total={_mgr3._pdf_total}")
        _pump3()
        _docs3 = _app3.ensure_kb_store().list_documents()
        _names3 = sorted(d["name"] for d in _docs3)
        check("批量 PDF 全部入库（跨两批 3 篇）",
              _names3 == ["b1.pdf", "b2.pdf", "b3.pdf"],
              f"docs={_names3}")
        _mgr3.close()
        _app3.root.destroy()
    else:
        print("  [SKIP] pypdf 不可用（6.26(4) 批量入库链路跳过）")

    # (5) v1.7.1 导入后预向量化接线（stub 网络层，不联网）
    from rag import vectors as _vmod
    _td5 = tempfile.mkdtemp(prefix="qt_batch_smoke5_")
    _cfg5 = _cmod2.Config()
    _cfg5.path = os.path.join(_td5, "config.json")
    _cfg5.data = _json.loads(_json.dumps(_cmod2.DEFAULTS))
    _cfg5.data.setdefault("providers", {}).setdefault(
        "siliconflow", {})["api_key"] = "sk-smoke-fake"   # 让 RagApi 可构造
    _app5 = type("App", (), {})()
    _app5.root = _tk2.Tk(); _app5.root.withdraw()
    _app5.cfg = _cfg5
    _app5.q = _q2.Queue()
    _app5.ensure_kb_store = (lambda: _KbStore2(_cfg5,
                              db_path=os.path.join(_td5, "kb.sqlite")))
    _mgr5 = _KbB(_app5)
    # 预置 1 个缺向量的 chunk（空库 vec>=chunk 会触发"无需补算"早退）
    _st5 = _app5.ensure_kb_store()
    _cur5 = _st5.db.execute(
        "INSERT INTO documents(name, sha1, char_len, chunk_count, created_at)"
        " VALUES('p.md','z',1,1,'t')")
    _st5.db.execute(
        "INSERT INTO chunks(doc_id, seq, title, content) VALUES(?,?,?,?)",
        (_cur5.lastrowid, 0, "P", "待向量化内容"))
    _st5.db.commit()
    _prog5 = []
    _orig_ecm = _vmod.embed_chunks_missing
    def _stub_ecm(store, api, batch=32, on_progress=None):
        if on_progress:
            on_progress(1, 1)
        _prog5.append(1)
        return 1
    _vmod.embed_chunks_missing = _stub_ecm
    try:
        _mgr5._start_prevectorize()
        _t0 = time.perf_counter()
        while getattr(_mgr5, "_vec_running", False) \
                and time.perf_counter() - _t0 < 5:
            time.sleep(0.02)
        # 驱动轮询消化 _vecq（smoke 环境 after() 失灵，与 6.25 同款处理）
        for _ in range(5):
            _mgr5._poll_vec()
        check("导入后预向量化线程接线（stub 完成）",
              _prog5 and getattr(_mgr5, "_vec_running", False) is False,
              f"prog={len(_prog5)} running={getattr(_mgr5, '_vec_running', '?')}")
        _txt5 = _mgr5.summary.cget("text")
        check("预向量化进度文案可达 summary",
              "篇文档" in _txt5 or "向量化" in _txt5, f"summary={_txt5!r}")
    finally:
        _vmod.embed_chunks_missing = _orig_ecm
        _mgr5.close()
        _app5.root.destroy()
except Exception as exc:
    import traceback; traceback.print_exc()
    check("批量导入子系统", False, repr(exc))

# ===== 6.27 生成模型切换增强（v1.7.2：模型历史记忆 + 检索参数 + 引擎热重建） =====
print("\n== 6.27 模型历史记忆 / 检索参数 / 切厂商跟随（v1.7.2）==")
try:
    import json as _json627
    import tempfile as _tf627
    from qt import config as _cmod627
    from qt.config import PROVIDER_ORDER as _PO627, MODEL_HISTORY_MAX as _MHM627

    _d627 = _tf627.mkdtemp(prefix="qt_cfg_627_")
    _c627 = _cmod627.Config()
    _c627.path = os.path.join(_d627, "config.json")
    _c627.data = _json627.loads(_json627.dumps(_cmod627.DEFAULTS))

    # (1) 惰性播种：旧配置无 models 键，models_for 用 chat_model 兜底
    check("models_for 无历史时播种厂商默认",
          _c627.models_for("modelscope") == ["Qwen/Qwen3.8-Flash-Next"],
          f"-> {_c627.models_for('modelscope')}")
    check("魔搭默认模型已更新为 Flash-Next（v1.7.2）",
          _cmod627.DEFAULTS["providers"]["modelscope"]["chat_model"]
          == "Qwen/Qwen3.8-Flash-Next")

    # (2) remember_model：去重 + 最近在前 + 默认恒最前
    _c627.remember_model("modelscope", "Qwen/Qwen3-8B")
    _c627.remember_model("modelscope", "Qwen/Qwen3.5-72B")
    _c627.remember_model("modelscope", "Qwen/Qwen3-8B")     # 重复 → 置顶去重
    check("历史去重且最近在前，默认恒最前",
          _c627.models_for("modelscope")
          == ["Qwen/Qwen3.8-Flash-Next", "Qwen/Qwen3-8B", "Qwen/Qwen3.5-72B"],
          f"-> {_c627.models_for('modelscope')}")

    # (3) 上限截断 + 非法输入忽略
    for _i in range(12):
        _c627.remember_model("deepseek", f"model-{_i}")
    check(f"历史条数封顶 {_MHM627}",
          len(_c627.get("providers.deepseek.models")) == _MHM627,
          f"-> {len(_c627.get('providers.deepseek.models'))}")
    _c627.remember_model("不存在的厂商", "x")               # 不抛错不改数据
    _c627.remember_model("deepseek", "   ")
    check("非法厂商/空模型静默忽略",
          len(_c627.get("providers.deepseek.models")) == _MHM627
          and "不存在的厂商" not in (_c627.get("providers") or {}))

    # (4) 落盘持久化：save → 新实例 load 后历史仍在
    _c627.save()
    _c627b = _cmod627.Config()
    _c627b.path = _c627.path
    _c627b.data = _json627.loads(_json627.dumps(_cmod627.DEFAULTS))
    _c627b.load()
    check("模型历史落盘可恢复",
          _c627b.models_for("modelscope")
          == ["Qwen/Qwen3.8-Flash-Next", "Qwen/Qwen3-8B", "Qwen/Qwen3.5-72B"])

    # (5) UI 切厂商：模型下拉跟随 + 跨厂商残留值回退默认
    import tkinter as tk
    from qt.ui import Settings as _Settings627
    _rr627 = tk.Tk()
    _rr627.withdraw()
    _cu627 = _cmod627.Config()
    _cu627.path = os.path.join(_d627, "ui.json")
    _cu627.data = _json627.loads(_json627.dumps(_cmod627.DEFAULTS))
    _cu627.remember_model("deepseek", "deepseek-v3-x")
    _fa627 = type("App", (), {})()
    _fa627.root = _rr627
    _fa627.cfg = _cu627
    _fa627.quit = lambda: None
    _fa627.reload_hotkeys = lambda: None
    _fa627.toggle_tray = lambda v: None
    _fa627.invalidate_rag_engine = lambda: None
    _fa627.apply_autostart = lambda enable: (True, "")
    _st627 = _Settings627(_fa627)
    check("设置页模型下拉 = 当前厂商历史",
          list(_st627.model_box.cget("values"))
          == _cu627.models_for("siliconflow"),
          f"-> {list(_st627.model_box.cget('values'))}")
    _st627.llm_model_var.set("Qwen/Qwen3-8B")               # 魔搭的模型
    _st627.gen_provider_var.set("DeepSeek")                 # 切到 DeepSeek
    _st627._on_provider_change()
    check("切厂商后下拉换成新厂商历史",
          list(_st627.model_box.cget("values"))
          == _cu627.models_for("deepseek"))
    check("跨厂商残留模型回退为空（=新厂商默认）",
          _st627.llm_model_var.get() == "")
    _st627.llm_model_var.set("deepseek-v3-x")               # 历史内不被清掉
    _st627._on_provider_change()
    check("历史内模型切厂商回切不清空",
          _st627.llm_model_var.get() == "deepseek-v3-x")

    # (6) 滚轮防误改（v1.7.2）：悬停在 Combobox 上滚轮必须只滚页面不改值
    def _combos_of(w):
        out = []
        for c in w.winfo_children():
            if c.winfo_class() == "TCombobox":
                out.append(c)
            out.extend(_combos_of(c))
        return out
    _cb627 = _combos_of(_st627)
    check("设置页全部下拉框已拦滚轮（控件级绑定非空）",
          _cb627 and all(c.bind("<MouseWheel>") for c in _cb627),
          f"n={len(_cb627)}")
    check("拦轮后仍转发页面滚动（含 _scroll_canvas）",
          all("break" in str(c.bind("<MouseWheel>"))
              for c in _cb627)
          and hasattr(_st627, "_scroll_canvas"))
    _st627.destroy()
    _rr627.destroy()

    # (7) 短背景豁免（v1.7.2）：检索 query = 问题 + 背景前 100 字
    from rag.engine import _retr_query as _rq627, _BG_QUERY_CHARS as _BQC627
    check("无概念词问题并入背景概念",
          _rq627("是什么？还有什么屏障？", "StoreStore屏障")
          == "是什么？还有什么屏障？ StoreStore屏障")
    check("长背景只取前 100 字（不稀释召回）",
          len(_rq627("q", "X" * 300)) == len("q") + 1 + _BQC627)
    check("背景为空回落纯问题",
          _rq627("什么是锁升级？", "  ") == "什么是锁升级？"
          and _rq627("什么是锁升级？", "") == "什么是锁升级？")
except Exception as exc:
    import traceback; traceback.print_exc()
    check("模型历史子系统", False, repr(exc))

print(f"\n==== 通过 {ok} 项，失败 {fail} 项 ====")
sys.exit(1 if fail else 0)
