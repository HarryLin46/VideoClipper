# marker.py
#
# Global middle mouse listener + PotPlayer integration.
# 每次你在 PotPlayer 按下滑鼠中鍵，PotPlayer 會把目前播放時間寫入剪貼簿，
# 這個腳本則會：
#   1) 確認前景視窗是 PotPlayer
#   2) 從剪貼簿讀出時間字串
#   3) 讀取 PotPlayer 視窗標題當作檔名
#   4) 依序寫入 <Desktop>/VideoMarks/<video_basename>.marks
#
# 使用需求：
#   - Windows 平台
#   - 已安裝：pynput, pywin32 (win32gui, win32clipboard)
#
# 建議啟動方式（開發測試）：
#   python marker.py --debug
#
# 實際常駐使用時：
#   python marker.py

import argparse
import os
import time
from collections import defaultdict

from pynput import mouse

from background_marker import utils



# 中鍵按下後，等待 PotPlayer 寫剪貼簿的時間（秒）
CLIPBOARD_DELAY_SEC = 0.8

# 用來判斷是否為 PotPlayer 視窗的 class name 關鍵字
POTPLAYER_CLASS_KEYWORD = "PotPlayer"


class MarkerState:
    """
    管理目前所有影片的 line_count 與共用設定。
    """

    def __init__(self, marks_dir: str, debug: bool = False) -> None:
        self.marks_dir = marks_dir
        self.debug = debug
        # 每個影片 (video_basename) 對應目前 .marks 行數
        self._line_counts = defaultdict(lambda: None)

        utils.ensure_dir_exists(self.marks_dir)

    def _debug_print(self, *args, **kwargs) -> None:
        if self.debug:
            print(*args, **kwargs)

    def _get_line_count(self, video_basename: str) -> int:
        """
        取得某影片目前 .marks 行數。
        若尚未載入過，第一次會實際讀檔計算；之後使用快取。
        """
        cached = self._line_counts[video_basename]
        if cached is not None:
            return cached

        marks_path = utils.build_marks_path(self.marks_dir, video_basename)
        count = utils.get_file_line_count(marks_path)
        self._line_counts[video_basename] = count
        return count

    def _increment_line_count(self, video_basename: str) -> None:
        current = self._get_line_count(video_basename)
        self._line_counts[video_basename] = current + 1

    # 這個函式會被 mouse.Listener 的 callback 呼叫
    def handle_click(self, x: int, y: int, button, pressed: bool) -> None:
        """
        全域滑鼠事件入口。只在「中鍵按下瞬間」觸發標記流程。
        """
        from pynput.mouse import Button

        if button != Button.middle:
            return

        # 只處理「按下」事件
        if not pressed:
            return

        try:
            self._handle_middle_click_event()
        except Exception as e:  # 保護性措施，避免例外讓 listener 終止
            self._debug_print(f"[ERROR] Exception in middle-click handler: {e}")

    def _handle_middle_click_event(self) -> None:
        """
        實際處理一次中鍵事件：
          1. 確認前景視窗是 PotPlayer
          2. 等待剪貼簿更新
          3. 讀取時間戳
          4. 讀取視窗標題取得影片 basename
          5. 依序判斷 start / end
          6. 寫入 .marks
        """
        # 1. 取得目前前景視窗資訊
        hwnd, title, class_name = utils.get_foreground_window_info()

        self._debug_print(f"[DEBUG] Foreground window: hwnd={hwnd}, title='{title}', class='{class_name}'")

        if hwnd is None:
            # 沒有前景視窗，直接忽略
            return

        # 嚴格只接受 PotPlayer 類型的視窗
        if POTPLAYER_CLASS_KEYWORD not in class_name:
            # 若你未來希望「任何視窗的中鍵都記錄」，可以拿掉這段判斷
            self._debug_print(
                "[DEBUG] Foreground is not PotPlayer, ignore middle-click."
            )
            return

        # 2. 等待 PotPlayer 把時間寫入剪貼簿
        time.sleep(CLIPBOARD_DELAY_SEC)

        # 3. 從剪貼簿讀取時間戳
        timestamp = utils.get_clipboard_text()
        if not timestamp:
            timestamp = "UNKNOWN"

        # 4. 從視窗標題解析影片 basename
        video_basename = utils.extract_video_basename_from_title(title)
        if not video_basename:
            video_basename = "unknown_video"

        # 5. 取得目前行數，決定 start / end
        line_count = self._get_line_count(video_basename)
        tag = "start" if line_count % 2 == 0 else "end"

        # 6. 寫入 .marks 檔
        marks_path = utils.build_marks_path(self.marks_dir, video_basename)
        utils.append_mark_line(marks_path, timestamp, tag)

        # 更新快取
        self._increment_line_count(video_basename)

        self._debug_print(
            f"[DEBUG] Append mark: file='{os.path.basename(marks_path)}', "
            f"line={line_count + 1}, value='{timestamp}, {tag}'"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="VideoClipper background marker - listen to middle mouse clicks and write .marks files."
    )
    default_marks_dir = os.path.join(
        os.path.expanduser("~"), "OneDrive","Desktop", "VideoMarks"
    )

    parser.add_argument(
        "--marks-dir",
        type=str,
        default=default_marks_dir,
        help=f"Directory to store .marks files (default: {default_marks_dir})",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug output.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    state = MarkerState(marks_dir=args.marks_dir, debug=args.debug)

    if state.debug:
        print(f"[DEBUG] Marks directory: {state.marks_dir}")
        print("[DEBUG] Waiting for middle mouse button events...")

    # 啟動全域滑鼠監聽
    with mouse.Listener(on_click=state.handle_click) as listener:
        listener.join()


if __name__ == "__main__":
    main()
