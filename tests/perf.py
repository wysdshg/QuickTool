"""体测：冷启动耗时 + 常驻内存占用 + 热键退出是否干净。

    python tests/perf.py
"""
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, "dist", "QuickTrans.exe")
LOG = os.path.join(tempfile.gettempdir(), "QuickTrans_e2e.log")
sys.path.insert(0, ROOT)

if not os.path.isfile(EXE):
    print("找不到 dist/QuickTrans.exe，请先执行 build.bat")
    sys.exit(1)

size_mb = os.path.getsize(EXE) / 1024 / 1024
print(f"exe 体积： {size_mb:.1f} MB")

if os.path.exists(LOG):
    os.remove(LOG)

print("\n-- 冷启动：从双击到热键注册完成 --")
t0 = time.perf_counter()
p = subprocess.Popen([EXE, "--selftest"], cwd=os.path.dirname(EXE))
boot = hotkey = None
while time.perf_counter() - t0 < 40:
    if os.path.exists(LOG):
        with open(LOG, encoding="utf-8") as f:
            for ln in f:
                if ln.startswith("EVENT:BOOT") and boot is None:
                    boot = time.perf_counter() - t0
                if ln.startswith("EVENT:HOTKEY") and hotkey is None:
                    hotkey = time.perf_counter() - t0
                    break
    if hotkey:
        break
    time.sleep(0.02)
print(f"  进程创建 -> Python 解释器就绪： {boot*1000:.0f} ms" if boot else "  BOOT 未捕获")
print(f"  进程创建 -> 全局热键可用：     {hotkey*1000:.0f} ms" if hotkey else "  HOTKEY 未捕获")

try:
    p.wait(timeout=25)
except subprocess.TimeoutExpired:
    p.kill()

print("\n-- 常驻内存（启动后静置 12s，不显示任何窗口）--")
p = subprocess.Popen([EXE], cwd=os.path.dirname(EXE))
time.sleep(12)
import ctypes
from ctypes import wintypes


class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]


psapi = ctypes.WinDLL("psapi")
psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE,
                                       ctypes.POINTER(PROCESS_MEMORY_COUNTERS),
                                       wintypes.DWORD]
PROCESS_QUERY_INFORMATION, PROCESS_VM_READ = 0x0400, 0x0010
h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ,
                                       False, p.pid)
pmc = PROCESS_MEMORY_COUNTERS()
pmc.cb = ctypes.sizeof(pmc)
if h and psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), ctypes.sizeof(pmc)):
    ws = pmc.WorkingSetSize / 1024 / 1024
    priv = pmc.PagefileUsage / 1024 / 1024
    print(f"  常驻内存（工作集）： {ws:.1f} MB")
    print(f"  私有内存（提交）：   {priv:.1f} MB")
    with open(os.path.join(ROOT, "build", "perf.txt"), "w", encoding="utf-8") as f:
        f.write(f"size_mb={size_mb:.1f}\nws_mb={ws:.1f}\npriv_mb={priv:.1f}\n")
ctypes.windll.kernel32.CloseHandle(h)

print("\n-- 热键退出 --")
from qt import winapi as wa
from qt.config import Config

# 不能写死 Ctrl+Alt+Q——退出热键被占用顺延后配置里存的是实际生效的组合
QUIT_HOTKEY = Config().get("hotkey_quit") or "Ctrl+Alt+Q"
print(f"  发送退出热键：{QUIT_HOTKEY}")


def send_combo(combo, hold=0.05):
    mods, vk = wa.HotkeySpec.parse(combo)
    downs = [k for m, k in ((wa.MOD_CONTROL, wa.VK_CONTROL), (wa.MOD_ALT, wa.VK_MENU),
                            (wa.MOD_SHIFT, wa.VK_SHIFT)) if mods & m]
    for k in downs:
        wa._send_input([wa._key(k)])
    wa._send_input([wa._key(vk)])
    time.sleep(hold)
    wa._send_input([wa._key(vk, up=True)])
    for k in reversed(downs):
        wa._send_input([wa._key(k, up=True)])


send_combo(QUIT_HOTKEY)
try:
    code = p.wait(timeout=8)
    print(f"  退出成功，返回码 {code}")
except subprocess.TimeoutExpired:
    p.kill()
    print("  热键退出失败（进程仍在）")
