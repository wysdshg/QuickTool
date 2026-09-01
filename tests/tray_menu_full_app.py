"""真 App 全链路驱动测试：托盘右键 -> 菜单 -> 选中「设置」-> 设置窗口可见。

驱动真实 main.App（不是 mock），验证 WM_RBUTTONUP 分支的完整链路：
  Shell 通知 -> popup_menu(TrackPopupMenu 阻塞等待选择) -> cmd==1001
  -> q.put(("settings", None)) -> open_settings() -> Toplevel 可见

注意：本脚本只从外部通过 Win32 观察，绝不跨线程调 Tk（否则会死锁），
验证完成后直接 os._exit(0)（诊断脚本，不需要优雅退出）。

用法：python tests/tray_menu_full_app.py
"""
import ctypes
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main as M
from qt import winapi as wa

user32 = wa.user32
VK_DOWN, VK_RETURN, VK_ESCAPE = 0x28, 0x0D, 0x1B
KEYEVENTF_KEYUP = 0x0002
user32.IsWindowVisible.argtypes = [wa.HANDLE]
user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]


def key(vk):
    user32.keybd_event(vk, 0, 0, 0)
    time.sleep(0.04)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.06)


def find(pred, timeout=10.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = pred()
        if r:
            return r
        time.sleep(0.05)
    return None


app = M.App()
threading.Thread(target=app.run, daemon=True).start()


def ready():
    return bool(app.hwnd and app.tray and app.tray.added)


ready_ok = find(ready, 10.0)
print(f"APP ready={bool(ready_ok)} hwnd={app.hwnd} "
      f"tray_added={bool(app.tray and app.tray.added)}", flush=True)

user32.SetCursorPos(300, 300)
time.sleep(0.2)

# 模拟 Shell 通知：托盘图标右键抬起
wa.post_message(app.hwnd, wa.WM_APP_TRAY, 1, wa.WM_RBUTTONUP | (1 << 16))

# 找菜单窗口（#32768）
menu = find(lambda: user32.FindWindowW("#32768", None), 5.0)
print(f"menu_found={bool(menu)}", flush=True)

# 键盘选中第一项「设置」+ 回车
if menu:
    key(VK_DOWN)
    key(VK_RETURN)

# 等设置窗口出现且可见（Win32 外部确认，不碰 Tk）
hwnd_s = find(lambda: (lambda h: h and user32.IsWindowVisible(h))(
    user32.FindWindowW(None, "QuickTrans 设置")), 8.0)
print(f"settings_visible={bool(hwnd_s)} hwnd={hwnd_s}", flush=True)

# 等对象引用赋值（Settings.__init__ 先 deiconify 显示、_build() 完成后才
# 返回，open_settings() 的 self.settings = ... 赋值发生在之后，存在
# 几十毫秒的"窗口可见但引用未赋值"时序窗口）
obj_ok = find(lambda: app.settings is not None, 8.0)
print(f"app.settings_obj={bool(obj_ok)}", flush=True)

# 菜单可能还开着（选择失败时），ESC 兜底后强制退出
key(VK_ESCAPE)
time.sleep(0.2)
print("APP done", flush=True)
os._exit(0)
