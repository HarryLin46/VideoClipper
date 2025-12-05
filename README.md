# VideoClipper

VideoClipper is a lightweight tool that lets you mark video clips while watching in PotPlayer, then review and export all clips through a simple GUI.  
It streamlines the workflow of watching → marking → exporting without interrupting playback or manually recording timestamps.

---

## Overview

VideoClipper consists of two main components:

### 1. background_marker.exe

When PotPlayer is the active window, pressing the middle mouse button records the current playback timestamp.  
The timestamps are saved into a `.marks` file located in:

```
VideoClipper/VideoMarks/
```

A `.marks` file looks like this:

```
03:13, start
04:22, end
32:13, start
35:49, end
01:02:17, start
01:06:06, end
...
```

Each pair of lines represents one clip segment.

### 2. gui_app.exe

After watching the video, you open the GUI to load the video file and its `.marks` file.  
The GUI displays all detected segments, allows optional fine adjustments using sliders and buttons, and finally exports all clips at once.

---

## Installation

VideoClipper provides pre-built Windows executables.  
Users do not need Python, dependencies, or any setup.

Simply download and extract the folder.  
Then run:

```
VideoClipper/background_marker/background_marker.exe
VideoClipper/clip_generator/gui_app.exe
```

---

## Usage Workflow

VideoClipper follows a simple two-step process:  
mark segments while watching, then load and export through the GUI.

---

### Step 1. Start the background marker

Run:

```
VideoClipper/background_marker/background_marker.exe
```

Keep it running in the background.

---

### Step 2. Watch and mark in PotPlayer

Play the video in PotPlayer.

Whenever you want to mark a segment:

- Press the middle mouse button slightly **before** the segment begins → records a `start`  
- Press the middle mouse button slightly **after** the segment ends → records an `end`

A `.marks` file will be created automatically in:

```
VideoClipper/VideoMarks/
```

Example:

```
VideoClipper/VideoMarks/MyVideo.marks
```

Contents:

```
03:13, start
04:22, end
32:13, start
35:49, end
...
```

---

### Step 3. Load and export clips in the GUI

Before opening the GUI, make sure the video file and its `.marks` file are located in the same folder.

Run:

```
VideoClipper/clip_generator/gui_app.exe
```

In the GUI:

- Click **Select Video File…** and choose the video you watched.  
- The GUI automatically loads the matching `.marks` file.  
- All segments (Clip #1, Clip #2, …) appear in the left sidebar.  
- You may fine-tune each segment using:
  - Green slider: start position  
  - Red slider: end position  
  - Blue slider: preview seek  
  - ±1s and ±0.1s buttons for precise adjustments  

If you do not wish to adjust anything, you may export directly.

#### Output Location

All exported clips are placed **in the same folder as the video and its `.marks` file**, for example:

```
~/MyVideo.mp4
~/MyVideo.marks
~/clip_001.mp4
~/clip_002.mp4
...
```

---

## Important Notes

- The background marker only records timestamps when PotPlayer is the foreground window.  
- The video file and its `.marks` file **must** be in the same folder before loading in the GUI.  
- Segment marking has **no undo function**; each middle-mouse press is permanently recorded.  
- Nested segments (a clip inside another clip) are **not supported**.

---

