"""v1.4 TaskbarCreated 托盘重建分支的白盒验证。

场景：explorer.exe 崩溃重启会广播 "TaskbarCreated"（注册消息号 >= 0xC000），
系统顺手把全部托盘图标收走。程序收到该消息必须重新 Shell_NotifyIconW，
否则"程序活着但托盘图标没了"。

做法：不起完整 GUI，直接实例化 App 并注入消息到 _on_message，
断言走到托盘重建分支、不抛异常。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qt import winapi as wa
from qt import logging_setup as ls

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [OK]   {name} {extra}")
    else:
        fail += 1
        print(f"  [FAIL] {name} {extra}")


import main as _m

ls.setup_logging()

print("== TaskbarCreated 托盘重建（v1.4 新功能）==")
app = _m.App()
# 最小化骨架：不跑 run()，只挂隐藏窗口 + 假托盘
app.hwnd = wa.create_hidden_window(app._on_message)
app._taskbar_created = wa.register_taskbar_created_msg()
check("注册消息号有效", app._taskbar_created >= 0xC000,
      f"0x{app._taskbar_created:X}")

add_calls = []
tray_add_orig = _m.App._tray_add


class FakeTray:
    """真实 Tray 的替身：记录 add 调用，且 _tray_add 被 patch 后不会被替换。"""
    added = True

    def add(self):
        add_calls.append(1)
        self.added = True
        return True

    def remove(self):
        self.added = False


def _fake_tray_add(self):
    add_calls.append(1)


_m.App._tray_add = _fake_tray_add     # 替换掉真实的 Tray 构建，只记录调用

app.tray = FakeTray()
app.cfg.set("enable_tray", True)

# 注入 TaskbarCreated 广播
handled = app._on_message(app.hwnd, app._taskbar_created, 0, 0)
check("消息被处理（返回 True）", handled is True)
check("触发托盘重建 add()", len(add_calls) >= 1, f"add 次数={len(add_calls)}")

# 非 TaskbarCreated 消息不受影响（回归）
handled2 = app._on_message(app.hwnd, wa.WM_APP + 99, 0, 0)
check("其他消息不误触重建", len(add_calls) == 1 and handled2 is False)

# 配置关闭托盘时收到广播也不重建（防骚扰）
app.tray = FakeTray()
app.cfg.set("enable_tray", False)
n = len(add_calls)
app._on_message(app.hwnd, app._taskbar_created, 0, 0)
check("关闭托盘时不重建", len(add_calls) == n)

_m.App._tray_add = tray_add_orig

wa.post_message(app.hwnd, wa.WM_DESTROY)
print(f"\n==== 通过 {ok} 项，失败 {fail} 项 ====")
sys.exit(1 if fail else 0)
