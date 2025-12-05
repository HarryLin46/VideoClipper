# clip_generator/core.py
#
# 這個模組提供「核心功能」給 CLI / GUI 共用：
#   - 從影片路徑與 .marks 讀取所有 clips、做 alignment
#   - 產生可微調的 clips list (start_sec / end_sec)
#   - 根據 clips list 呼叫 ffmpeg 進行剪輯
#
# 不處理任何 UI / argparse。

from __future__ import annotations

import dataclasses
import os
import subprocess
from typing import List, Dict, Any, Tuple, Optional

from clip_generator import alignment


@dataclasses.dataclass
class ClipSegment:
    """單一剪輯片段的資訊（給 GUI / CLI 使用）"""
    index: int                  # 第幾段（1-based）
    start_sec: float            # 使用者最後要拿來剪的起點（秒）
    end_sec: float              # 使用者最後要拿來剪的終點（秒）
    raw_start_str: str          # .marks 原始起點字串
    raw_end_str: str            # .marks 原始終點字串
    aligned_start_sec: float    # alignment 算出的起點（秒）
    aligned_end_sec: float      # alignment 算出的終點（秒）
    used_keyframe_alignment: bool
    alignment_note: str = ""


# ======== 內部工具 ========

def _parse_timestamp_to_seconds(ts: str) -> float:
    """
    支援以下時間格式：
      - "SS"
      - "MM:SS"
      - "HH:MM:SS"
    最後一段可帶小數，例如 "12.345"。

    若格式不合法，會 raise ValueError。
    """
    ts = ts.strip()
    if not ts:
        raise ValueError("Empty timestamp")

    parts = ts.split(":")

    try:
        if len(parts) == 1:
            # SS
            s = float(parts[0])
            if s < 0:
                raise ValueError
            return s

        elif len(parts) == 2:
            # MM:SS
            m_str, s_str = parts
            m = int(m_str)
            s = float(s_str)
            if m < 0 or m >= 60 or s < 0 or s >= 60:
                raise ValueError
            return m * 60.0 + s

        elif len(parts) == 3:
            # HH:MM:SS
            h_str, m_str, s_str = parts
            h = int(h_str)
            m = int(m_str)
            s = float(s_str)
            if h < 0 or m < 0 or m >= 60 or s < 0 or s >= 60:
                raise ValueError
            return h * 3600.0 + m * 60.0 + s

        else:
            raise ValueError
    except ValueError:
        raise ValueError(f"Invalid timestamp format or value: '{ts}'")


def _parse_marks_file(path: str) -> List[Dict[str, Any]]:
    """
    讀取 .marks，解析成 entries list：
      {
        "line_no": int,
        "timestamp_raw": str,
        "tag": "start" | "end",
      }

    規則（與 CLI 版本一致）：
      - 空行忽略
      - 格式必須為 "timestamp, tag"
      - tag 只能是 start / end
      - 總行數必須為偶數
      - 必須依序出現 start, end, start, end, ...
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f".marks file not found: {path}")

    entries: List[Dict[str, Any]] = []

    with open(path, "r", encoding="utf-8") as f:
        for idx, line in enumerate(f, start=1):
            raw = line.strip()
            if not raw:
                continue

            if "," not in raw:
                raise ValueError(f"Line {idx}: missing comma. Content: '{raw}'")

            ts_part, tag_part = raw.split(",", 1)
            ts = ts_part.strip()
            tag = tag_part.strip().lower()

            if tag not in ("start", "end"):
                raise ValueError(
                    f"Line {idx}: invalid tag '{tag}'. Expected 'start' or 'end'."
                )

            entries.append(
                {
                    "line_no": idx,
                    "timestamp_raw": ts,
                    "tag": tag,
                }
            )

    if len(entries) == 0:
        raise ValueError("No valid entries found in .marks file.")

    if len(entries) % 2 != 0:
        raise ValueError(
            f".marks must contain even number of lines (pairs of start/end). "
            f"Currently got {len(entries)} valid non-empty lines."
        )

    # 驗證順序：start, end, start, end, ...
    for i in range(0, len(entries), 2):
        e1 = entries[i]
        e2 = entries[i + 1]
        if e1["tag"] != "start" or e2["tag"] != "end":
            raise ValueError(
                f"Lines {e1['line_no']} and {e2['line_no']} are not in 'start, end' order "
                f"(got '{e1['tag']}', '{e2['tag']}')."
            )

    return entries


def _build_segments_from_entries(
    entries: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    把 entries 兩兩配對成 segments（alignment 前的原始 segments）。

    回傳 list，每個元素包含：
      - pair_index
      - start_line, end_line
      - start_str, end_str
      - start_sec, end_sec
    """
    segments: List[Dict[str, Any]] = []
    pair_index = 0

    for i in range(0, len(entries), 2):
        pair_index += 1
        start_entry = entries[i]
        end_entry = entries[i + 1]

        ts_start = start_entry["timestamp_raw"]
        ts_end = end_entry["timestamp_raw"]

        if ts_start == "UNKNOWN" or ts_end == "UNKNOWN":
            # 這種 pair 直接視為錯誤，不自動忽略，讓使用者自己處理
            raise ValueError(
                f"Pair #{pair_index} (lines {start_entry['line_no']}-{end_entry['line_no']}) "
                f"contains UNKNOWN timestamp ({ts_start}, {ts_end})."
            )

        try:
            s_sec = _parse_timestamp_to_seconds(ts_start)
            e_sec = _parse_timestamp_to_seconds(ts_end)
        except ValueError as e:
            raise ValueError(
                f"Pair #{pair_index} (lines {start_entry['line_no']}-{end_entry['line_no']}): {e}"
            )

        if s_sec >= e_sec:
            raise ValueError(
                f"Pair #{pair_index} has start >= end: "
                f"start={ts_start} ({s_sec}), end={ts_end} ({e_sec}). "
                f"Lines {start_entry['line_no']}-{end_entry['line_no']}."
            )

        segments.append(
            {
                "pair_index": pair_index,
                "start_line": start_entry["line_no"],
                "end_line": end_entry["line_no"],
                "start_str": ts_start,
                "end_str": ts_end,
                "start_sec": s_sec,
                "end_sec": e_sec,
            }
        )

    return segments


def _format_seconds_to_timestamp(sec: float) -> str:
    """
    給 ffmpeg / 顯示用的時間格式：HH:MM:SS或HH:MM:SS.mmm
    """
    if sec < 0:
        sec = 0.0

    total_ms = int(round(sec * 1000))
    ms = total_ms % 1000
    total_sec = total_ms // 1000

    h = total_sec // 3600
    m = (total_sec % 3600) // 60
    s = total_sec % 60

    if ms == 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    else:
        return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


# ======== 對外主要函式 ========

def load_segments_from_marks(
    video_path: str,
    marks_path: str,
    align_mode: str = "keyframe",
) -> List[ClipSegment]:
    """
    給 GUI / CLI 使用：

    根據 video + .marks，回傳一組 ClipSegment list（已做 boundary alignment）。
    若 .marks 格式有問題會 raise Exception（讓上層決定要不要顯示錯誤視窗）。

    align_mode:
      - "keyframe": 使用 alignment 模組，碰到怪情況再 fallback 回原始時間
      - "none": 完全照原始 .marks 時間（不做 keyframe 對齊）
    """
    video_path = os.path.abspath(video_path)
    marks_path = os.path.abspath(marks_path)

    entries = _parse_marks_file(marks_path)
    raw_segments = _build_segments_from_entries(entries)

    # 呼叫 alignment 模組，取得 align 後的 segments
    aligned = alignment.align_segments_with_keyframes(
        video_path=video_path,
        segments=raw_segments,
        mode=align_mode,
    )

    result: List[ClipSegment] = []

    for seg in aligned:
        start_final = float(seg["start_final"])
        end_final = float(seg["end_final"])

        result.append(
            ClipSegment(
                index=seg["pair_index"],
                start_sec=start_final,
                end_sec=end_final,
                raw_start_str=seg["start_str"],
                raw_end_str=seg["end_str"],
                aligned_start_sec=start_final,
                aligned_end_sec=end_final,
                used_keyframe_alignment=bool(seg["used_keyframe_alignment"]),
                alignment_note=str(seg.get("alignment_note", "")),
            )
        )

    # 依 index 排序保險一下
    result.sort(key=lambda c: c.index)
    return result


def run_ffmpeg_for_segments(
    video_path: str,
    segments: List[ClipSegment],
    out_dir: str,
) -> Tuple[int, int]:
    """
    根據 segments list 實際呼叫 ffmpeg 進行剪接。

    使用完全無損剪接（-c copy -map 0），輸出檔名為：
        clip_001.<ext>, clip_002.<ext>, ...

    回傳 (success_count, fail_count)。
    """
    video_path = os.path.abspath(video_path)
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)

    _, video_ext = os.path.splitext(video_path)
    if not video_ext:
        video_ext = ".mp4"

    success = 0
    fail = 0

    for i, seg in enumerate(segments, start=1):
        start_ts = _format_seconds_to_timestamp(seg.start_sec)
        end_ts = _format_seconds_to_timestamp(seg.end_sec)

        out_name = f"clip_{i:03d}{video_ext}"
        out_path = os.path.join(out_dir, out_name)

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            video_path,
            "-ss",
            start_ts,
            "-to",
            end_ts,
            "-c",
            "copy",
            "-map",
            "0",
            out_path,
        ]

        print(f"[INFO] ffmpeg cmd: {' '.join(cmd)}")

        result = subprocess.run(cmd)
        if result.returncode == 0:
            success += 1
        else:
            fail += 1
            print(f"[ERROR] ffmpeg failed for clip #{i}, output: {out_path}")

    return success, fail
