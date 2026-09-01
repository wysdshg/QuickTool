"""端到端测试：真起一个进程，用 SendInput 真实按下全局热键。

验证链路：系统热键 -> Win32 消息循环 -> 抓词线程 -> 队列 -> tkinter 悬浮窗

    python tests/e2e.py            源码模式
    python tests/e2e.py --exe      测打包后的 dist/QuickTrans.exe
"""
import os
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable
EXE = os.path.join(ROOT, "dist", "QuickTrans.exe")
LOG = os.path.join(tempfile.gettempdir(), "QuickTrans_e2e.log")
sys.path.insert(0, ROOT)

from qt import winapi as wa

USE_EXE = "--exe" in sys.argv
if os.path.exists(LOG):
    os.remove(LOG)

if USE_EXE:
    cmd, cwd = [EXE, "--selftest"], os.path.dirname(EXE)
    print(f"\n== E2E（打包版 {EXE}）==")
else:
    cmd, cwd = [PY, "main.py", "--selftest"], ROOT
    print("\n== E2E（源码模式）==")

proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, text=True,
                        encoding="utf-8", errors="replace")


def reader():                                   # windowed exe 没有输出，仅兜底用
    for line in proc.stdout:
        print("   stdout |", line.rstrip())


threading.Thread(target=reader, daemon=True).start()


def events():
    try:
        with open(LOG, "r", encoding="utf-8") as f:
            return [l.strip() for l in f if l.strip().startswith("EVENT:")]
    except FileNotFoundError:
        return []


def wait_for(prefix, timeout=25.0):
    end = time.time() + timeout
    while time.time() < end:
        for ln in events():
            if ln.startswith("EVENT:" + prefix):
                return ln
        time.sleep(0.1)
    return None


def send_combo(combo, hold=0.05):
    mods, vk = wa.HotkeySpec.parse(combo)
    downs = []
    if mods & wa.MOD_CONTROL:
        downs.append(wa.VK_CONTROL)
    if mods & wa.MOD_ALT:
        downs.append(wa.VK_MENU)
    if mods & wa.MOD_SHIFT:
        downs.append(wa.VK_SHIFT)
    for k in downs:
        wa._send_input([wa._key(k)])
    wa._send_input([wa._key(vk)])
    time.sleep(hold)
    wa._send_input([wa._key(vk, up=True)])
    for k in reversed(downs):
        wa._send_input([wa._key(k, up=True)])


results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"  [{'OK' if cond else 'FAIL'}] {name} {extra}")


t0 = time.time()
hk_line = wait_for("HOTKEY=", 30)
startup = time.time() - t0
check("进程启动并完成热键注册", hk_line is not None, hk_line or f"({startup:.1f}s)")
check("READY 信号", wait_for("READY", 10) is not None)

hotkey = hk_line.split("=", 1)[1] if hk_line else "Ctrl+Alt+T"
print(f"  -> 实际使用的快捷键：{hotkey}（进程就绪耗时 {startup:.1f}s）")

print("\n== 真实按下全局热键（未选中文本 -> 应弹出提示窗）==")
before = len(events())
send_combo(hotkey)
got = wait_for("POPUP_SHOWN:", 8)
check("热键触发后弹窗出现", got is not None, got or "")

print("\n== 界面与退出 ==")
check("翻译链路", wait_for("TRANSLATE_OK=", 25) is not None)
check("悬浮窗渲染", wait_for("POPUP_OK", 10) is not None)
check("设置窗口构建（托盘消息模拟路径）", wait_for("SETTINGS_OK=True", 10) is not None)
check("迷你按钮翻译链路", wait_for("MINI_DONE=True", 20) is not None)
check("截图对照小窗 + 剪贴板 CF_DIB", wait_for("PIN_CLIP=True", 10) is not None)
ocr_line = wait_for("OCR_LANGS=", 40)
check("截图 OCR 桥（语言包探测）", ocr_line is not None,
      (ocr_line or "").replace("EVENT:", "") +
      ("（系统无 OCR 语言包，功能不可用但桥路正常）"
       if ocr_line and ocr_line.endswith("=0") else ""))
check("自动退出", wait_for("QUIT", 10) is not None)

try:
    code = proc.wait(timeout=20)
except subprocess.TimeoutExpired:
    proc.kill()
    code = -1
check("进程干净退出（返回码 0）", code == 0, f"code={code}")

print(f"\n==== E2E 通过 {sum(results)}/{len(results)} 项 ====")
sys.exit(0 if all(results) else 1)
