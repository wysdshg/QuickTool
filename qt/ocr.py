"""截图 OCR：调用 Windows 10/11 内置的 Windows.Media.Ocr（WinRT）。

为什么走 PowerShell 而不是 pip 装 winrt / PaddleOCR / Tesseract：
  - QuickTool 铁律是零第三方运行时依赖（打包 exe 要小、不碰杀软红线）；
  - Windows.Media.Ocr 是系统自带能力，完全离线、无 API Key，识别质量
    对"屏幕清晰文字"场景完全够用（STranslate 等也把它作为引擎之一）；
  - PowerShell 5.1 可通过 WinRT 投射直接调用（PS7 反而不行），社区已有
    成熟模式（PsOcr / Get-OCR.ps1），这里内嵌脚本按需写到 %TEMP%。

分工：
  抓屏   由 winapi.grab_screen_bmp 用 GDI 完成（本进程 DPI 感知，
         坐标即物理像素，避开子进程 DPI 虚拟化的坑）
  OCR    本模块只负责把 BMP 图片文件喂给 PowerShell 子进程换回文本

OCR 语言包：取决于系统安装的语言（设置→时间和语言→语言→选项→
"文本识别"）。AvailableRecognizerLanguages 为空 = 没有可用语言包。
"""
import json
import os
import subprocess
import tempfile
import threading

_POWERSHELL = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                           "System32", "windowspowershell", "v1.0",
                           "powershell.exe")
_SCRIPT_PATH = os.path.join(tempfile.gettempdir(), "QuickTool_ocr.ps1")
_CREATE_NO_WINDOW = 0x08000000          # windowed exe 下别闪黑框

# WinRT 异步操作在 PS5.1 里没有原生 await，用反射拿 GetAwaiter 兜住（社区标准做法）
_PS_OCR = r"""param([string]$ImgPath, [string]$LangTag)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
try {
  Add-Type -AssemblyName System.Runtime.WindowsRuntime
  $null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
  $null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
  $null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
  $null = [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]
  $awaiter = [WindowsRuntimeSystemExtensions].GetMember('GetAwaiter', 'Method', 'Public,Static') |
    Where-Object { $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } |
    Select-Object -First 1
  function Await($t, $as) {
    $awaiter.MakeGenericMethod($as).Invoke($null, @($t)).GetResult()
  }
  $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($ImgPath)) `
              ([Windows.Storage.StorageFile])
  $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) `
              ([Windows.Storage.Streams.IRandomAccessStream])
  $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) `
              ([Windows.Graphics.Imaging.BitmapDecoder])
  $bmp = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
  if ($LangTag -and $LangTag -ne 'auto') {
    $lang = New-Object Windows.Globalization.Language($LangTag)
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($lang)
  } else {
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
  }
  if (-not $engine) {
    @{ ok = $false; error = 'no_engine'; text = ''; lang = '' } |
      ConvertTo-Json -Compress | Write-Output
    exit 0
  }
  $result = Await ($engine.RecognizeAsync($bmp)) ([Windows.Media.Ocr.OcrResult])
  $lines = @($result.Lines | ForEach-Object { $_.Text })
  @{ ok = $true; text = ($lines -join "`n"); lang = $engine.RecognizerLanguage.LanguageTag; error = '' } |
    ConvertTo-Json -Compress | Write-Output
} catch {
  @{ ok = $false; error = $_.Exception.Message; text = ''; lang = '' } |
    ConvertTo-Json -Compress | Write-Output
}
"""

_PS_LANGS = r"""
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
[Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages |
  ForEach-Object { $_.LanguageTag }
"""


def _run_ps(args, timeout):
    return subprocess.run(
        [_POWERSHELL, "-NoProfile", "-NonInteractive", "-STA",
         "-ExecutionPolicy", "Bypass"] + args,
        capture_output=True, timeout=timeout,
        creationflags=_CREATE_NO_WINDOW)


def _ensure_script():
    """把内嵌脚本写到 %TEMP%（内容变更时自动覆盖）。"""
    try:
        with open(_SCRIPT_PATH, "r", encoding="utf-8-sig") as f:
            if f.read() == _PS_OCR:
                return
    except Exception:
        pass
    with open(_SCRIPT_PATH, "w", encoding="utf-8") as f:
        f.write(_PS_OCR)


_LANGS_CACHE = None                      # 进程级缓存：语言包运行中不会变
# v1.6.7 ③（P1 ③ 收尾）：_LANGS_CACHE 会被多个线程并发首写——JobWorker
# （OCR 前探测语言）与主线程（--selftest）可能同时首次调用 available_langs，
# 无锁会让两边都起 PowerShell 子进程（结果收敛但白跑一次）。double-check 锁：
# 命中缓存走无锁快路径，只有未命中才进锁内探测。
_LANGS_LOCK = threading.Lock()


def available_langs(timeout=20):
    """列出系统可用的 OCR 识别语言（BCP-47 标签，如 en-US / zh-CN）。

    结果缓存于进程生命周期内——语言包安装后需重启本程序生效（可接受），
    否则每次截图都要先起一个 PowerShell 子进程探测，白白多等约 1s。
    """
    global _LANGS_CACHE
    if _LANGS_CACHE is not None:
        return _LANGS_CACHE
    with _LANGS_LOCK:
        if _LANGS_CACHE is not None:     # double-check：别人已探测完
            return _LANGS_CACHE
        try:
            p = _run_ps(["-Command", _PS_LANGS], timeout)
            tags = p.stdout.decode("utf-8", "replace").split()
            _LANGS_CACHE = [t for t in tags if "-" in t]
        except Exception:
            _LANGS_CACHE = []
    return _LANGS_CACHE


def pick_lang(cfg_lang, langs):
    """按配置挑识别语言：auto 时优先英语（本工具主场景是英→中），否则首个可用。"""
    if not langs:
        return None
    if cfg_lang and cfg_lang != "auto":
        if cfg_lang in langs:
            return cfg_lang
    for pref in ("en-US", "en-GB", "en-AU", "zh-CN", "zh-Hans-CN"):
        if pref in langs:
            return pref
    return langs[0]


def ocr_image_file(img_path, lang_tag="auto", timeout=30):
    """对图片文件跑 OCR。返回 (ok, text, lang_tag, error)。"""
    _ensure_script()
    try:
        p = _run_ps(["-File", _SCRIPT_PATH,
                     "-ImgPath", os.path.abspath(img_path),
                     "-LangTag", lang_tag or "auto"], timeout)
    except subprocess.TimeoutExpired:
        return False, "", "", f"OCR 超时（>{timeout}s）"
    out = p.stdout.decode("utf-8", "replace").strip()
    if not out:
        err = p.stderr.decode("utf-8", "replace").strip()[:300] or "无输出"
        return False, "", "", err
    try:
        data = json.loads(out.splitlines()[-1])
    except Exception:
        return False, "", "", out[:300]
    return bool(data.get("ok")), data.get("text", "") or "", \
        data.get("lang", ""), data.get("error", "") or ""
