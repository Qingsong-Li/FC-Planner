#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

import bpy


def parse_args():
    if "--" in sys.argv:
        raw_args = sys.argv[sys.argv.index("--") + 1 :]
    else:
        raw_args = sys.argv[1 :]

    parser = argparse.ArgumentParser(description="Encode image sequence to MP4 with Blender.")
    parser.add_argument("--frames_dir", required=True, help="Directory containing 0000.png, 0001.png ...")
    parser.add_argument("--output", required=True, help="Output mp4 path")
    parser.add_argument("--fps", type=int, default=20, help="Frame rate")
    return parser.parse_args(raw_args)


def main():
    args = parse_args()
    frames_dir = Path(args.frames_dir).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    fps = int(args.fps)

    if not frames_dir.exists():
        raise FileNotFoundError(f"frames_dir not found: {frames_dir}")

    images = sorted(frames_dir.glob("*.png"))
    if not images:
        raise RuntimeError(f"No PNG frames found in {frames_dir}")

    scene = bpy.context.scene
    scene.sequence_editor_create()
    seq = scene.sequence_editor

    for s in list(seq.sequences_all):
        seq.sequences.remove(s)

    first = bpy.data.images.load(str(images[0]))
    width, height = first.size[0], first.size[1]
    bpy.data.images.remove(first)

    strip = seq.sequences.new_image(
        name="FOVFrames",
        filepath=str(images[0]),
        channel=1,
        frame_start=1,
    )
    # Blender 3.6 的 `elements` 不支持 clear()，保留第 1 帧并追加后续帧。
    if len(strip.elements) > 0:
        strip.elements[0].filename = images[0].name
    for img in images[1:]:
        strip.elements.append(img.name)

    scene.frame_start = 1
    scene.frame_end = len(images)
    scene.render.fps = fps
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "FFMPEG"
    scene.render.ffmpeg.format = "MPEG4"
    scene.render.ffmpeg.codec = "H264"
    scene.render.ffmpeg.constant_rate_factor = "MEDIUM"
    scene.render.ffmpeg.ffmpeg_preset = "GOOD"
    scene.render.ffmpeg.audio_codec = "NONE"
    scene.render.use_sequencer = True
    scene.render.filepath = str(output)

    output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.render.render(animation=True)
    print(f"[DONE] Video saved: {output}")


if __name__ == "__main__":
    main()
