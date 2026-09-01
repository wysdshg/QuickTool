"""绿色便携性验证：把 exe 单独复制到全新目录，伪造空白 APPDATA 模拟"新电脑首次运行"。

检查三件事：
  1. 脱离源码目录（无 qt/、无 data/）能否独立启动并跑通 selftest 全流程；
  2. 配置文件落到哪里（同目录无 config.json 时应落 %APPDATA%\\QuickTrans\\config.json）；
  3. 离线词库 mini_dict.json 是否随 exe 打包（_MEIPASS 解包路径能否读到）。

用法：python tests/portable_check.py [exe路径]
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EXE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "dist",
                                                        "QuickTrans.exe")
SANDBOX = os.path.join(ROOT, "tests", "_portable_sandbox")
FAKE_APPDATA = os.path.join(SANDBOX, "appdata")
LOG = os.path.join(tempfile.gettempdir(), "QuickTrans_e2e.log")

print(f"exe={EXE} exists={os.path.isfile(EXE)}", flush=True)
if not os.path.isfile(EXE):
    sys.exit("找不到 exe，先打包：python -m PyInstaller build.spec --clean")

# ---- 准备干净沙箱：只有 exe，没有 qt/ data/ config.json ----
if os.path.isdir(SANDBOX):
    shutil.rmtree(SANDBOX, ignore_errors=True)
os.makedirs(FAKE_APPDATA, exist_ok=True)
target = os.path.join(SANDBOX, "QuickTrans.exe")
shutil.copy2(EXE, target)
print(f"sandbox={SANDBOX} files={os.listdir(SANDBOX)}", flush=True)

if os.path.isfile(LOG):
    os.remove(LOG)

env = dict(os.environ)
env["APPDATA"] = FAKE_APPDATA          # 模拟全新用户环境（无历史配置）

t0 = time.time()
proc = subprocess.Popen([target, "--selftest"], env=env, cwd=SANDBOX)
try:
    rc = proc.wait(timeout=120)
except subprocess.TimeoutExpired:
    proc.kill()
    rc = -1
print(f"exit_code={rc} elapsed={time.time() - t0:.1f}s", flush=True)

# ---- selftest 事件日志 ----
if os.path.isfile(LOG):
    with open(LOG, encoding="utf-8") as f:
        events = [l.strip() for l in f if l.strip()]
    for e in events:
        print(f"  {e}", flush=True)
else:
    print("  [FAIL] 无 e2e 日志：进程根本没起来", flush=True)
    sys.exit(1)

required = ["BOOT", "READY", "TRANSLATE_OK=True", "POPUP_OK",
            "SETTINGS_OK=True", "MINI_DONE=True", "OCR_LANGS=", "QUIT"]
missing = [r for r in required
           if not any(e.endswith(r) or (r in e and r.endswith("="))
                      for e in events)]
print(f"REQUIRED_MISSING={missing}", flush=True)

# ---- 配置落点 ----
portable_cfg = os.path.join(SANDBOX, "config.json")
appdata_cfg = os.path.join(FAKE_APPDATA, "QuickTrans", "config.json")
print(f"CFG_portable={os.path.isfile(portable_cfg)} "
      f"CFG_appdata={os.path.isfile(appdata_cfg)}", flush=True)
if os.path.isfile(appdata_cfg):
    with open(appdata_cfg, encoding="utf-8") as f:
        cfg = json.load(f)
    print(f"CFG_keys={len(cfg)} hotkey_translate={cfg.get('hotkey_translate')} "
          f"engine={cfg.get('engine')}", flush=True)

ok = rc == 0 and not missing and os.path.isfile(appdata_cfg)
print(f"PORTABLE {'PASS' if ok else 'FAIL'}", flush=True)
sys.exit(0 if ok else 1)
