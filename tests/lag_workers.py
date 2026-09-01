"""验证重活是否已移出鼠标钩子线程（"拖选卡鼠标"修复的回归测试）。

为什么不用真实鼠标卡顿来测：本机可能还有其他 QuickTrans 实例在跑，它们
各自安装钩子，会污染鼠标消息的延迟测量（见 tests/drag_lag.py，那个脚本
测的是"整体体感"，需要干净环境）。

本测试只针对**本进程启动的这个 App 实例**，完全隔离：
  1. 把抓词函数替换成 sleep(0.8s)，人为制造一个"最坏情况"的阻塞窗口
     （等价于"没选中内容时等满 timeout×retries"）；
  2. 注入一次拖选触发抓词；
  3. 抓词期间反复用 SendMessage(hwnd, WM_NULL) 测量 Win32 线程的消息泵
     往返延迟——跨线程 SendMessage 必须等目标线程回到消息泵才会返回，
     所以这个数字直接反映"钩子线程有没有被堵住"。

判定：
  - 修复前：抓词在 Win32 线程同步执行 → 往返延迟 ≈ 800ms；
  - 修复后：抓词在 CaptureWorker 线程 → 往返延迟应 < 200ms。

用法：python tests/lag_workers.py
"""
import ctypes
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main as M

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                ctypes.c_size_t, ctypes.c_ssize_t]
user32.SendMessageW.restype = ctypes.c_ssize_t
WM_NULL = 0x0000
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000

SLOW = 0.8                # 模拟最坏抓词耗时
SEEN = {"thread": None, "calls": 0}


def _fake_capture(*a, **kw):
    """假抓词：只睡，不碰剪贴板，返回空（不弹迷你按钮，避免干扰桌面）。"""
    SEEN["thread"] = threading.current_thread().name
    SEEN["calls"] += 1
    time.sleep(SLOW)
    return ""


M.get_selected_text = _fake_capture      # patch 的是 main 命名空间里的名字


def _move(x, y):
    sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    user32.mouse_event(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE,
                       int(x * 65536 / sw), int(y * 65536 / sh), 0, 0)


def pump_latency(hwnd, samples, gap=0.06):
    """测量 Win32 线程消息泵往返延迟的最大值（毫秒）。"""
    worst = 0.0
    for _ in range(samples):
        t0 = time.perf_counter()
        user32.SendMessageW(ctypes.c_void_p(hwnd), WM_NULL, 0, 0)
        worst = max(worst, (time.perf_counter() - t0) * 1000)
        time.sleep(gap)
    return worst


# ================================================================ 主流程
app = M.App()
threading.Thread(target=app.run, daemon=True, name="AppMain").start()

t0 = time.time()
while time.time() - t0 < 10:
    if app.hwnd and app.drag:
        break
    time.sleep(0.05)
print(f"APP ready hwnd={app.hwnd} drag={bool(app.drag)}", flush=True)
time.sleep(0.6)

# 热键被占用时启动会弹"已自动改用 X"提示，而 popup_open=True 会让拖选
# 提前 return（设计如此），先把这些提示关掉再测。
for _ in range(5):
    if not app.popup_open:
        break
    app.q.put(("close_popup", None))
    time.sleep(0.3)
print(f"popup_cleared={not app.popup_open}", flush=True)

if "--baseline" in sys.argv:
    # 对照组：模拟修复前的行为——抓词在 Win32 线程同步执行。
    # _handle_drag_end 由 WM_APP_DRAG_END 在钩子线程里调用，所以这里直接
    # 同步跑假抓词（sleep 0.8s）等价于旧代码的最坏情况。
    app._handle_drag_end = lambda: (_fake_capture(), None)[1]
    print("MODE=baseline (模拟修复前：抓词在钩子线程同步执行)", flush=True)
else:
    print("MODE=fixed (抓词在 CaptureWorker)", flush=True)

base = pump_latency(app.hwnd, 8)
print(f"IDLE pump_latency_max={base:.0f}ms", flush=True)

# ---- 触发一次拖选（屏幕左上空白区，不选中任何东西）----
sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
x0, y0 = 60, max(int(sh * 0.72), 60)
_move(x0, y0)
time.sleep(0.1)
user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
time.sleep(0.05)
for i in range(1, 7):
    _move(x0 + i * 30, y0 + i * 12)
    time.sleep(0.03)
user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
print("DRAG triggered", flush=True)

# ---- 抓词进行中（约 0.8s），持续采样钩子线程响应性 ----
during = pump_latency(app.hwnd, 12, gap=0.05)
print(f"DURING_CAPTURE pump_latency_max={during:.0f}ms", flush=True)
print(f"CAPTURE_THREAD={SEEN['thread']} calls={SEEN['calls']}", flush=True)

ok_thread = SEEN["thread"] == "CaptureWorker"
ok_latency = during < 200
print(f"THREAD_OK={ok_thread} (抓词必须在 CaptureWorker，不能在 Win32Thread)",
      flush=True)
print(f"LATENCY_OK={ok_latency} (钩子线程往返 < 200ms，实测 {during:.0f}ms)",
      flush=True)
print(f"RESULT {'PASS' if (ok_thread and ok_latency and SEEN['calls']) else 'FAIL'}",
      flush=True)
os._exit(0)
