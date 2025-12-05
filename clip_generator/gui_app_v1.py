# clip_generator/gui_app.py
#
# GUI 入口：
#   在專案根目錄執行：
#       python -m clip_generator.gui_app
#
# 需求：
#   pip install PySide6
#
# 功能：
#   - 按鈕選擇影片檔（假設同一層有對應 .marks）
#   - 使用 core.load_segments_from_marks 取得每段 clip 起訖時間
#   - 左側 clip 清單
#   - 右側：
#       * 影片預覽
#       * 三條 slider：
#           - 開始點 slider（可拖拉，控制 start_sec）
#           - 結束點 slider（可拖拉，控制 end_sec）
#           - 播放 slider（可拖拉，控制播放位置）
#       * 一組微調按鈕（-5秒 / +5秒 / -1幀 / +1幀），針對「目前選中的邊界」（start 或 end）
#   - 播放規則：
#       * 調 start 模式：畫面停在 start，播放從 start → end，自動停。
#       * 調 end   模式：畫面停在 end，播放從 end（或 end 前幾秒）往後播放。
#
#   你可以透過 END_PREVIEW_OFFSET_SECONDS 調整「調 end 時播放從 end 前幾秒開始」。
#   預設為 0.0（不提前）。

from __future__ import annotations

import os
import sys
from typing import List, Optional

from PySide6.QtCore import Qt, QUrl, QTimer
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QFileDialog,
    QMessageBox,
    QListWidget,
    QListWidgetItem,
    QLabel,
    QSlider,
    QGroupBox,
)

from clip_generator.core import (
    load_segments_from_marks,
    run_ffmpeg_for_segments,
    ClipSegment,
)

# 你可以自己改這個常數，控制「在調結束點模式時，播放從 end 前幾秒開始」。
# 預設 0.0：從 end 本身開始播放。
END_PREVIEW_OFFSET_SECONDS = 0.0

SLIDER_MAX = 1000  # 所有 slider 統一用 0 ~ 1000 當比例尺。


class VideoClipperWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("VideoClipper - GUI")
        self.resize(1200, 700)

        # 狀態變數
        self.video_path: Optional[str] = None
        self.marks_path: Optional[str] = None
        self.segments: List[ClipSegment] = []
        self.current_index: int = -1  # 目前選中的 clip index (0-based)
        self.active_target: str = "start"  # "start" or "end"
        self.video_duration_ms: int = 0
        self.fps: float = 30.0  # 先假設 30 FPS，未來可改成用 ffprobe 等取得。

        # 目前這個 clip 的顯示/調整視窗範圍（秒）
        self.window_start_sec: float = 0.0
        self.window_end_sec: float = 0.0

        # Qt 播放相關
        self.audio_output = QAudioOutput()
        self.media_player = QMediaPlayer()
        self.media_player.setAudioOutput(self.audio_output)

        # 播放區間控制：僅在「調 start」模式下，用來限制播放到 end 就停。
        self.playback_timer = QTimer(self)
        self.playback_timer.setInterval(100)  # 每 0.1 秒檢查一次
        self.playback_timer.timeout.connect(self._on_playback_timer)

        # 建立 UI
        self._init_ui()

        # MediaPlayer 綁定 video widget 與事件
        self.media_player.setVideoOutput(self.video_widget)
        self.media_player.mediaStatusChanged.connect(self._on_media_status_changed)
        self.media_player.durationChanged.connect(self._on_duration_changed)
        self.media_player.positionChanged.connect(self._on_position_changed)

        # 一開始 clip 相關控制都 disable
        self._set_clip_controls_enabled(False)

    # ======== UI 建構 ========

    def _init_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)

        main_layout = QVBoxLayout()
        central.setLayout(main_layout)

        # 上方：選影片按鈕
        top_bar = QHBoxLayout()
        self.btn_open = QPushButton("選擇影片檔案...")
        self.btn_open.clicked.connect(self.on_open_video)
        top_bar.addWidget(self.btn_open)
        top_bar.addStretch()
        main_layout.addLayout(top_bar)

        # 中間：左側 clip 清單 + 右側影片 + 控制
        middle_layout = QHBoxLayout()
        main_layout.addLayout(middle_layout, stretch=1)

        # 左側：clip 清單
        left_panel = QVBoxLayout()
        middle_layout.addLayout(left_panel, stretch=1)

        left_panel.addWidget(QLabel("Clip 列表"))
        self.clip_list = QListWidget()
        self.clip_list.itemSelectionChanged.connect(self.on_clip_selection_changed)
        left_panel.addWidget(self.clip_list, stretch=1)

        # 右側：影片 + 時間軸 + 控制
        right_panel = QVBoxLayout()
        middle_layout.addLayout(right_panel, stretch=2)

        # 影片視窗
        self.video_widget = QVideoWidget()
        right_panel.addWidget(self.video_widget, stretch=3)

        # 時間軸區：開始點 / 結束點 / 播放 slider
        timeline_box = QGroupBox("時間控制")
        tl_layout = QVBoxLayout()
        timeline_box.setLayout(tl_layout)

        # 開始點 slider
        start_row = QHBoxLayout()
        self.lbl_start = QLabel("Start: -")
        self.start_slider = QSlider(Qt.Horizontal)
        self.start_slider.setRange(0, SLIDER_MAX)
        # 用 style 區分顏色
        self.start_slider.setStyleSheet(
            "QSlider::handle:horizontal { background: green; width: 12px; }"
        )
        self.start_slider.sliderPressed.connect(self.on_start_slider_pressed)
        self.start_slider.sliderReleased.connect(self.on_start_slider_released)
        start_row.addWidget(QLabel("開始點"))
        start_row.addWidget(self.start_slider)
        start_row.addWidget(self.lbl_start)
        tl_layout.addLayout(start_row)

        # 結束點 slider
        end_row = QHBoxLayout()
        self.lbl_end = QLabel("End: -")
        self.end_slider = QSlider(Qt.Horizontal)
        self.end_slider.setRange(0, SLIDER_MAX)
        self.end_slider.setStyleSheet(
            "QSlider::handle:horizontal { background: red; width: 12px; }"
        )
        self.end_slider.sliderPressed.connect(self.on_end_slider_pressed)
        self.end_slider.sliderReleased.connect(self.on_end_slider_released)
        end_row.addWidget(QLabel("結束點"))
        end_row.addWidget(self.end_slider)
        end_row.addWidget(self.lbl_end)
        tl_layout.addLayout(end_row)

        # 播放 slider（藍點）
        play_row = QHBoxLayout()
        self.lbl_play = QLabel("播放位置")
        self.playback_slider = QSlider(Qt.Horizontal)
        self.playback_slider.setRange(0, SLIDER_MAX)
        # 播放 slider：藍色 handle
        self.playback_slider.setStyleSheet(
            "QSlider::handle:horizontal { background: #2070ff; width: 10px; }"
        )
        # 只在「放開」時才 seek，避免 signal 迴圈
        self.playback_slider.sliderReleased.connect(self.on_playback_slider_released)
        play_row.addWidget(self.lbl_play)
        play_row.addWidget(self.playback_slider)
        tl_layout.addLayout(play_row)

        right_panel.addWidget(timeline_box)

        # 控制區：播放 + 微調 + 目前正在調整哪個點
        controls_box = QGroupBox("控制")
        controls_layout = QVBoxLayout()
        controls_box.setLayout(controls_layout)

        # 播放 / 暫停
        play_layout = QHBoxLayout()
        self.btn_play = QPushButton("播放 / 暫停")
        self.btn_play.clicked.connect(self.on_play_pause_clicked)
        play_layout.addWidget(self.btn_play)
        play_layout.addStretch()
        controls_layout.addLayout(play_layout)

        # 微調按鈕（單一組，依 active_target 調 start 或 end）
        adjust_layout = QHBoxLayout()
        self.btn_minus_5s = QPushButton("-5 秒")
        self.btn_plus_5s = QPushButton("+5 秒")
        self.btn_minus_1f = QPushButton("-1 幀")
        self.btn_plus_1f = QPushButton("+1 幀")

        self.btn_minus_5s.clicked.connect(lambda: self.on_adjust_clicked(delta_seconds=-5.0))
        self.btn_plus_5s.clicked.connect(lambda: self.on_adjust_clicked(delta_seconds=+5.0))
        self.btn_minus_1f.clicked.connect(self.on_adjust_minus_frame)
        self.btn_plus_1f.clicked.connect(self.on_adjust_plus_frame)

        adjust_layout.addWidget(self.btn_minus_5s)
        adjust_layout.addWidget(self.btn_plus_5s)
        adjust_layout.addWidget(self.btn_minus_1f)
        adjust_layout.addWidget(self.btn_plus_1f)
        controls_layout.addLayout(adjust_layout)

        self.lbl_active_target = QLabel("目前正在調整：開始點")
        controls_layout.addWidget(self.lbl_active_target)

        right_panel.addWidget(controls_box)

        # 底部：上一段 / 下一段 / 輸出
        bottom_layout = QHBoxLayout()
        main_layout.addLayout(bottom_layout)

        self.btn_prev = QPushButton("上一段")
        self.btn_next = QPushButton("下一段")
        self.btn_export = QPushButton("開始輸出所有 clips")

        self.btn_prev.clicked.connect(self.on_prev_clip)
        self.btn_next.clicked.connect(self.on_next_clip)
        self.btn_export.clicked.connect(self.on_export_clicked)

        bottom_layout.addWidget(self.btn_prev)
        bottom_layout.addWidget(self.btn_next)
        bottom_layout.addStretch()
        bottom_layout.addWidget(self.btn_export)

    # ======== enable / disable ========

    def _set_clip_controls_enabled(self, enabled: bool) -> None:
        self.clip_list.setEnabled(enabled)
        self.start_slider.setEnabled(enabled)
        self.end_slider.setEnabled(enabled)
        self.playback_slider.setEnabled(enabled)
        self.btn_play.setEnabled(enabled)
        self.btn_minus_5s.setEnabled(enabled)
        self.btn_plus_5s.setEnabled(enabled)
        self.btn_minus_1f.setEnabled(enabled)
        self.btn_plus_1f.setEnabled(enabled)
        self.btn_prev.setEnabled(enabled)
        self.btn_next.setEnabled(enabled)
        self.btn_export.setEnabled(enabled)

    # ======== 開啟影片 + 載入 clips ========

    def on_open_video(self) -> None:
        dlg = QFileDialog(self, "選擇影片檔案")
        dlg.setFileMode(QFileDialog.ExistingFile)
        dlg.setNameFilter(
            "Video Files (*.mp4 *.ts *.mkv *.mov *.avi *.flv *.m4v);;All Files (*)"
        )
        if not dlg.exec():
            return

        files = dlg.selectedFiles()
        if not files:
            return

        video_path = files[0]
        base, _ext = os.path.splitext(video_path)
        marks_path = base + ".marks"

        if not os.path.exists(marks_path):
            QMessageBox.critical(
                self,
                "找不到 .marks 檔",
                f"在同一資料夾中找不到對應的 .marks 檔：\n{marks_path}\n\n"
                f"請先使用 background_marker 產生標記檔。",
            )
            return

        # 讀取 clips
        try:
            segments = load_segments_from_marks(video_path, marks_path, align_mode="keyframe")
        except Exception as e:
            QMessageBox.critical(
                self,
                "讀取 .marks 失敗",
                f"解析 .marks 時發生錯誤：\n{e}",
            )
            return

        if not segments:
            QMessageBox.warning(self, "沒有 clips", "這個 .marks 沒有任何可用片段。")
            return

        self.video_path = os.path.abspath(video_path)
        self.marks_path = os.path.abspath(marks_path)
        self.segments = segments
        self.current_index = 0
        self.active_target = "start"
        self.lbl_active_target.setText("目前正在調整：開始點")

        # 載入影片
        self.media_player.setSource(QUrl.fromLocalFile(self.video_path))
        self.media_player.pause()

        # 建立 clip list
        self._populate_clip_list()

        # 啟用控制
        self._set_clip_controls_enabled(True)

        # 選中第一個 clip
        self.clip_list.setCurrentRow(0)
        self._update_ui_for_current_clip()

    def _populate_clip_list(self) -> None:
        self.clip_list.clear()
        for seg in self.segments:
            text = f"Clip #{seg.index}  Start={self._fmt_time(seg.start_sec)}  End={self._fmt_time(seg.end_sec)}"
            item = QListWidgetItem(text)
            self.clip_list.addItem(item)

    # ======== Clip 切換 ========

    def on_clip_selection_changed(self) -> None:
        row = self.clip_list.currentRow()
        if row < 0 or row >= len(self.segments):
            return
        self.current_index = row
        self.media_player.pause()
        self.playback_timer.stop()
        self._update_ui_for_current_clip()

    def on_prev_clip(self) -> None:
        if not self.segments:
            return
        new_index = max(0, self.current_index - 1)
        self.clip_list.setCurrentRow(new_index)

    def on_next_clip(self) -> None:
        if not self.segments:
            return
        new_index = min(len(self.segments) - 1, self.current_index + 1)
        self.clip_list.setCurrentRow(new_index)

    # ======== 依目前 clip 更新 UI ========

    def _update_ui_for_current_clip(self) -> None:
        if not self.segments or self.current_index < 0:
            return

        seg = self.segments[self.current_index]

        self.lbl_start.setText(f"Start: {self._fmt_time(seg.start_sec)}")
        self.lbl_end.setText(f"End: {self._fmt_time(seg.end_sec)}")

        # 重算顯示/調整視窗
        self._recompute_window_range()

        # 更新 start/end slider（以視窗範圍為尺）
        self._update_boundary_sliders()

        # 根據 active_target，決定畫面停在哪裡
        if self.active_target == "start":
            self._seek_to_sec(seg.start_sec)
        else:
            self._seek_to_sec(seg.end_sec)

        # 左邊清單的當前 item 也順便更新一下（防止前面調過時間）
        self._refresh_clip_list_item(self.current_index)


    def _recompute_window_range(self) -> None:
        """根據目前 clip 的 start/end 與整體影片長度，計算顯示/調整視窗範圍。"""
        if not self.segments or self.current_index < 0:
            self.window_start_sec = 0.0
            self.window_end_sec = 1.0
            return

        seg = self.segments[self.current_index]

        if self.video_duration_ms > 0:
            duration_sec = self.video_duration_ms / 1000.0
        else:
            # 若影片長度未知，就以這段結束時間當成大概長度
            duration_sec = max(seg.end_sec, seg.start_sec + 1.0)

        # 視窗先抓 start 前 30 秒、end 後 30 秒
        ws = max(0.0, seg.start_sec - 30.0)
        we = min(duration_sec, seg.end_sec + 30.0)

        # 如果 clip 本身太短或靠近片尾，確保 we > ws
        if we <= ws:
            we = min(duration_sec, seg.end_sec + 1.0)
            ws = max(0.0, we - 60.0)  # 最多顯示 60 秒，避免視窗太怪

        self.window_start_sec = ws
        self.window_end_sec = we


    def _refresh_clip_list_item(self, index: Optional[int] = None) -> None:
        """更新左側 clip 清單中某一列的文字，以反映最新的 start/end。"""
        if index is None:
            index = self.current_index

        if index < 0 or index >= len(self.segments):
            return

        seg = self.segments[index]
        item = self.clip_list.item(index)
        if item is None:
            return

        text = f"Clip #{seg.index}  Start={self._fmt_time(seg.start_sec)}  End={self._fmt_time(seg.end_sec)}"
        item.setText(text)


    def _update_boundary_sliders(self) -> None:
        """根據目前 clip 的 start/end 與 window_start/window_end 設定 start/end slider 位置。"""
        if self.window_end_sec <= self.window_start_sec:
            # 視窗不合法時，先歸零
            self.start_slider.blockSignals(True)
            self.end_slider.blockSignals(True)
            self.start_slider.setValue(0)
            self.end_slider.setValue(0)
            self.start_slider.blockSignals(False)
            self.end_slider.blockSignals(False)
            return

        seg = self.segments[self.current_index]

        window_len = self.window_end_sec - self.window_start_sec

        start_ratio = (seg.start_sec - self.window_start_sec) / window_len
        end_ratio = (seg.end_sec - self.window_start_sec) / window_len

        start_ratio = max(0.0, min(1.0, start_ratio))
        end_ratio = max(0.0, min(1.0, end_ratio))

        self.start_slider.blockSignals(True)
        self.end_slider.blockSignals(True)
        self.start_slider.setValue(int(start_ratio * SLIDER_MAX))
        self.end_slider.setValue(int(end_ratio * SLIDER_MAX))
        self.start_slider.blockSignals(False)
        self.end_slider.blockSignals(False)


    # ======== 播放 / 暫停 ========

    def on_play_pause_clicked(self) -> None:
        if not self.segments or self.current_index < 0:
            return

        if self.media_player.playbackState() == QMediaPlayer.PlayingState:
            self.media_player.pause()
            self.playback_timer.stop()
            return

        seg = self.segments[self.current_index]

        if self.active_target == "start":
            # 調 start：從 start 播到 end
            self._seek_to_sec(seg.start_sec)
            self.media_player.play()
            self.playback_timer.start()
        else:
            # 調 end：從 end（或 end 前幾秒）往後播放
            start_sec = seg.end_sec
            if END_PREVIEW_OFFSET_SECONDS > 0.0:
                start_sec = max(seg.start_sec, seg.end_sec - END_PREVIEW_OFFSET_SECONDS)
            self._seek_to_sec(start_sec)
            self.media_player.play()
            # 不限制在 end 停，依你的設定「往後播沒關係」
            self.playback_timer.stop()

    def _on_playback_timer(self) -> None:
        # 僅在「調 start」模式下，限制播放到 end 就停
        if not self.segments or self.current_index < 0:
            return
        if self.active_target != "start":
            return

        seg = self.segments[self.current_index]
        pos_ms = self.media_player.position()
        end_ms = int(seg.end_sec * 1000)

        if pos_ms >= end_ms:
            self.media_player.pause()
            self.playback_timer.stop()

    # ======== 微調按鈕 ========

    def on_adjust_clicked(self, delta_seconds: float) -> None:
        """-5秒 / +5秒 共同 handler：調整目前 active_target 指定的邊界。"""
        if not self.segments or self.current_index < 0:
            return

        seg = self.segments[self.current_index]

        if self.active_target == "start":
            new_start = seg.start_sec + delta_seconds
            # clamp：不小於 0、不大於 end、也不超出目前視窗左界太多
            new_start = max(0.0, new_start)
            new_start = max(self.window_start_sec, new_start)
            new_start = min(new_start, seg.end_sec - 0.05)
            seg.start_sec = new_start
            self.lbl_start.setText(f"Start: {self._fmt_time(seg.start_sec)}")
            self._seek_to_sec(seg.start_sec)
        else:
            new_end = seg.end_sec + delta_seconds
            if self.video_duration_ms > 0:
                max_sec = self.video_duration_ms / 1000.0
                new_end = min(new_end, max_sec)
            new_end = min(new_end, self.window_end_sec)
            new_end = max(new_end, seg.start_sec + 0.05)
            seg.end_sec = new_end
            self.lbl_end.setText(f"End: {self._fmt_time(seg.end_sec)}")
            self._seek_to_sec(seg.end_sec)

        # 調整後重算視窗 + slider + 左側清單文字
        self._recompute_window_range()
        self._update_boundary_sliders()
        self._refresh_clip_list_item(self.current_index)


    def on_adjust_minus_frame(self) -> None:
        frame_delta = 1.0 / (self.fps if self.fps > 0 else 30.0)
        self.on_adjust_clicked(delta_seconds=-frame_delta)

    def on_adjust_plus_frame(self) -> None:
        frame_delta = 1.0 / (self.fps if self.fps > 0 else 30.0)
        self.on_adjust_clicked(delta_seconds=+frame_delta)

    # ======== Start / End slider 事件 ========

    def on_start_slider_pressed(self) -> None:
        # 使用者準備調整開始點
        self.active_target = "start"
        self.lbl_active_target.setText("目前正在調整：開始點")
    
    def on_start_slider_released(self) -> None:
        if not self.segments or self.current_index < 0:
            return
        if self.window_end_sec <= self.window_start_sec:
            return

        seg = self.segments[self.current_index]
        window_len = self.window_end_sec - self.window_start_sec

        value = self.start_slider.value()
        ratio = value / SLIDER_MAX
        new_start = self.window_start_sec + ratio * window_len

        # clamp：不得小於 0，不得 >= end，且不超出視窗範圍
        new_start = max(self.window_start_sec, new_start)
        new_start = min(new_start, seg.end_sec - 0.05)

        seg.start_sec = new_start
        self.lbl_start.setText(f"Start: {self._fmt_time(seg.start_sec)}")
        self._seek_to_sec(seg.start_sec)

        # 調整 clip 視窗範圍，保持「start±30s、end±30s」的概念
        self._recompute_window_range()
        self._update_boundary_sliders()
        self._refresh_clip_list_item(self.current_index)



    def on_end_slider_pressed(self) -> None:
        # 使用者準備調整結束點
        self.active_target = "end"
        self.lbl_active_target.setText("目前正在調整：結束點")

    def on_end_slider_released(self) -> None:
        if not self.segments or self.current_index < 0:
            return
        if self.window_end_sec <= self.window_start_sec:
            return

        seg = self.segments[self.current_index]
        window_len = self.window_end_sec - self.window_start_sec

        value = self.end_slider.value()
        ratio = value / SLIDER_MAX
        new_end = self.window_start_sec + ratio * window_len

        # clamp：不得超過影片總長、不小於 start，且不超出視窗範圍
        if self.video_duration_ms > 0:
            max_sec = self.video_duration_ms / 1000.0
            new_end = min(new_end, max_sec)

        new_end = max(new_end, seg.start_sec + 0.05)
        new_end = min(new_end, self.window_end_sec)

        seg.end_sec = new_end
        self.lbl_end.setText(f"End: {self._fmt_time(seg.end_sec)}")
        self._seek_to_sec(seg.end_sec)

        self._recompute_window_range()
        self._update_boundary_sliders()
        self._refresh_clip_list_item(self.current_index)


    # ======== 播放 slider 事件（只控制播放頭） ========

    def on_playback_slider_released(self) -> None:
        if self.window_end_sec <= self.window_start_sec:
            return
        value = self.playback_slider.value()
        ratio = value / SLIDER_MAX
        sec = self.window_start_sec + ratio * (self.window_end_sec - self.window_start_sec)
        self._seek_to_sec(sec)


    # ======== 播放器事件 ========

    def _on_media_status_changed(self, status) -> None:
        # 目前不特別處理 loading/error，必要時可加
        pass

    def _on_duration_changed(self, duration_ms: int) -> None:
        self.video_duration_ms = duration_ms
        # 每次影片長度更新時，重算視窗 + 更新 slider
        if self.segments and self.current_index >= 0:
            self._recompute_window_range()
            self._update_boundary_sliders()


    def _on_position_changed(self, position_ms: int) -> None:
        # 更新播放 slider 位置（避免 signal 迴圈，需要 blockSignals）
        if self.window_end_sec <= self.window_start_sec:
            return

        current_sec = max(0.0, position_ms / 1000.0)
        window_len = self.window_end_sec - self.window_start_sec

        ratio = (current_sec - self.window_start_sec) / window_len
        ratio = max(0.0, min(1.0, ratio))

        self.playback_slider.blockSignals(True)
        self.playback_slider.setValue(int(ratio * SLIDER_MAX))
        self.playback_slider.blockSignals(False)


    # ======== 匯出 clips ========

    def on_export_clicked(self) -> None:
        if not self.segments or not self.video_path:
            return

        out_dir = os.path.dirname(os.path.abspath(self.video_path))

        ret = QMessageBox.question(
            self,
            "開始輸出",
            f"準備將 {len(self.segments)} 段 clips 輸出到：\n{out_dir}\n\n要繼續嗎？",
        )
        if ret != QMessageBox.Yes:
            return

        success, fail = run_ffmpeg_for_segments(self.video_path, self.segments, out_dir)

        QMessageBox.information(
            self,
            "輸出完成",
            f"成功：{success} 段\n失敗：{fail} 段\n輸出位置：\n{out_dir}",
        )

    # ======== 工具函式 ========

    def _fmt_time(self, sec: float) -> str:
        s = max(0.0, sec)
        total_ms = int(round(s * 1000))
        ms = total_ms % 1000
        total_sec = total_ms // 1000
        h = total_sec // 3600
        m = (total_sec % 3600) // 60
        s = total_sec % 60
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}"
        else:
            return f"{m:02d}:{s:02d}"

    def _seek_to_sec(self, sec: float) -> None:
        if sec < 0:
            sec = 0.0
        pos_ms = int(sec * 1000)
        self.media_player.setPosition(pos_ms)


def main() -> None:
    app = QApplication(sys.argv)
    window = VideoClipperWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
