# generate_clips.py
#
# 預設使用方式（建議）：
#   1. 將一組 <video> + <video>.marks 放在專案根目錄下的 ./video_source/
#   2. 在專案根目錄執行：
#        python -m clip_generator.generate_clips
#
# 預設行為：
#   - 自動在 ./video_source/ 裡尋找「唯一一組」影片檔 + .marks
#   - 預設輸出也寫回同一個 ./video_source/ 目錄
#   - align_mode 預設為 "keyframe"
#   - dry-run 預設關閉（直接跑 ffmpeg）
#
# 若需要覆寫：
#   - --video / --marks / --out-dir 仍可自訂路徑
#   - 若任一有指定，auto-detect 的工作目錄改為當前工作目錄（os.getcwd()）

import argparse
import os
import subprocess
import sys
from typing import List, Dict, Any, Tuple, Optional

from . import alignment


VIDEO_EXT_WHITELIST = {".mp4", ".ts", ".mkv", ".mov", ".avi", ".flv", ".m4v"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="VideoClipper clip generator - use .marks file to cut video into clips."
    )
    parser.add_argument(
        "--video",
        type=str,
        help="Path to input video file. If omitted, auto-detect in default source directory.",
    )
    parser.add_argument(
        "--marks",
        type=str,
        help="Path to .marks file. If omitted, auto-detect in default source directory.",
    )
    parser.add_argument(
        "--out-dir",
        type=str,
        default=None,
        help="Directory to store output clips. "
             "Default: same as source directory used for auto-detection.",
    )
    parser.add_argument(
        "--align-mode",
        type=str,
        choices=["none", "keyframe"],
        default="keyframe",
        help='Boundary alignment mode: "none" or "keyframe" (default: keyframe).',
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="If set, will only print planned clips and ffmpeg commands without executing.",
    )
    return parser.parse_args()


def _error(msg: str) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr)


def _warn(msg: str) -> None:
    print(f"[WARN] {msg}", file=sys.stderr)


def _info(msg: str) -> None:
    print(f"[INFO] {msg}")


def _auto_detect_video_and_marks(
    video_arg: Optional[str],
    marks_arg: Optional[str],
    working_dir: str,
) -> Tuple[str, str]:
    """
    根據使用者提供的參數 + 指定的 working_dir 自動決定要用的 video / marks。

    規則：
      1) 若 video_arg / marks_arg 都有提供 → 直接使用（檢查存在）。
      2) 若只給 video：
           - 優先找 <video_basename>.marks in working_dir
           - 否則若 working_dir 中只有一個 .marks → 用那個
           - 否則報錯，要求指定 --marks
      3) 若都沒給：
           - 掃描 working_dir：
               * 找出所有影片檔（在白名單 ext 中）
               * 找出所有 .marks 檔
           - 若剛好 1 個 video + 1 個 marks → 用這組
           - 否則報錯，要求指定
    """
    cwd = working_dir

    # 如果兩個都有指定，直接用
    if video_arg and marks_arg:
        video_path = os.path.abspath(video_arg)
        marks_path = os.path.abspath(marks_arg)
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video not found: {video_path}")
        if not os.path.exists(marks_path):
            raise FileNotFoundError(f".marks file not found: {marks_path}")
        return video_path, marks_path

    # 列出 working_dir 內檔案
    files = [
        f for f in os.listdir(cwd)
        if os.path.isfile(os.path.join(cwd, f))
    ]
    video_candidates = [
        f for f in files
        if os.path.splitext(f)[1].lower() in VIDEO_EXT_WHITELIST
    ]
    marks_candidates = [
        f for f in files
        if f.lower().endswith(".marks")
    ]

    # 若只有 video_arg，沒 marks_arg
    if video_arg and not marks_arg:
        video_path = os.path.abspath(video_arg)
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video not found: {video_path}")

        base, _ext = os.path.splitext(os.path.basename(video_path))
        preferred_marks = os.path.join(cwd, f"{base}.marks")
        if os.path.exists(preferred_marks):
            marks_path = os.path.abspath(preferred_marks)
            return video_path, marks_path

        # 再退一步：若只有一個 .marks，就用那個
        if len(marks_candidates) == 1:
            marks_path = os.path.abspath(os.path.join(cwd, marks_candidates[0]))
            return video_path, marks_path

        raise ValueError(
            "Cannot auto-detect .marks file: "
            "no matching <video_basename>.marks and not exactly one .marks in source directory. "
            "Please specify --marks explicitly."
        )

    # 若兩個都沒有指定 → 完全依賴 auto-detect in working_dir
    if not video_arg and not marks_arg:
        if len(video_candidates) == 0:
            raise FileNotFoundError(
                f"No video file found in source directory: {cwd}. "
                f"Supported extensions: {sorted(VIDEO_EXT_WHITELIST)}"
            )
        if len(marks_candidates) == 0:
            raise FileNotFoundError(
                f"No .marks file found in source directory: {cwd}. "
                "Please generate a .marks file first."
            )
        if len(video_candidates) > 1:
            raise ValueError(
                f"Multiple video files found in source directory: {video_candidates}. "
                "Please specify --video explicitly."
            )
        if len(marks_candidates) > 1:
            raise ValueError(
                f"Multiple .marks files found in source directory: {marks_candidates}. "
                "Please specify --marks explicitly."
            )

        video_path = os.path.abspath(os.path.join(cwd, video_candidates[0]))
        marks_path = os.path.abspath(os.path.join(cwd, marks_candidates[0]))
        return video_path, marks_path

    # 理論上前面已涵蓋所有組合，這裡只是保險
    raise ValueError("Unexpected combination of video/marks arguments.")


def _parse_marks_file(path: str) -> List[Dict[str, Any]]:
    """
    讀取並解析 .marks 檔。

    回傳：
      list of dict:
        {
          "line_no": int,
          "timestamp_raw": str,  # 例如 "00:41:58" 或 "UNKNOWN"
          "tag": "start" | "end"
        }

    若格式有問題會直接 raise ValueError。
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f".marks file not found: {path}")

    entries: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for idx, line in enumerate(f, start=1):
            raw = line.strip()
            if not raw:
                # 空行直接跳過，不計入
                continue

            # 期待格式： "<timestamp>, <tag>"
            if "," not in raw:
                raise ValueError(f"Line {idx}: missing comma. Content: '{raw}'")

            ts_part, tag_part = raw.split(",", 1)
            ts = ts_part.strip()
            tag = tag_part.strip().lower()

            if tag not in ("start", "end"):
                raise ValueError(f"Line {idx}: invalid tag '{tag}'. Expected 'start' or 'end'.")

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

    # 驗證順序：必須依序 start, end, start, end, ...
    for i in range(0, len(entries), 2):
        e1 = entries[i]
        e2 = entries[i + 1]
        if e1["tag"] != "start" or e2["tag"] != "end":
            raise ValueError(
                f"Lines {e1['line_no']} and {e2['line_no']} are not in 'start, end' order "
                f"(got '{e1['tag']}', '{e2['tag']}')."
            )

    return entries


def _parse_timestamp_to_seconds(ts: str) -> float:
    """
    將下列格式轉為秒數 (float)：
      - "SS"
      - "MM:SS"
      - "HH:MM:SS"
    最後一段可帶小數，例如 "12.345"。

    若格式不符合預期，會 raise ValueError。
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
            # 超過 3 段就視為錯誤
            raise ValueError
    except ValueError:
        raise ValueError(f"Invalid timestamp format or value: '{ts}'")



def _format_seconds_to_timestamp(sec: float) -> str:
    """
    將秒數 (float) 格式化成 "HH:MM:SS.mmm"（或 "HH:MM:SS" 若毫秒接近 0）。
    ffmpeg 接受這種格式。
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


def _build_segments_from_entries(
    entries: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    將解析好的 entries 兩兩配對成 segments。

    回傳:
      (valid_segments, skipped_segments_due_to_unknown)
    """
    valid_segments: List[Dict[str, Any]] = []
    skipped_unknown: List[Dict[str, Any]] = []

    pair_index = 0
    for i in range(0, len(entries), 2):
        pair_index += 1
        start_entry = entries[i]
        end_entry = entries[i + 1]

        ts_start = start_entry["timestamp_raw"]
        ts_end = end_entry["timestamp_raw"]

        if ts_start == "UNKNOWN" or ts_end == "UNKNOWN":
            # 這組 pair 直接略過
            skipped_unknown.append(
                {
                    "pair_index": pair_index,
                    "start_line": start_entry["line_no"],
                    "end_line": end_entry["line_no"],
                    "timestamp_start": ts_start,
                    "timestamp_end": ts_end,
                }
            )
            continue

        seg = {
            "pair_index": pair_index,
            "start_line": start_entry["line_no"],
            "end_line": end_entry["line_no"],
            "start_str": ts_start,
            "end_str": ts_end,
        }
        valid_segments.append(seg)

    return valid_segments, skipped_unknown


def _convert_segments_to_seconds(segments: List[Dict[str, Any]]) -> None:
    """
    將 segments 裡的 start_str / end_str 轉成 start_sec / end_sec。
    若 start_sec >= end_sec，會 raise ValueError。
    """
    for seg in segments:
        s_str = seg["start_str"]
        e_str = seg["end_str"]

        try:
            s_sec = _parse_timestamp_to_seconds(s_str)
            e_sec = _parse_timestamp_to_seconds(e_str)
        except ValueError as e:
            raise ValueError(
                f"Segment #{seg['pair_index']} (lines {seg['start_line']}-{seg['end_line']}): {e}"
            )

        if s_sec >= e_sec:
            raise ValueError(
                f"Segment #{seg['pair_index']} has start >= end: "
                f"start={s_str} ({s_sec}), end={e_str} ({e_sec}). "
                f"Lines {seg['start_line']}-{seg['end_line']}."
            )

        seg["start_sec"] = s_sec
        seg["end_sec"] = e_sec


def _run_ffmpeg_cut(
    video_path: str,
    out_path: str,
    start_sec: float,
    end_sec: float,
) -> int:
    """
    呼叫 ffmpeg 做無損剪輯 (-c copy -map 0)，回傳 ffmpeg 的 return code。
    """
    start_ts = _format_seconds_to_timestamp(start_sec)
    end_ts = _format_seconds_to_timestamp(end_sec)

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

    _info(f"ffmpeg cmd: {' '.join(cmd)}")

    result = subprocess.run(cmd)
    return result.returncode


def main() -> None:
    args = parse_args()

    # 決定這次 auto-detect 使用的 source directory：
    #   - 若沒給 --video / --marks → 預設使用 專案根目錄下的 ./video_source
    #   - 若有給任一者 → working_dir 改為當前工作目錄 (os.getcwd())
    if args.video or args.marks:
        source_dir = os.getcwd()
        _info(f"Source directory (by explicit paths): {source_dir}")
    else:
        # 預設：專案根目錄執行時，source_dir = ./video_source
        source_dir = os.path.join(os.getcwd(), "video_source")
        _info(f"Source directory (default): {source_dir}")

    if not os.path.isdir(source_dir):
        _error(f"Source directory does not exist: {source_dir}")
        sys.exit(1)

    # 自動決定要用的 video / marks
    try:
        video_path, marks_path = _auto_detect_video_and_marks(
            video_arg=args.video,
            marks_arg=args.marks,
            working_dir=source_dir,
        )
    except Exception as e:
        _error(f"Failed to auto-detect video/marks: {e}")
        sys.exit(1)

    # 決定輸出目錄：
    #   - 若未指定 --out-dir → 預設輸出到 source_dir（也就是 video_source）
    #   - 若有指定 → 若為相對路徑，則以當前工作目錄為基準
    if args.out_dir is None:
        out_dir = source_dir
    else:
        if os.path.isabs(args.out_dir):
            out_dir = args.out_dir
        else:
            out_dir = os.path.join(os.getcwd(), args.out_dir)

    align_mode = args.align_mode
    dry_run = args.dry_run

    _info(f"Using video: {video_path}")
    _info(f"Using marks: {marks_path}")
    _info(f"Output directory: {out_dir}")

    os.makedirs(out_dir, exist_ok=True)

    # 解析 .marks
    try:
        entries = _parse_marks_file(marks_path)
    except Exception as e:
        _error(f"Failed to parse .marks: {e}")
        sys.exit(1)

    _info(f"Parsed {len(entries)} valid entries from .marks.")

    # 兩兩配對，處理 UNKNOWN
    valid_segments, skipped_unknown = _build_segments_from_entries(entries)

    if len(skipped_unknown) > 0:
        for seg in skipped_unknown:
            _warn(
                f"Skip pair #{seg['pair_index']} (lines {seg['start_line']}-{seg['end_line']}): "
                f"contains UNKNOWN timestamp ({seg['timestamp_start']}, {seg['timestamp_end']})."
            )

    if len(valid_segments) == 0:
        _warn("No valid segments left after skipping UNKNOWN entries. Nothing to do.")
        sys.exit(0)

    _info(f"{len(valid_segments)} valid segment(s) will be processed.")

    # 轉成秒數並檢查 start < end
    try:
        _convert_segments_to_seconds(valid_segments)
    except Exception as e:
        _error(f"Failed to convert timestamps: {e}")
        sys.exit(1)

    # 套用 alignment
    try:
        aligned_segments = alignment.align_segments_with_keyframes(
            video_path=video_path,
            segments=valid_segments,
            mode=align_mode,
        )
    except Exception as e:
        _error(f"Alignment error: {e}")
        sys.exit(1)

    # 決定輸出檔案副檔名：沿用原影片
    _, video_ext = os.path.splitext(video_path)
    if not video_ext:
        video_ext = ".mp4"

    success_count = 0
    fail_count = 0
    clip_index = 0

    for seg in aligned_segments:
        clip_index += 1
        pair_idx = seg["pair_index"]

        s_raw = seg["start_str"]
        e_raw = seg["end_str"]
        s_final = seg["start_final"]
        e_final = seg["end_final"]
        used_align = seg["used_keyframe_alignment"]
        note = seg.get("alignment_note", "")

        s_final_ts = _format_seconds_to_timestamp(s_final)
        e_final_ts = _format_seconds_to_timestamp(e_final)

        _info(
            f"Clip #{clip_index} (pair #{pair_idx}, lines {seg['start_line']}-{seg['end_line']}): "
            f"raw [{s_raw} -> {e_raw}], final [{s_final_ts} -> {e_final_ts}], "
            f"align_used={used_align}, note={note}"
        )

        out_name = f"clip_{clip_index:03d}{video_ext}"
        out_path = os.path.join(out_dir, out_name)

        if dry_run:
            _info(f"(dry-run) Would write: {out_path}")
            continue

        ret = _run_ffmpeg_cut(video_path, out_path, s_final, e_final)
        if ret == 0:
            success_count += 1
        else:
            fail_count += 1
            _error(f"ffmpeg failed for clip #{clip_index}, output: {out_path}")

    if dry_run:
        _info("Dry-run mode: no actual clips were generated.")
    else:
        _info(f"Done. Success: {success_count}, Fail: {fail_count}.")


if __name__ == "__main__":
    main()
