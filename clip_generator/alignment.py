# alignment.py
#
# 負責 boundary alignment 的邏輯：
#  - align_mode = "none"     → 不調整，直接用原本 start/end
#  - align_mode = "keyframe" → 以影片 keyframe 做對齊，
#                              如果對齊結果不合理，則回退成 "none"

import subprocess
from bisect import bisect_left, bisect_right
from typing import List, Dict, Any


def _run_ffprobe_for_keyframes(video_path: str) -> List[float]:
    """
    呼叫 ffprobe 取得影片中所有 keyframe 的時間 (秒)，只取 video stream。
    使用:
      ffprobe -select_streams v:0 -skip_frame nokey -show_frames
              -show_entries frame=pkt_pts_time -of csv=p=0 -v error

    回傳：遞增排序的 float list。
    """
    cmd = [
        "ffprobe",
        "-select_streams",
        "v:0",
        "-skip_frame",
        "nokey",
        "-show_frames",
        "-show_entries",
        "frame=pkt_pts_time",
        "-of",
        "csv=p=0",
        "-v",
        "error",
        video_path,
    ]

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        # ffprobe 失敗就視為沒有 keyframe 資訊
        return []

    keyframes: List[float] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            t = float(line)
            keyframes.append(t)
        except ValueError:
            # 有怪格式就略過那一行
            continue

    # ffprobe 預期已經遞增，但保險起見再 sort 一次
    keyframes.sort()
    return keyframes


def _align_single_segment_with_keyframes(
    start_sec: float,
    end_sec: float,
    keyframes: List[float],
) -> (float, float, bool):
    """
    對單一 segment 做 keyframe alignment。

    規則：
      - start_aligned: 第一個 >= start_sec 的 keyframe；
                       若 start_sec > 所有 keyframe，取最後一個 keyframe。
      - end_aligned:   最後一個 <= end_sec 的 keyframe；
                       若 end_sec < 所有 keyframe，取第一個 keyframe。

    回傳:
      (start_final, end_final, used_alignment)

      - used_alignment = True  → 有使用 keyframe 對齊（結果合理）
      - used_alignment = False → 對齊結果不合理，需回退使用原始 start/end
    """
    if not keyframes or start_sec >= end_sec:
        # 沒 keyframe 或本身就不合理，呼叫端會另外處理
        return start_sec, end_sec, False

    # 尋找 start_aligned: 第一個 >= start_sec
    idx_start = bisect_left(keyframes, start_sec)
    if idx_start >= len(keyframes):
        # start 在所有 keyframe 之後 → 取最後一個
        start_aligned = keyframes[-1]
    else:
        start_aligned = keyframes[idx_start]

    # 尋找 end_aligned: 最後一個 <= end_sec
    idx_end = bisect_right(keyframes, end_sec) - 1
    if idx_end < 0:
        # end 在所有 keyframe 之前 → 取第一個
        end_aligned = keyframes[0]
    else:
        end_aligned = keyframes[idx_end]

    # 若對齊結果導致長度不合理，回退
    if start_aligned >= end_aligned:
        return start_sec, end_sec, False

    return start_aligned, end_aligned, True


def align_segments_with_keyframes(
    video_path: str,
    segments: List[Dict[str, Any]],
    mode: str = "keyframe",
) -> List[Dict[str, Any]]:
    """
    對一組 segments 做 boundary alignment。

    輸入:
      video_path: 影片路徑
      segments:   list of dict，每個 dict 至少包含：
                    {
                        "pair_index": int,      # 第幾組 pair (對應 marks 中第 N 組)
                        "start_sec": float,
                        "end_sec": float,
                        ...
                    }
      mode:       "none" 或 "keyframe"

    回傳:
      新的 segments list，每個 dict 會多出：
        - "start_final": float
        - "end_final": float
        - "used_keyframe_alignment": bool
        - "alignment_note": str (optional 說明)
    """
    if mode == "none":
        # 直接用原始 start/end，不做任何對齊
        for seg in segments:
            seg["start_final"] = seg["start_sec"]
            seg["end_final"] = seg["end_sec"]
            seg["used_keyframe_alignment"] = False
            seg["alignment_note"] = "align_mode=none"
        return segments

    if mode != "keyframe":
        raise ValueError(f"Unsupported align_mode: {mode}")

    # 取得影片 keyframe 時間
    keyframes = _run_ffprobe_for_keyframes(video_path)
    if not keyframes:
        # 沒拿到 keyframe → 全部回退成原始時間
        for seg in segments:
            seg["start_final"] = seg["start_sec"]
            seg["end_final"] = seg["end_sec"]
            seg["used_keyframe_alignment"] = False
            seg["alignment_note"] = "no_keyframe_info_fallback"
        return segments

    # 逐段對齊
    for seg in segments:
        s = seg["start_sec"]
        e = seg["end_sec"]

        aligned_start, aligned_end, ok = _align_single_segment_with_keyframes(s, e, keyframes)

        if not ok:
            # 對齊結果不合理 → 回退成原始時間
            seg["start_final"] = s
            seg["end_final"] = e
            seg["used_keyframe_alignment"] = False
            seg["alignment_note"] = "alignment_degenerate_fallback"
        else:
            seg["start_final"] = aligned_start
            seg["end_final"] = aligned_end
            seg["used_keyframe_alignment"] = True
            seg["alignment_note"] = "aligned_to_keyframe"

    return segments
