"""真 App 全链路驱动测试：截图翻译（框选 -> GDI 抓屏 -> OCR -> 翻译管线）。

驱动真实 main.App（不是 mock），完整链路：
  q.put(("ocr_select", None)) -> open_ocr() -> RegionSelector 全屏遮罩
  -> SendInput 真实鼠标拖拽框选（含已知文字的区域）
  -> _ocr_region_selected：GDI 抓屏 -> WM_APP_OCR_RUN -> _run_ocr(PowerShell OCR)
  -> q.put(("ocr_result", ...)) -> _handle_ocr_result -> _mini_pending
  -> WM_APP_MINI_TRANSLATE -> 翻译 -> Popup 打开

同时验证干扰防护：框选拖动期间，WH_MOUSE_LL 钩子不应把这次拖动当成
「划词拖选」而弹出迷你按钮（_handle_drag_end 的 ocr_selector 守卫）。

已知文字用纯 Win32 渲染：自注册窗口类 + STATIC 子控件 + 大号粗体字体，
在专用线程跑消息循环。绝不在脚本里开第二个 Tk root（双 Tk 跨线程有死锁
前科，见 tray_menu_full_app.py 的教训）。

注意：只从外部观察（读 App 属性 + Win32），绝不跨线程调 Tk；
验证完成后 os._exit(0)（诊断脚本，不需要优雅退出）。

用法：python tests/ocr_full_app.py
"""
import ctypes
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main as M                      # 导入即设置 DPI 感知（物理像素坐标）
from qt import winapi as wa
from qt import ocr

user32 = wa.user32
gdi32 = wa.gdi32

TEST_TEXT = "quicktrans ocr full app test"

# ---- Win32 常量 ----
WS_POPUP, WS_VISIBLE, WS_CHILD = 0x80000000, 0x10000000, 0x40000000
WS_EX_TOPMOST = 0x8
SS_CENTER = 1
WM_SETFONT = 0x0030
HWND_TOPMOST = -1
SWP_SHOWWINDOW = 0x40
FW_BOLD = 700
DEFAULT_CHARSET, CLEARTYPE_QUALITY = 1, 5
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000
COLOR_WINDOW = ctypes.c_void_p(6)

WNDPROC = wa.WNDPROC
_PROCS = {}                           # 防回调被 GC


def _stub_wndproc(hwnd, msg, wparam, lparam):
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def _text_window_thread(x, y, w, h, text, ready):
    """专用线程：创建置顶弹出窗 + 大字 STATIC，然后跑消息循环维持渲染。"""
    hinst = wa.kernel32.GetModuleHandleW(None)
    proc = WNDPROC(_stub_wndproc)
    _PROCS["stub"] = proc
    wcex = wa.WNDCLASSEXW()
    wcex.cbSize = ctypes.sizeof(wa.WNDCLASSEXW)
    wcex.lpfnWndProc = proc
    wcex.hInstance = hinst
    wcex.hbrBackground = COLOR_WINDOW             # 白底，OCR 对比度好
    wcex.lpszClassName = "QuickTransOcrTestHost"
    if not user32.RegisterClassExW(ctypes.byref(wcex)):
        err = ctypes.get_last_error()
        if err != 1410:                           # ERROR_CLASS_ALREADY_EXISTS
            print(f"TEXT_WINDOW register_fail err={err}", flush=True)
            ready.set()
            return
    host = user32.CreateWindowExW(
        WS_EX_TOPMOST, "QuickTransOcrTestHost", "qt-ocr-test",
        WS_POPUP | WS_VISIBLE, x, y, w, h, None, None, hinst, None)
    if not host:
        print(f"TEXT_WINDOW create_fail err={ctypes.get_last_error()}",
              flush=True)
        ready.set()
        return
    hfont = gdi32.CreateFontW(-64, 0, 0, 0, FW_BOLD, 0, 0, 0,
                              DEFAULT_CHARSET, 0, 0, CLEARTYPE_QUALITY,
                              0, "Arial")
    static = user32.CreateWindowExW(
        0, "STATIC", text, WS_CHILD | WS_VISIBLE | SS_CENTER,
        0, 0, w, h, host, None, hinst, None)
    if static:
        user32.SendMessageW(static, WM_SETFONT, hfont, 1)
    user32.SetWindowPos(host, HWND_TOPMOST, 0, 0, 0, 0,
                        0x0001 | 0x0002 | SWP_SHOWWINDOW)   # NOSIZE|NOMOVE
    user32.UpdateWindow(host)
    ready.set()
    wa.message_loop()                             # 驻留，处理 WM_PAINT 等


def _mouse_abs(x, y):
    sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    user32.mouse_event(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE,
                       int(x * 65536 / sw), int(y * 65536 / sh), 0, 0)


def drag_select(x0, y0, x1, y1):
    """注入真实鼠标拖拽（RegionSelector 只认真实输入事件）。"""
    _mouse_abs(x0, y0)
    time.sleep(0.2)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.15)
    steps = 14
    for i in range(1, steps + 1):
        _mouse_abs(x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps)
        time.sleep(0.03)                          # 让 B1-Motion 持续触发
    time.sleep(0.15)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def wait_for(pred, timeout, what):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if pred():
                print(f"{what}=True ({time.time() - t0:.1f}s)", flush=True)
                return True
        except Exception:
            pass
        time.sleep(0.1)
    print(f"{what}=False (timeout {timeout}s)", flush=True)
    return False


# ================================================================ 主流程
sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
tw, th = min(1000, int(sw * 0.55)), 170
tx, ty = int(sw * 0.08), int(sh * 0.52)           # 屏幕左下区域，避开遮罩提示文字
ready = threading.Event()
threading.Thread(target=_text_window_thread,
                 args=(tx, ty, tw, th, TEST_TEXT, ready), daemon=True).start()
ready.wait(5.0)
time.sleep(0.3)
print(f"TEXT_WINDOW ok={ready.is_set()} rect=({tx},{ty},{tw},{th}) "
      f"screen={sw}x{sh}", flush=True)

langs = []
try:
    langs = ocr.available_langs()
except Exception as exc:
    print(f"OCR_LANGS error={exc}", flush=True)
print(f"OCR_LANGS={len(langs)}", flush=True)

# ---- 启动真 App ----
app = M.App()
threading.Thread(target=app.run, daemon=True).start()
wait_for(lambda: bool(app.hwnd and app.tray and app.tray.added), 10, "APP_ready")

# ---- 触发截图选区（走真实队列事件，同热键/托盘菜单路径）----
app.q.put(("ocr_select", None))
sel_ok = wait_for(lambda: app.ocr_selector is not None, 8, "SELECTOR_shown")
time.sleep(0.5)                                   # 等遮罩完全渲染

# ---- 框选文字区域 ----
x0, y0 = max(tx - 30, 2), max(ty - 30, 2)
x1, y1 = min(tx + tw + 30, sw - 2), min(ty + th + 30, sh - 2)
drag_select(x0, y0, x1, y1)
print(f"DRAG done rect=({x0},{y0})-({x1},{y1})", flush=True)

# ---- 守卫验证：拖动结束后迷你按钮不应弹出（截图框选 != 划词）----
time.sleep(1.5)
mini_ok = app.mini_btn is None
print(f"MINI_GUARD_ok={mini_ok}", flush=True)

# ---- OCR 结果应进入翻译管线 ----
bmp = os.path.join(os.environ.get("TEMP", ""), "QuickTrans_ocr.bmp")
bmp_ok = os.path.exists(bmp) and os.path.getsize(bmp) > 0
print(f"GRAB_file={bmp_ok} size={os.path.getsize(bmp) if os.path.exists(bmp) else 0}",
      flush=True)

pending_ok = wait_for(lambda: bool(app._mini_pending.strip()), 40,
                      "OCR_pending_text")
print(f"PENDING_TEXT={app._mini_pending.strip()!r}", flush=True)

popup_ok = wait_for(lambda: app.popup_open, 20, "POPUP_shown")

# ---- 汇总 ----
ocr_text = app._mini_pending.strip().lower()
hit = "quicktrans" in ocr_text or "ocr" in ocr_text
print(f"OCR_TEXT_MATCH={hit}", flush=True)
ok = all([sel_ok, mini_ok, pending_ok, popup_ok])
print(f"FULL_APP_OCR {'PASS' if ok else 'FAIL'}", flush=True)
os._exit(0)
