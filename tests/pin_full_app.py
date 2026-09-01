"""真 App 全链路驱动测试：截图对照小窗（队列触发 -> 遮罩 -> 框选 -> PinWindow）。

驱动真实 main.App（不是 mock），完整链路：
  q.put(("pin_select", None)) -> open_pin() -> RegionSelector 全屏遮罩
  -> SendInput 真实鼠标拖拽框选
  -> _pin_region_selected：GDI 抓屏 -> PinWindow 置顶小窗弹出
  -> 关闭后 app.pin_wins 清空（v1.5.1 多窗口：并存 + 级联偏移）

v1.5.1 多窗口断言：
  - 两次框选并存 total=2，且第二个窗口级联偏移位置不同
  - 继续框选到第 5 个正常创建；第 6 次被 PIN_MAX 上限拦截（仍 5 个）

同时验证干扰防护：框选拖动期间，WH_MOUSE_LL 钩子不应把这次拖动当成
「划词拖选」而弹出迷你按钮（_handle_drag_end 的 ocr_selector 守卫）。

注意：只从外部观察（读 App 属性 + Win32），绝不跨线程调 Tk；
验证完成后 os._exit(0)（诊断脚本，不需要优雅退出）。

用法：python tests/pin_full_app.py
"""
import ctypes
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main as M                      # 导入即设置 DPI 感知（物理像素坐标）
from qt import winapi as wa

user32 = wa.user32

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000


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
# 框选一个固定区域（避开遮罩顶部提示文字 46px 附近）
bx0, by0 = int(sw * 0.25), int(sh * 0.30)
bx1, by1 = int(sw * 0.55), int(sh * 0.45)
print(f"SCREEN={sw}x{sh} REGION=({bx0},{by0})-({bx1},{by1})", flush=True)

# ---- 启动真 App ----
app = M.App()
threading.Thread(target=app.run, daemon=True).start()
wait_for(lambda: bool(app.hwnd and app.tray and app.tray.added), 10, "APP_ready")

# ---- 触发截图对照选区（走真实队列事件，同热键路径）----
app.q.put(("pin_select", None))
sel_ok = wait_for(lambda: app.ocr_selector is not None, 8, "SELECTOR_shown")
time.sleep(0.5)                                   # 等遮罩完全渲染

# ---- 框选区域 ----
drag_select(bx0, by0, bx1, by1)
print(f"DRAG done rect=({bx0},{by0})-({bx1},{by1})", flush=True)

# ---- 守卫验证：拖动结束后迷你按钮不应弹出（截图框选 != 划词）----
time.sleep(1.0)
mini_ok = app.mini_btn is None
print(f"MINI_GUARD_ok={mini_ok}", flush=True)

# ---- PinWindow 应已创建且置顶 ----
pin_ok = wait_for(lambda: len(app.pin_wins) >= 1
                  and not app.pin_wins[0].closed, 8, "PINWINDOW_shown")
topmost_ok = False
factor_ok = False
if pin_ok:
    pw = app.pin_wins[0]
    try:
        topmost_ok = bool(pw.win.attributes("-topmost"))
        factor_ok = 1 <= pw._factor <= pw.ZOOM_MAX
        print(f"PIN factor={pw._factor} size={pw._orig_w}x{pw._orig_h} "
              f"photo={pw._photo.width()}x{pw._photo.height()}", flush=True)
    except Exception as exc:
        print(f"PIN inspect error={exc}", flush=True)

# ---- 剪贴板：框选完成即进 CF_DIB（同 Win+Shift+S）----
clip_ok = wait_for(wa.clipboard_has_dib, 5, "CLIP_DIB")
print(f"CLIP_DIB_ok={clip_ok}", flush=True)

# ---- 再截一个：应并存（多窗口），且第二个有级联偏移 ----
app.q.put(("pin_select", None))
wait_for(lambda: app.ocr_selector is not None, 8, "SELECTOR2_shown")
time.sleep(0.4)
bx0b, by0b = int(sw * 0.30), int(sh * 0.55)
bx1b, by1b = int(sw * 0.60), int(sh * 0.70)
drag_select(bx0b, by0b, bx1b, by1b)
two_ok = wait_for(lambda: len(app.pin_wins) == 2, 8, "PINWINDOW_second")
cascade_ok = False
if two_ok:
    a, b = app.pin_wins[0], app.pin_wins[1]
    try:
        # 时序：第二个窗口刚创建，给 Tk 主线程 0.5s 完成布局/映射，
        # 再读 winfo_x/y 才是真实屏幕坐标（外部脚本不能跨线程调 Tk）
        time.sleep(0.5)
        cascade_ok = (a.win.winfo_x(), a.win.winfo_y()) != \
                     (b.win.winfo_x(), b.win.winfo_y())
        print(f"PIN2 index={b.index} total={len(app.pin_wins)} "
              f"cascade_ok={cascade_ok}", flush=True)
    except Exception as exc:
        print(f"PIN2 inspect error={exc}", flush=True)

# ---- PIN_MAX 上限：第 3~5 个正常创建，第 6 个被拒（仍 5 个）----
more_ok = True
for i in range(3, M.App.PIN_MAX + 1):           # 第 3、4、5 个
    app.q.put(("pin_select", None))
    wait_for(lambda: app.ocr_selector is not None, 8, f"SELECTOR_{i}")
    time.sleep(0.4)
    x0 = int(sw * 0.12) + i * 18
    y0 = int(sh * 0.18) + i * 12
    drag_select(x0, y0, x0 + int(sw * 0.22), y0 + int(sh * 0.10))
    ok_i = wait_for(lambda i=i: len(app.pin_wins) == i, 8, f"PIN_{i}")
    more_ok = more_ok and ok_i
    print(f"PIN_{i}_ok={ok_i} total={len(app.pin_wins)}", flush=True)

# 第 6 次：应被上限拦截（列表仍是 5 个）
app.q.put(("pin_select", None))
wait_for(lambda: app.ocr_selector is not None, 8, "SELECTOR_6th")
time.sleep(0.4)
drag_select(int(sw * 0.12) + 6 * 18, int(sh * 0.18) + 6 * 12,
            int(sw * 0.12) + 6 * 18 + int(sw * 0.22),
            int(sh * 0.18) + 6 * 12 + int(sh * 0.10))
limit_ok = wait_for(
    lambda: len([p for p in app.pin_wins if not p.closed]) == M.App.PIN_MAX,
    5, "PINMAX_kept5")
print(f"PINMAX_limit_ok={limit_ok} total={len(app.pin_wins)}", flush=True)

# ---- 关闭后引用应清空 ----
app.q.put(("pin_close", None))
closed_ok = wait_for(lambda: len(app.pin_wins) == 0, 5, "PINWINDOW_closed")

# ---- 汇总 ----
ok = all([sel_ok, mini_ok, pin_ok, topmost_ok, factor_ok, clip_ok,
          two_ok, cascade_ok, more_ok, limit_ok, closed_ok])
print(f"FULL_APP_PIN {'PASS' if ok else 'FAIL'}", flush=True)
os._exit(0)
