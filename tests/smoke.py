"""冒烟测试：不依赖 GUI，逐个验证底层能力。运行：python tests/smoke.py"""
import os
import sys
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
t0 = time.perf_counter()
text = get_selected_text(timeout=0.25, retries=1)
check("未选中时不卡死", time.perf_counter() - t0 < 2.0, f"耗时 {time.perf_counter()-t0:.2f}s")
check("抓词后剪贴板已还原", wa.get_clipboard_text() == "原始内容-ABC-123",
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
    tray = wa.Tray(hwnd, "QuickTrans 测试")
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
        tk.Label(root, text="quicktrans ocr test",
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
        path = os.path.join(tempfile.gettempdir(), "QuickTrans_smoke_ocr.bmp")
        with open(path, "wb") as f:
            f.write(bmp)
        good, text, _tag, err = _ocr.ocr_image_file(path, _lang, timeout=40)
        hit = good and any(w in text.lower()
                           for w in ("quicktrans", "ocr", "test"))
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
    fake3._selecting = True
    fake3.ocr_selector = None
    fake3.pin_wins = []
    fake3.popup_open = False
    fake3.mini_btn = None
    fake3.cfg = type("C", (), {
        "get": lambda s, k, d=None: True if k == "mini_button" else d})()
    fake3._drag_pt = (10, 10)
    fake3._request_capture = lambda kind, payload: calls2.append(kind)
    _main.App._handle_drag_end(fake3)
    check("选区流程中钩子不抢拖拽", calls2 == [])
    fake3._selecting = False
    _main.App._handle_drag_end(fake3)
    check("选区结束后钩子正常抓词", calls2 == ["drag"], f"-> {calls2}")
    root.destroy()
except Exception as exc:
    check("PinWindow 子系统", False, repr(exc))

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
logging.getLogger("QuickTrans").info("SMOKE-PROBE-DIRECT")
import io

# 强制落盘：logging 默认按行缓冲，直接检查文件内容
for h in logging.getLogger("QuickTrans").handlers:
    try:
        h.flush()
    except Exception:
        pass
main_log = os.path.join(d, "QuickTrans.log")
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

print(f"\n==== 通过 {ok} 项，失败 {fail} 项 ====")
sys.exit(1 if fail else 0)
