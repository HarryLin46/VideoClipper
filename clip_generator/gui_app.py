# clip_generator/gui_app.py
#
# 執行方式（專案根目錄）：
#     python -m clip_generator.gui_app
#
# 需求：
#     pip install PySide6
#
# 功能摘要：
#   - 選影片檔（假設同一層有對應 .marks）
#   - 用 core.load_segments_from_marks() 取得每段 clip 的 start/end
#   - 左側：clip 列表（會即時反映調整後的 start/end）
#   - 右側：
#       * 影片預覽
#       * 三條滑桿：
#           - 綠色：開始點（start）
#           - 紅色：結束點（end）
#           - 藍色：播放位置（playback，僅播放時可拖曳，範圍被限制在 start~end）
#       * 一組微調按鈕：
#           - -1 秒 / +1 秒
#           - -0.1 秒 / +0.1 秒
#       * 一顆可點擊文字按鈕，切換「目前正在調整：開始點 / 結束點」
#
#   - 模式與行為：
#       * active_target = "start":
#           - 只能操作綠色滑桿與微調按鈕（影響 start）
#           - 紅色滑桿鎖住
#           - 播放：從 start 播到 end
#       * active_target = "end":
#           - 只能操作紅色滑桿與微調按鈕（影響 end）
#           - 綠色滑桿鎖住
#           - 播放：從 max(start, end - END_PREVIEW_OFFSET_SECONDS) 播到 end
#       * 藍色滑桿：
#           - 只在播放中啟用，可拖曳來 seek
#           - 對應時間被限制在目前 clip 的 start~end 間
#
#   - 滑桿比例重點：
#       * 對每個 clip，window_start/window_end 只在「選 clip / 影片長度變更」時計算一次。
#       * 之後不會因為你拖拉或微調再重算，所以同一個時間點對應的 slider 位置始終一致。
#
#   - 視窗範圍（每段 clip 一次性決定）：
#       window_start = max(0, start - 30)
#       window_end   = min(video_duration, end + 30)
#
#   - 播放中若拖拉或微調 start/end：
#       * 會以最新的 start/end 重新決定播放起訖，並自動繼續播放。
#       * 若原本是暫停狀態，則只更新畫面、不自動播放。

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

# --- 可自行修改的常數 ---

SLIDER_MAX = 1000  # 滑桿內部比例尺 0 ~ SLIDER_MAX

COARSE_DELTA_SECONDS = 1.0   # 大步微調：1 秒
FINE_DELTA_SECONDS = 0.1     # 小步微調：0.1 秒

END_PREVIEW_OFFSET_SECONDS = 3.0  # 調整結束點時，播放從 end 前幾秒開始


class VideoClipperWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("VideoClipper - GUI")
        self.resize(1200, 750)

        # 狀態變數
        self.video_path: Optional[str] = None
        self.marks_path: Optional[str] = None
        self.segments: List[ClipSegment] = []
        self.current_index: int = -1  # 目前選中的 clip index (0-based)
        self.active_target: str = "start"  # "start" 或 "end"

        self.video_duration_ms: int = 0  # 影片總長度（毫秒）
        # 目前這個 clip 的顯示/調整視窗（秒）
        self.window_start_sec: float = 0.0
        self.window_end_sec: float = 0.0

        # 播放區間（秒），僅在播放時使用
        self.playback_end_sec: Optional[float] = None

        # Qt 播放相關
        self.audio_output = QAudioOutput()
        self.audio_output.setVolume(1.0)   # 確保有聲音
        self.audio_output.setMuted(False)  # 確保不是靜音

        self.media_player = QMediaPlayer()
        self.media_player.setAudioOutput(self.audio_output)

        # 播放區間控制：檢查到達 end 時自動停
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
        self.playback_slider.setEnabled(False)

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

        # 時間軸區：開始點 / 結束點滑桿
        timeline_box = QGroupBox("時間調整（綠：開始點，紅：結束點，藍：播放位置）")
        tl_layout = QVBoxLayout()
        timeline_box.setLayout(tl_layout)

        # 統一左側標題 Label 寬度，確保三條滑桿起點對齊
        self.lbl_start_title = QLabel("開始點")
        self.lbl_end_title = QLabel("結束點")
        self.lbl_playback_title = QLabel("播放位置")
        for lbl in (self.lbl_start_title, self.lbl_end_title, self.lbl_playback_title):
            lbl.setFixedWidth(60)  # 需要可以自己調整
            lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        # 統一右側時間 Label 寬度，只顯示時間字串
        self.lbl_start = QLabel("--:--")
        self.lbl_end = QLabel("--:--")
        self.lbl_playback = QLabel("--:--")
        for lbl in (self.lbl_start, self.lbl_end, self.lbl_playback):
            lbl.setFixedWidth(70)  # 尾巴欄位固定寬度，三條滑桿尾端對齊
            lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        # 開始點 slider（綠）
        start_row = QHBoxLayout()
        self.start_slider = QSlider(Qt.Horizontal)
        self.start_slider.setRange(0, SLIDER_MAX)
        self.start_slider.setStyleSheet(
            "QSlider::handle:horizontal { background: green; width: 12px; }"
        )
        self.start_slider.valueChanged.connect(self.on_start_slider_changed)
        start_row.addWidget(self.lbl_start_title)
        start_row.addWidget(self.start_slider, 1)
        start_row.addWidget(self.lbl_start)
        tl_layout.addLayout(start_row)

        # 結束點 slider（紅）
        end_row = QHBoxLayout()
        self.end_slider = QSlider(Qt.Horizontal)
        self.end_slider.setRange(0, SLIDER_MAX)
        self.end_slider.setStyleSheet(
            "QSlider::handle:horizontal { background: red; width: 12px; }"
        )
        self.end_slider.valueChanged.connect(self.on_end_slider_changed)
        end_row.addWidget(self.lbl_end_title)
        end_row.addWidget(self.end_slider, 1)
        end_row.addWidget(self.lbl_end)
        tl_layout.addLayout(end_row)

        # 播放位置 slider（藍）
        playback_row = QHBoxLayout()
        self.playback_slider = QSlider(Qt.Horizontal)
        self.playback_slider.setRange(0, SLIDER_MAX)
        self.playback_slider.setStyleSheet(
            "QSlider::handle:horizontal { background: blue; width: 10px; }"
        )
        self.playback_slider.valueChanged.connect(self.on_playback_slider_changed)
        playback_row.addWidget(self.lbl_playback_title)
        playback_row.addWidget(self.playback_slider, 1)
        playback_row.addWidget(self.lbl_playback)
        tl_layout.addLayout(playback_row)


        right_panel.addWidget(timeline_box)

        # 控制區：播放 + 微調 + 模式切換
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
        self.btn_minus_coarse = QPushButton("-1 秒")
        self.btn_plus_coarse = QPushButton("+1 秒")
        self.btn_minus_fine = QPushButton(f"-{FINE_DELTA_SECONDS:.1f} 秒")
        self.btn_plus_fine = QPushButton(f"+{FINE_DELTA_SECONDS:.1f} 秒")

        self.btn_minus_coarse.clicked.connect(
            lambda: self.on_adjust_clicked(-COARSE_DELTA_SECONDS)
        )
        self.btn_plus_coarse.clicked.connect(
            lambda: self.on_adjust_clicked(+COARSE_DELTA_SECONDS)
        )
        self.btn_minus_fine.clicked.connect(
            lambda: self.on_adjust_clicked(-FINE_DELTA_SECONDS)
        )
        self.btn_plus_fine.clicked.connect(
            lambda: self.on_adjust_clicked(+FINE_DELTA_SECONDS)
        )

        adjust_layout.addWidget(self.btn_minus_coarse)
        adjust_layout.addWidget(self.btn_plus_coarse)
        adjust_layout.addWidget(self.btn_minus_fine)
        adjust_layout.addWidget(self.btn_plus_fine)
        controls_layout.addLayout(adjust_layout)

        # 模式切換按鈕（看起來像 label）
        self.btn_active_target = QPushButton("目前正在調整：開始點")
        self.btn_active_target.setFlat(True)
        self.btn_active_target.clicked.connect(self.on_toggle_active_target)
        controls_layout.addWidget(self.btn_active_target)

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
        # playback_slider 由播放狀態控制啟用/鎖定
        self.btn_play.setEnabled(enabled)
        self.btn_minus_coarse.setEnabled(enabled)
        self.btn_plus_coarse.setEnabled(enabled)
        self.btn_minus_fine.setEnabled(enabled)
        self.btn_plus_fine.setEnabled(enabled)
        self.btn_prev.setEnabled(enabled)
        self.btn_next.setEnabled(enabled)
        self.btn_export.setEnabled(enabled)
        self.btn_active_target.setEnabled(enabled)

    # ======== 播放開始/停止 helper ========

    def _stop_playback(self) -> None:
        self.media_player.pause()
        self.playback_timer.stop()
        self.playback_end_sec = None
        self.playback_slider.setEnabled(False)

    def _start_segment_playback(self) -> None:
        """依目前 active_target 與最新 start/end 設定播放區間並開始播放。"""
        if not self.segments or self.current_index < 0:
            return
        seg = self.segments[self.current_index]

        if self.active_target == "start":
            play_start = seg.start_sec
            play_end = seg.end_sec
        else:
            play_end = seg.end_sec
            play_start = max(seg.start_sec, seg.end_sec - END_PREVIEW_OFFSET_SECONDS)

        self.playback_end_sec = play_end
        self._seek_to_sec(play_start)
        self.media_player.play()
        self.playback_timer.start()
        self.playback_slider.setEnabled(True)
        # 播放起點時更新藍色滑桿
        self._update_playback_slider_from_time(play_start)

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
        self._update_active_target_ui()

        # 載入影片
        self.media_player.setSource(QUrl.fromLocalFile(self.video_path))
        self.media_player.pause()

        # 建立 clip list
        self._populate_clip_list()

        # 啟用控制
        self._set_clip_controls_enabled(True)
        self.playback_slider.setEnabled(False)

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
        self._stop_playback()
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

    # ======== 模式切換 ========

    def on_toggle_active_target(self) -> None:
        if self.active_target == "start":
            self.active_target = "end"
        else:
            self.active_target = "start"
        self._update_active_target_ui()
        # 切換模式後，畫面跳到對應邊界
        if not self.segments or self.current_index < 0:
            return
        seg = self.segments[self.current_index]
        if self.active_target == "start":
            self._seek_to_sec(seg.start_sec)
        else:
            self._seek_to_sec(seg.end_sec)

    def _update_active_target_ui(self) -> None:
        if self.active_target == "start":
            self.btn_active_target.setText("目前正在調整：開始點")
            self.start_slider.setEnabled(True)
            self.end_slider.setEnabled(False)
        else:
            self.btn_active_target.setText("目前正在調整：結束點")
            self.start_slider.setEnabled(False)
            self.end_slider.setEnabled(True)

    # ======== 依目前 clip 更新 UI ========

    def _update_ui_for_current_clip(self) -> None:
        if not self.segments or self.current_index < 0:
            return

        seg = self.segments[self.current_index]

        self.lbl_start.setText(f"Start: {self._fmt_time(seg.start_sec)}")
        self.lbl_end.setText(f"End: {self._fmt_time(seg.end_sec)}")

        # 只在這裡（選 clip / 影片長度更新時）依當前 start/end 決定視窗範圍
        self._recompute_window_range()

        # 更新 start/end slider 位置（程式內部 setValue 必須 blockSignals）
        self._update_boundary_sliders()

        # 播放位置顯示為 start（暫停狀態）
        self._update_playback_slider_from_time(seg.start_sec)

        # 畫面跳到目前 active_target 的位置
        if self.active_target == "start":
            self._seek_to_sec(seg.start_sec)
        else:
            self._seek_to_sec(seg.end_sec)

        # 更新左邊清單上這條的文字（防止之前有改過沒反映）
        self._refresh_clip_list_item(self.current_index)

    def _refresh_clip_list_item(self, index: Optional[int] = None) -> None:
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

    # ======== 視窗範圍與滑桿映射 ========

    def _recompute_window_range(self) -> None:
        """根據目前 clip 的 start/end 與影片長度，計算顯示/調整視窗範圍。
           注意：每段 clip 僅在選取時算一次，之後不因拖拉而改變。"""
        if not self.segments or self.current_index < 0:
            self.window_start_sec = 0.0
            self.window_end_sec = 1.0
            return

        seg = self.segments[self.current_index]

        if self.video_duration_ms > 0:
            duration_sec = self.video_duration_ms / 1000.0
        else:
            duration_sec = max(seg.end_sec, seg.start_sec + 1.0)

        ws = max(0.0, seg.start_sec - 30.0)
        we = min(duration_sec, seg.end_sec + 30.0)

        if we <= ws:
            we = min(duration_sec, seg.end_sec + 1.0)
            ws = max(0.0, we - 60.0)

        self.window_start_sec = ws
        self.window_end_sec = we

    def _update_boundary_sliders(self) -> None:
        """根據 window_start/window_end 與目前 start/end 設定滑桿。"""
        if self.window_end_sec <= self.window_start_sec:
            self.start_slider.blockSignals(True)
            self.end_slider.blockSignals(True)
            self.start_slider.setValue(0)
            self.end_slider.setValue(SLIDER_MAX)
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

    def _slider_value_to_sec(self, value: int) -> float:
        """將 start/end 滑桿值轉換成視窗範圍中的時間（秒）。"""
        if self.window_end_sec <= self.window_start_sec:
            return self.window_start_sec
        ratio = max(0.0, min(1.0, value / SLIDER_MAX))
        return self.window_start_sec + ratio * (self.window_end_sec - self.window_start_sec)

    # 播放滑桿（藍）使用 start~end 區間，而非 window 範圍
    def _playback_slider_value_to_sec(self, value: int) -> float:
        """將播放滑桿值轉為視窗範圍(window_start~window_end)中的時間."""
        if self.window_end_sec <= self.window_start_sec:
            return self.window_start_sec
        ratio = max(0.0, min(1.0, value / SLIDER_MAX))
        return self.window_start_sec + ratio * (self.window_end_sec - self.window_start_sec)


    def _update_playback_slider_from_time(self, sec: float) -> None:
        """依據時間（秒）更新播放滑桿位置與 label。
        滑桿位置用 window_start~window_end 映射，確保與綠/紅一致。"""
        if self.window_end_sec <= self.window_start_sec:
            return

        # 時間在視窗內 clamp，避免超出 window 導致比例錯亂
        clamped = max(self.window_start_sec, min(sec, self.window_end_sec))
        ratio = (clamped - self.window_start_sec) / (self.window_end_sec - self.window_start_sec)
        value = int(ratio * SLIDER_MAX)

        self.playback_slider.blockSignals(True)
        self.playback_slider.setValue(value)
        self.playback_slider.blockSignals(False)

        # label 顯示實際時間（sec），通常也在 start~end 之間
        self.lbl_playback.setText(f"Playback: {self._fmt_time(sec)}")


    # ======== 滑桿事件（start/end：點擊或拖曳） ========

    def on_start_slider_changed(self, value: int) -> None:
        """調整開始點：僅當 active_target 是 start 且有有效 clip 時作用。"""
        if self.active_target != "start":
            return
        if not self.segments or self.current_index < 0:
            return

        seg = self.segments[self.current_index]
        was_playing = (
            self.media_player.playbackState() == QMediaPlayer.PlayingState
        )

        new_start = self._slider_value_to_sec(value)

        # clamp：不小於 0，不大於 end - 0.05
        new_start = max(0.0, new_start)
        new_start = min(new_start, seg.end_sec - 0.05)

        seg.start_sec = new_start
        self.lbl_start.setText(f"Start: {self._fmt_time(seg.start_sec)}")
        self._seek_to_sec(seg.start_sec)
        self._refresh_clip_list_item(self.current_index)

        # 播放滑桿更新：播放位置仍維持目前播放時間（若正在播）或 start（若暫停）
        # 這裡簡化成：若暫停就顯示 start；若播放，後面 positionChanged 會覆蓋更新。
        if not was_playing:
            self._update_playback_slider_from_time(seg.start_sec)

        if was_playing:
            self._start_segment_playback()
        else:
            self._stop_playback()

    def on_end_slider_changed(self, value: int) -> None:
        """調整結束點：僅當 active_target 是 end 且有有效 clip 時作用。"""
        if self.active_target != "end":
            return
        if not self.segments or self.current_index < 0:
            return

        seg = self.segments[self.current_index]
        was_playing = (
            self.media_player.playbackState() == QMediaPlayer.PlayingState
        )

        new_end = self._slider_value_to_sec(value)

        # clamp：不小於 start + 0.05，不大於影片總長 / 視窗右界
        if self.video_duration_ms > 0:
            max_sec = self.video_duration_ms / 1000.0
            new_end = min(new_end, max_sec)
        new_end = max(new_end, seg.start_sec + 0.05)
        new_end = min(new_end, self.window_end_sec)

        seg.end_sec = new_end
        self.lbl_end.setText(f"End: {self._fmt_time(seg.end_sec)}")
        self._seek_to_sec(seg.end_sec)
        self._refresh_clip_list_item(self.current_index)

        if not was_playing:
            # 暫停狀態下，播放滑桿顯示 start
            self._update_playback_slider_from_time(seg.start_sec)

        if was_playing:
            self._start_segment_playback()
        else:
            self._stop_playback()

    # ======== 播放滑桿事件（藍） ========

    def on_playback_slider_changed(self, value: int) -> None:
        """播放位置滑桿：僅在播放中時可拖曳，時間映射用 window，
        但實際生效範圍限制在目前 clip 的 start~end 之間。"""
        if self.media_player.playbackState() != QMediaPlayer.PlayingState:
            # 非播放狀態時，忽略使用者互動（理論上也被 setEnabled(False) 鎖住）
            return
        if not self.segments or self.current_index < 0:
            return

        seg = self.segments[self.current_index]
        if seg.end_sec <= seg.start_sec:
            return

        # 先用 window 映射取得時間
        sec = self._playback_slider_value_to_sec(value)

        # 若超出 start~end 範圍，視為無效操作：還原到目前播放位置
        if sec < seg.start_sec or sec > seg.end_sec:
            current_sec = self.media_player.position() / 1000.0
            self._update_playback_slider_from_time(current_sec)
            return

        # 在合法範圍內才真正 seek
        self._seek_to_sec(sec)
        self.lbl_playback.setText(f"Playback: {self._fmt_time(sec)}")


    # ======== 播放 / 暫停 ========

    def on_play_pause_clicked(self) -> None:
        if not self.segments or self.current_index < 0:
            return

        # 如果正在播放，按一下就是暫停
        if self.media_player.playbackState() == QMediaPlayer.PlayingState:
            self._stop_playback()
            return

        # 否則以目前 active_target 與最新 start/end 開始播放
        self._start_segment_playback()

    def _on_playback_timer(self) -> None:
        """檢查是否到達 playback_end_sec，若到達則停止播放。"""
        if self.playback_end_sec is None:
            return
        pos_ms = self.media_player.position()
        current_sec = pos_ms / 1000.0
        if current_sec >= self.playback_end_sec:
            self._stop_playback()

    # ======== 微調按鈕 ========

    def on_adjust_clicked(self, delta_seconds: float) -> None:
        """-1秒 / +1秒 / -0.1秒 / +0.1秒 共同 handler。"""
        if not self.segments or self.current_index < 0:
            return

        seg = self.segments[self.current_index]
        was_playing = (
            self.media_player.playbackState() == QMediaPlayer.PlayingState
        )

        if self.active_target == "start":
            # 以目前綠色滑桿位置為基準
            current = self._slider_value_to_sec(self.start_slider.value())
            new_start = current + delta_seconds

            new_start = max(0.0, new_start)
            new_start = min(new_start, seg.end_sec - 0.05)

            seg.start_sec = new_start
            self.lbl_start.setText(f"Start: {self._fmt_time(seg.start_sec)}")
            self._seek_to_sec(seg.start_sec)

        else:
            # active_target == "end"
            current = self._slider_value_to_sec(self.end_slider.value())
            new_end = current + delta_seconds

            if self.video_duration_ms > 0:
                max_sec = self.video_duration_ms / 1000.0
                new_end = min(new_end, max_sec)
            new_end = max(new_end, seg.start_sec + 0.05)
            new_end = min(new_end, self.window_end_sec)

            seg.end_sec = new_end
            self.lbl_end.setText(f"End: {self._fmt_time(seg.end_sec)}")
            self._seek_to_sec(seg.end_sec)

        # 微調後：更新綠/紅滑桿位置（視窗不再重算，因此比例保持一致）
        self._update_boundary_sliders()
        self._refresh_clip_list_item(self.current_index)

        if not was_playing:
            # 暫停狀態下，播放位置顯示 start
            self._update_playback_slider_from_time(seg.start_sec)
            self._stop_playback()
        else:
            # 播放狀態下，依最新 start/end 重新播放
            self._start_segment_playback()

    # ======== 播放器事件 ========

    def _on_media_status_changed(self, status) -> None:
        # 目前不特別處理 loading/error，必要時可再補
        pass

    def _on_duration_changed(self, duration_ms: int) -> None:
        self.video_duration_ms = duration_ms
        # 影片長度改變時，若已選 clip，就重新計算一次視窗與滑桿位置
        if self.segments and self.current_index >= 0:
            self._recompute_window_range()
            self._update_boundary_sliders()

    def _on_position_changed(self, position_ms: int) -> None:
        """影片播放位置變化時，更新藍色滑桿與顯示。"""
        if not self.segments or self.current_index < 0:
            return
        seg = self.segments[self.current_index]
        sec = position_ms / 1000.0
        # 播放位置顯示是真實播放時間，但藍滑桿位置被 clamp 在 start~end
        self._update_playback_slider_from_time(sec)

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
