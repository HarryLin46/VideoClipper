⭐ Overview

VideoClipper allows you to watch a video in PotPlayer and simply press the middle mouse button whenever you want to mark a segment.
After finishing the video, VideoClipper automatically turns these marked timestamps into clean and precisely aligned video clips.


🎬 How It Works (High-Level Workflow)

VideoClipper consists of two components:

1. Background Marker

Runs automatically when your computer starts

Listens for a global hotkey (e.g., middle mouse button)

When triggered:

Obtains current timestamp from PotPlayer

Associates it with the currently playing video

Stores the timestamp in a dedicated .marks file for that video

2. Clip Generator (Python script)

Takes a video file and its .marks file as input

Reads timestamp pairs (start/end)

Adjusts boundaries to proper aligned frames

Outputs multiple clean video segments using ffmpeg


📦 Project Structure
```text
VideoClipper/
│
├── background_marker/
│   ├── marker.py            # Background listener (global hotkey + PotPlayer API)
│   └── utils.py
│
├── clip_generator/
│   ├── generate_clips.py    # Main clip-cutting script
│   └── alignment.py         # Start/end boundary refinement (keyframes etc.)
│
├── examples/
│   └── sample.marks
│
└── README.md

🗂 The .marks File Format

Each video has a dedicated .marks file named:

<video_basename>.marks


Example:

my_video.mp4 → my_video.marks


Contents:

HH:MM:SS, start
HH:MM:SS, end
HH:MM:SS, start
HH:MM:SS, end
...

Rules:

File is assumed to contain an even number of lines

Every two consecutive lines form a single segment

start marks is assumed to occur before the desired clip

end marks is assumed to occur after the desired clip


Example:

00:12:34, start
00:15:20, end
00:32:10, start
00:35:05, end

⚙️ Clip Generation

Run:

python generate_clips.py \
    --video my_concert_2025.mp4 \
    --marks my_concert_2025.marks \
    --out-dir clips \
    --align-mode keyframe

Output files as:

```text
clips/
  clip_001.mp4
  clip_002.mp4
  ...