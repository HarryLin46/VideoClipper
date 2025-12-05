# marker.py
#
# Global middle mouse listener + PotPlayer integration.
# 每次你在 PotPlayer 按下滑鼠中鍵，PotPlayer 會把目前播放時間寫入剪貼簿，
# 這個腳本則會：
#   1) 確認前景視窗是 PotPlayer
#   2) 從剪貼簿讀出時間字串
#   3) 讀取 PotPlayer 視窗標題當作檔名
#   4) 依序寫入 <ProjectRoot>/VideoMarks/<video_basename>.marks
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
from typing import Optional

from pynput import mouse

from background_marker import utils


# 剪貼簿讀取相關時間參數（秒）
# 第一次等 PotPlayer 寫入剪貼簿的時間
CLIPBOARD_INITIAL_DELAY_SEC = 1.0
# 在偵測到剪貼簿內容尚未更新時，每次重試前的等待間隔
CLIPBOARD_RETRY_INTERVAL_SEC = 0.2
# 從第一次等待開始計算的總等待上限
CLIPBOARD_MAX_WAIT_SEC = 3.0

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

    def _get_last_mark_timestamp(self, marks_path: str) -> Optional[str]:
        """
        讀取指定 .marks 檔最後一行的 timestamp。

        預期每行格式為： "<timestamp>, <tag>"
        若檔案不存在、為空或格式不符合，回傳 None。
        """
        if not os.path.exists(marks_path):
            return None

        try:
            with open(marks_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception as e:
            self._debug_print(f"[DEBUG] Failed to read marks file '{marks_path}': {e}")
            return None

        # 從最後一行往上找第一個非空行
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            # 預期格式："timestamp, tag"
            parts = [p.strip() for p in line.split(",", 1)]
            if not parts:
                continue
            ts = parts[0]
            if ts:
                return ts

        return None

    def _get_timestamp_with_retry(self, reference_timestamp: Optional[str]) -> str:
        """
        依據剪貼簿內容取得本次標記要寫入的 timestamp。

        邏輯：
          1. 先等待 CLIPBOARD_INITIAL_DELAY_SEC 秒，讀一次剪貼簿。
          2. 若沒有 reference_timestamp（表示這支影片尚無任何標記），
             或第一次讀到的 timestamp != reference_timestamp，則直接使用。
          3. 若第一次讀到的 timestamp == reference_timestamp，代表很可能尚未更新，
             啟動重試機制：
               - 每次等待 CLIPBOARD_RETRY_INTERVAL_SEC 秒後再讀一次剪貼簿
               - 總等待時間（含第一次 0.8 秒）不得超過 CLIPBOARD_MAX_WAIT_SEC
               - 若在上限內讀到與 reference 不同的值，立刻採用
               - 若到上限都沒更新，仍使用目前值（對應需求中選項 A）
        """
        total_wait = 0.0

        # 第一次等待
        time.sleep(CLIPBOARD_INITIAL_DELAY_SEC)
        total_wait += CLIPBOARD_INITIAL_DELAY_SEC

        timestamp = utils.get_clipboard_text()
        if not timestamp:
            timestamp = "UNKNOWN"

        # 若沒有 reference，可以直接使用，不做重試
        if reference_timestamp is None:
            self._debug_print(
                f"[DEBUG] No previous mark, use initial clipboard timestamp '{timestamp}'."
            )
            return timestamp

        # 若第一次就已不同，也不需要重試
        if timestamp != reference_timestamp:
            self._debug_print(
                f"[DEBUG] Clipboard timestamp differs from last mark immediately "
                f"('{timestamp}' != '{reference_timestamp}'), use it."
            )
            return timestamp

        # 若到這邊，代表 timestamp == reference_timestamp，啟動重試
        self._debug_print(
            f"[DEBUG] Clipboard timestamp '{timestamp}' == last mark '{reference_timestamp}', "
            f"start retry loop (max wait {CLIPBOARD_MAX_WAIT_SEC}s)."
        )

        # 在總等待時間不超過上限的前提下重試
        while (
            timestamp == reference_timestamp
            and total_wait + CLIPBOARD_RETRY_INTERVAL_SEC <= CLIPBOARD_MAX_WAIT_SEC
        ):
            time.sleep(CLIPBOARD_RETRY_INTERVAL_SEC)
            total_wait += CLIPBOARD_RETRY_INTERVAL_SEC

            new_ts = utils.get_clipboard_text()
            if not new_ts:
                new_ts = "UNKNOWN"
            timestamp = new_ts

            self._debug_print(
                f"[DEBUG] Retry clipboard read after {total_wait:.1f}s: '{timestamp}'."
            )

            # 若已不同，可以直接跳出迴圈
            if timestamp != reference_timestamp:
                break

        # 走到這裡，有兩種情況：
        # 1) 在時間上限內讀到不同的 timestamp → 使用更新後的值
        # 2) 即使用到上限，仍然等於 reference_timestamp → 仍然使用該值（需求選項 A）
        if timestamp == reference_timestamp:
            self._debug_print(
                f"[DEBUG] Clipboard timestamp stayed the same as last mark "
                f"('{timestamp}') after {total_wait:.1f}s, use it anyway."
            )
        else:
            self._debug_print(
                f"[DEBUG] Clipboard timestamp updated to '{timestamp}' "
                f"after {total_wait:.1f}s, use new value."
            )

        return timestamp

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
          2. 根據目前 .marks 檔最後一個 timestamp 作為 reference
          3. 以重試機制讀取剪貼簿時間戳
          4. 讀取視窗標題取得影片 basename
          5. 依序判斷 start / end
          6. 寫入 .marks
        """
        # 1. 取得目前前景視窗資訊
        hwnd, title, class_name = utils.get_foreground_window_info()

        self._debug_print(
            f"[DEBUG] Foreground window: hwnd={hwnd}, title='{title}', class='{class_name}'"
        )

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

        # 2. 從視窗標題解析影片 basename
        video_basename = utils.extract_video_basename_from_title(title)
        if not video_basename:
            video_basename = "unknown_video"

        # 3. 準備 .marks 路徑與目前行數
        marks_path = utils.build_marks_path(self.marks_dir, video_basename)
        line_count = self._get_line_count(video_basename)

        # 4. 決定這次標記的 tag
        tag = "start" if line_count % 2 == 0 else "end"

        # 5. 取得上一個標記的 timestamp，作為 reference（若有）
        reference_timestamp: Optional[str] = None
        if line_count > 0:
            reference_timestamp = self._get_last_mark_timestamp(marks_path)
            self._debug_print(
                f"[DEBUG] Current line_count={line_count}, last mark timestamp='{reference_timestamp}'."
            )
        else:
            self._debug_print(
                f"[DEBUG] No existing marks for '{video_basename}'. This will be the first mark."
            )

        # 6. 根據 reference_timestamp，以重試機制讀取剪貼簿時間戳
        timestamp = self._get_timestamp_with_retry(reference_timestamp)
        if not timestamp:
            timestamp = "UNKNOWN"

        # 7. 寫入 .marks 檔
        utils.append_mark_line(marks_path, timestamp, tag)

        # 8. 更新快取
        self._increment_line_count(video_basename)

        self._debug_print(
            f"[DEBUG] Append mark: file='{os.path.basename(marks_path)}', "
            f"line={line_count + 1}, value='{timestamp}, {tag}'"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="VideoClipper background marker - listen to middle mouse clicks and write .marks files."
    )

    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(current_dir)
    default_marks_dir = os.path.join(project_root, "VideoMarks")

    os.makedirs(default_marks_dir, exist_ok=True)

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
