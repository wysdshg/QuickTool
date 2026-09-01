"""鼠标卡顿量化测试：拖选「没有选中内容」时，测量系统鼠标消息的派发延迟。

背景（用户报障）：按住左键拖一下、没选中任何文字，鼠标会卡一下。

原理：QuickTrans 的 WH_MOUSE_LL 钩子安装在 Win32 消息循环线程上，而抓词
（模拟 Ctrl+C + 轮询等剪贴板变化）原本也在同一个线程同步执行。选中内容时
剪贴板序列号很快变化、立即返回（无感）；**没选中内容时会等满
timeout×retries（0.45s×2 ≈ 0.9s）**，这段时间钩子线程取不走钩子通知，
系统会等待/降级，表现为鼠标卡顿。

测量方法（不需要改动被测程序）：
  1. 建一个无文本的可见窗口（专用线程跑消息泵），记录每次 WM_MOUSEMOVE
     的到达时间戳；
  2. 以固定 20ms 节奏注入鼠标移动，先跑一段预热得到"基线噪声"；
  3. 在窗口内做一次拖选（无文本 → 抓不到内容，触发最坏路径）；
  4. 继续注入移动，统计拖选结束后 2.5s 内的最大间隔。

用法：
    python tests/drag_lag.py              只测环境里已运行的 QuickTrans
    python tests/drag_lag.py --app        额外启动一个本源码的 App 实例
"""
import ctypes
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

user32 = ctypes.WinDLL("user32", use_last_error=True)
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_void_p,
                             ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t)
# 必须显式声明：lparam 是大正数，默认转换会按 32 位 c_int 处理导致 OverflowError
user32.DefWindowProcW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                  ctypes.c_size_t, ctypes.c_ssize_t]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.CreateWindowExW.argtypes = [ctypes.c_uint32, ctypes.c_wchar_p,
                                   ctypes.c_wchar_p, ctypes.c_uint32,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_void_p,
                                   ctypes.c_void_p, ctypes.c_void_p,
                                   ctypes.c_void_p]
user32.CreateWindowExW.restype = ctypes.c_void_p

SW_SHOW = 5
WM_MOUSEMOVE, WM_DESTROY = 0x0200, 0x0002
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000

MOVES = []                       # [(t, kind)]，kind: move / down / up
_LOCK = threading.Lock()
_READY = threading.Event()
_PROCS = {}


def _wndproc(hwnd, msg, wparam, lparam):
    if msg == WM_MOUSEMOVE:
        with _LOCK:
            MOVES.append((time.perf_counter(), "move"))
    elif msg == WM_DESTROY:
        user32.PostQuitMessage(0)
        return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", ctypes.c_uint), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", ctypes.c_void_p), ("hIcon", ctypes.c_void_p),
                ("hCursor", ctypes.c_void_p), ("hbrBackground", ctypes.c_void_p),
                ("lpszMenuName", ctypes.c_wchar_p),
                ("lpszClassName", ctypes.c_wchar_p)]


def _pump(x, y, w, h):
    """专用线程：建窗口 + 消息泵（记录鼠标移动时间戳）。"""
    hinst = ctypes.windll.kernel32.GetModuleHandleW(None)
    proc = WNDPROC(_wndproc)
    _PROCS["p"] = proc
    wc = WNDCLASSW()
    wc.lpfnWndProc = proc
    wc.hInstance = hinst
    wc.hbrBackground = ctypes.c_void_p(6)          # COLOR_WINDOW+1
    wc.lpszClassName = "QtLagProbe"
    user32.RegisterClassW(ctypes.byref(wc))
    hwnd = user32.CreateWindowExW(0x00000008,      # WS_EX_TOPMOST
                                  "QtLagProbe", "qt-lag-probe",
                                  0x90000000,     # WS_POPUP|WS_VISIBLE
                                  x, y, w, h, None, None, hinst, None)
    user32.SetWindowPos(hwnd, -1, x, y, w, h, 0x0040)   # HWND_TOPMOST
    _READY.hwnd = hwnd
    _READY.set()
    m = ctypes.create_string_buffer(48)            # MSG 结构（够用）
    while user32.GetMessageW(ctypes.byref(m), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(m))
        user32.DispatchMessageW(ctypes.byref(m))


def _move(x, y):
    sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    user32.mouse_event(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE,
                       int(x * 65536 / sw), int(y * 65536 / sh), 0, 0)


def gaps():
    """取相邻两个 move 事件之间的间隔（秒）。"""
    with _LOCK:
        seq = [t for t, k in MOVES if k == "move"]
    return [b - a for a, b in zip(seq, seq[1:])]


# ================================================================ 主流程
if "--app" in sys.argv:                             # 可选：额外起源码版 App
    import main as M
    app = M.App()
    threading.Thread(target=app.run, daemon=True).start()
    for _ in range(100):
        if app.hwnd:
            break
        time.sleep(0.05)
    print(f"APP_started hwnd={app.hwnd}", flush=True)

sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
WW, WH = 640, 420
WX, WY = max((sw - WW) // 2, 10), max(int(sh * 0.18), 10)
threading.Thread(target=_pump, args=(WX, WY, WW, WH), daemon=True).start()
_READY.wait(5.0)
hwnd = getattr(_READY, "hwnd", None)
print(f"probe_window hwnd={hwnd} rect=({WX},{WY},{WW},{WH})", flush=True)
time.sleep(0.4)
user32.SetForegroundWindow(hwnd)                    # 让 Ctrl+C 发到这个无文本窗口
time.sleep(0.3)
fg = user32.GetForegroundWindow()
print(f"foreground_ok={bool(fg) and fg == hwnd}", flush=True)

cx, cy = WX + WW // 2, WY + WH // 2

# ---- 阶段 1：预热，测基线噪声 ----
time.sleep(0.3)
with _LOCK:
    MOVES.clear()
for i in range(30):
    _move(cx - 60 + (i % 12) * 10, cy - 40 + (i % 8) * 10)
    time.sleep(0.02)
base_gaps = gaps()
base_max = max(base_gaps) if base_gaps else 0.0
print(f"BASELINE moves={len(base_gaps) + 1} max_gap={base_max * 1000:.0f}ms",
      flush=True)

# ---- 阶段 2：拖选（窗口内无文本 → 抓不到内容 → 走最坏路径）----
with _LOCK:
    MOVES.append((time.perf_counter(), "down"))
_move(cx - 150, cy - 80)
time.sleep(0.06)
user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
time.sleep(0.05)
for i in range(1, 9):
    _move(cx - 150 + i * 37, cy - 80 + i * 20)
    time.sleep(0.035)
user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
t_up = time.perf_counter()
with _LOCK:
    MOVES.append((t_up, "up"))
print("DRAG done (no text under cursor)", flush=True)

# ---- 阶段 3：拖选后继续注入移动，测卡顿尖峰 ----
for i in range(110):
    _move(cx - 60 + (i % 12) * 10, cy - 40 + (i % 8) * 10)
    time.sleep(0.02)

with _LOCK:
    after = [t for t, k in MOVES if k == "move" and t > t_up + 0.05]
after_gaps = [b - a for a, b in zip(after, after[1:])]
after_max = max(after_gaps) if after_gaps else 0.0
big = [g for g in after_gaps if g > 0.15]
print(f"AFTER_DRAG moves={len(after_gaps) + 1} max_gap={after_max * 1000:.0f}ms "
      f"gaps>150ms={len(big)}", flush=True)
if big:
    print(f"  spikes_ms={[int(g * 1000) for g in big]}", flush=True)

verdict = "LAG" if after_max > max(base_max * 3, 0.25) else "SMOOTH"
print(f"VERDICT={verdict}", flush=True)
