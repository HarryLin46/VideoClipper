# utils.py
#
# 提供 marker.py 會用到的各種小工具：
#   - 取得前景視窗標題與 class name
#   - 從標題抽出影片 basename
#   - 讀取剪貼簿文字
#   - .marks 檔路徑生成與寫入
#   - 計算檔案行數

import os
from typing import Optional, Tuple

import win32clipboard
import win32con
import win32gui


def ensure_dir_exists(path: str) -> None:
    """
    確保目錄存在，若不存在則建立。
    """
    if not path:
        return
    os.makedirs(path, exist_ok=True)


def get_foreground_window_info() -> Tuple[Optional[int], str, str]:
    """
    取得目前前景視窗的 hwnd、標題與 class name。

    回傳:
        (hwnd, title, class_name)
        若目前沒有前景視窗，hwnd 會是 None。
    """
    try:
        hwnd = win32gui.GetForegroundWindow()
    except Exception:
        return None, "", ""

    if not hwnd:
        return None, "", ""

    try:
        title = win32gui.GetWindowText(hwnd) or ""
    except Exception:
        title = ""

    try:
        class_name = win32gui.GetClassName(hwnd) or ""
    except Exception:
        class_name = ""

    return hwnd, title, class_name


def extract_video_basename_from_title(title: str) -> str:
    """
    從 PotPlayer 視窗標題推測影片 basename。

    目前依照你的描述：標題就是純檔名，例如 "MyConcert_2025.ts"。
    我們做的事就是：
      1) strip 空白
      2) 取掉副檔名

    若未來你改了 PotPlayer 標題格式，這裡再調整即可。
    """
    if not title:
        return ""

    # 去掉空白
    title = title.strip()

    # 若未來標題變成 "MyConcert_2025.ts - PotPlayer"，
    # 可以在這裡先 split(" - PotPlayer")[0]
    # 目前先假設 title 就是檔名。
    base = os.path.basename(title)
    name, _ext = os.path.splitext(base)
    return name


def get_clipboard_text() -> Optional[str]:
    """
    從 Windows 剪貼簿讀取文字內容。

    若剪貼簿不是文字或讀取失敗，回傳 None。
    """
    text = None
    try:
        win32clipboard.OpenClipboard()
        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            data = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
            if isinstance(data, str):
                text = data.strip()
        # 若不是文字，保持 text = None
    except Exception:
        text = None
    finally:
        try:
            win32clipboard.CloseClipboard()
        except Exception:
            pass

    return text if text else None


def build_marks_path(marks_dir: str, video_basename: str) -> str:
    """
    將目錄與影片 basename 組合成 .marks 完整路徑。
    """
    filename = f"{video_basename}.marks"
    return os.path.join(marks_dir, filename)


def get_file_line_count(path: str) -> int:
    """
    回傳檔案行數。若檔案不存在則回傳 0。
    """
    if not os.path.exists(path):
        return 0

    try:
        with open(path, "r", encoding="utf-8") as f:
            return sum(1 for _ in f)
    except Exception:
        # 若讀取失敗，保守起見視為 0（後續會從頭開始計算）
        return 0


def append_mark_line(path: str, timestamp: str, tag: str) -> None:
    """
    以 append 模式在 .marks 檔尾寫入一行："<timestamp>, <tag>\\n"
    例如： "00:41:58, start"
          "00:52:03, end"
          "UNKNOWN, start"
    """
    # 確保目錄存在
    dir_name = os.path.dirname(path)
    if dir_name:
        ensure_dir_exists(dir_name)

    line = f"{timestamp}, {tag}\n"

    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
