#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""clip.py —— 切 9:16 竖版短片。

契约: content/kit/README.md 第 2 节 "clip.py —— 切 9:16 竖版"

用法（定位二选一）:
    python content/kit/clip.py --dir content/sessions/2026-10-20-ep01 \
        --from-label "fix-it-works" --to-label "beauty-sunset-orbit" --out content/publish/ep01/short1.mp4
    python content/kit/clip.py --input long.mp4 --start 12.5 --end 40.0 \
        --out content/publish/ep01/short2.mp4

硬性规格: 1080x1920 (9:16)、H.264 + AAC；横屏源居中裁切放大。

只依赖标准库 + ffmpeg/ffprobe。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------- 路径与常量

KIT_DIR = Path(__file__).resolve().parent
CONTENT_DIR = KIT_DIR.parent
REPO_ROOT = CONTENT_DIR.parent
DEFAULT_SESSIONS_ROOT = CONTENT_DIR / "sessions"

OUT_WIDTH = 1080
OUT_HEIGHT = 1920
TARGET_ASPECT = OUT_WIDTH / OUT_HEIGHT  # 9:16 = 0.5625

FFMPEG_ENV = "FFMPEG_BIN"
FFPROBE_ENV = "FFPROBE_BIN"

FFMPEG_FALLBACKS = (
    Path(r"E:\ffmpeg\bin\ffmpeg.exe"),
    Path(r"E:\ffmpeg\bin\ffmpeg"),
    Path("/usr/bin/ffmpeg"),
    Path("/usr/local/bin/ffmpeg"),
    Path("/opt/homebrew/bin/ffmpeg"),
)
FFPROBE_FALLBACKS = (
    Path(r"E:\ffmpeg\bin\ffprobe.exe"),
    Path(r"E:\ffmpeg\bin\ffprobe"),
    Path("/usr/bin/ffprobe"),
    Path("/usr/local/bin/ffprobe"),
    Path("/opt/homebrew/bin/ffprobe"),
)

TOOL_HINT = (
    "Install ffmpeg (with ffprobe) and add it to PATH, or:\n"
    "  · set the environment variable {env}=<path to executable>\n"
    "  · or pass the command-line option {flag} <path to executable>\n"
    "  · common location on this machine: E:\\ffmpeg\\bin\\ffmpeg.exe"
)

# ---------------------------------------------------------------- 输出辅助


def warn(msg: str) -> None:
    print("WARNING: " + str(msg), file=sys.stderr)


def info(msg: str) -> None:
    print(str(msg))


def die(msg: str, code: int = 1) -> "None":
    print("ERROR: " + str(msg), file=sys.stderr)
    sys.exit(code)


# ---------------------------------------------------------------- 工具解析


def resolve_tool(tool: str, explicit: str | None, env_var: str, fallbacks) -> str:
    exe_names = [tool, tool + ".exe"] if os.name == "nt" else [tool]
    candidates: list[Path] = []

    if explicit:
        p = Path(explicit).expanduser()
        if p.is_dir():
            candidates.extend(p / n for n in exe_names)
        else:
            candidates.append(p)

    env_value = os.environ.get(env_var)
    if env_value:
        candidates.append(Path(env_value).expanduser())

    found = shutil.which(tool)
    if found:
        candidates.append(Path(found))

    candidates.extend(fallbacks)

    for cand in candidates:
        try:
            if cand.is_file():
                return str(cand)
        except OSError:
            continue

    flag = "--ffmpeg" if tool == "ffmpeg" else "--ffprobe"
    hint = TOOL_HINT.format(env=env_var, flag=flag)
    die(f"{tool} not found.\n{hint}")


# ---------------------------------------------------------------- ffprobe


def ffprobe_info(ffprobe: str, path: Path) -> dict:
    cmd = [
        ffprobe,
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
    except OSError as exc:
        die(f"cannot run ffprobe: {exc}")
    if proc.returncode != 0:
        die(f"ffprobe cannot read {path}\n{proc.stderr.strip()}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        die(f"cannot parse ffprobe output ({path}): {exc}")


def first_stream(info: dict, kind: str) -> dict | None:
    for stream in info.get("streams", []) or []:
        if stream.get("codec_type") == kind:
            return stream
    return None


def media_duration(info: dict, path: Path) -> float:
    raw = (info.get("format") or {}).get("duration")
    if raw in (None, "N/A"):
        for stream in info.get("streams", []) or []:
            if stream.get("duration") not in (None, "N/A"):
                raw = stream["duration"]
                break
    try:
        return float(raw)
    except (TypeError, ValueError):
        die(f"cannot determine media duration (ffprobe reported no duration): {path}")


# ---------------------------------------------------------------- JSON


def load_json(path: Path, what: str) -> dict:
    if not path.is_file():
        die(f"{what} not found: {path}")
    try:
        # utf-8-sig：兼容 Windows 上被记事本/PowerShell 写出 BOM 的 UTF-8 文件。
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        die(f"cannot read {what} ({path}): {exc}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        die(f"{what} is not valid JSON ({path}): {exc}")
    if not isinstance(data, dict):
        die(f"{what} must be a JSON object at the top level: {path}")
    return data


# ---------------------------------------------------------------- 定位


def normalize_markers(doc: dict, markers_path: Path) -> list[dict]:
    raw_markers = doc.get("markers")
    if not isinstance(raw_markers, list) or not raw_markers:
        die(f"markers.json has no usable markers array: {markers_path}")
    markers: list[dict] = []
    for idx, item in enumerate(raw_markers):
        if not isinstance(item, dict):
            die(f"markers.json markers[{idx}] is not an object: {markers_path}")
        label = item.get("label")
        t = item.get("t")
        if not isinstance(label, str) or not label:
            die(f"markers.json markers[{idx}] is missing a non-empty label: {markers_path}")
        if isinstance(t, bool) or not isinstance(t, (int, float)):
            die(f"markers.json markers[{idx}].t must be a number: {markers_path}")
        markers.append({"label": label, "t": float(t)})
    markers.sort(key=lambda m: m["t"])
    return markers


def find_marker_index(markers: list[dict], label: str) -> int | None:
    """标签重复时取第一个匹配。"""
    for idx, marker in enumerate(markers):
        if marker["label"] == label:
            return idx
    return None


def available_labels(markers: list[dict]) -> str:
    lines = [f"    - {m['label']}  (t={m['t']:g}s)" for m in markers]
    return "\n".join(lines) if lines else "    (no labels in markers.json)"


def index_or_die(markers: list[dict], label: str, where: str) -> int:
    idx = find_marker_index(markers, label)
    if idx is None:
        die(
            f"{where}: label {label!r} not found.\n"
            f"  Available labels ({len(markers)} total in markers.json):\n{available_labels(markers)}"
        )
    return idx


# ---------------------------------------------------------------- 数值格式


def format_num(value: float) -> str:
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return text if text else "0"


def exit_code_text(code: int | None) -> str:
    """Windows 上子进程退出码可能是无符号形式，转回可读的带符号值。"""
    if code is None:
        return "unknown"
    if code > 0x7FFFFFFF:
        code -= 0x100000000
    return str(code)


# ---------------------------------------------------------------- 主流程


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="clip.py",
        description="Cut a 9:16 vertical short clip (output is always 1080x1920, H.264 + AAC).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python content/kit/clip.py --dir content/sessions/2026-10-20-ep01 \\\n"
            "      --from-label \"fix-it-works\" --to-label \"beauty-sunset-orbit\" --out content/publish/ep01/short1.mp4\n"
            "  python content/kit/clip.py --input long.mp4 --start 12.5 --end 40.0 \\\n"
            "      --out content/publish/ep01/short2.mp4\n"
            "\n"
            "Choose one locator:\n"
            "  · --dir + --from-label/--to-label (reads <dir>/markers.json)\n"
            "  · --input + --start/--end (seconds)\n"
            "  When --to-label is omitted, the next marker or the end of the source is used.\n"
            "  publish/ refuses to overwrite an existing output by default; pass --overwrite explicitly.\n"
        ),
    )

    locator = parser.add_mutually_exclusive_group(required=True)
    locator.add_argument("--dir", metavar="DIR", help="session directory (containing markers.json and raw.mkv)")
    locator.add_argument("--input", metavar="PATH", help="source video path (used with --start/--end)")

    parser.add_argument("--from-label", dest="from_label", metavar="LABEL",
                        help="start label (used with --dir)")
    parser.add_argument("--to-label", dest="to_label", metavar="LABEL",
                        help="end label (used with --dir); when omitted, the next marker or the end of the source is used")
    parser.add_argument("--start", type=float, metavar="SEC",
                        help="start time in seconds (used with --input; required)")
    parser.add_argument("--end", type=float, metavar="SEC",
                        help="end time in seconds (used with --input); when omitted, up to the end of the source")

    parser.add_argument("--out", required=True, metavar="PATH", help="output mp4 path")
    parser.add_argument("--overwrite", action="store_true", help="allow overwriting an existing output file")
    parser.add_argument("--sessions-root", default=None, metavar="DIR",
                        help=f"session root directory, default {DEFAULT_SESSIONS_ROOT}")
    parser.add_argument("--ffmpeg", default=None, metavar="PATH",
                        help="path to the ffmpeg executable (or use the FFMPEG_BIN environment variable)")
    parser.add_argument("--ffprobe", default=None, metavar="PATH",
                        help="path to the ffprobe executable (or use the FFPROBE_BIN environment variable)")
    parser.add_argument("--preset", default="medium", help="x264 preset, default medium")
    parser.add_argument("--crf", type=float, default=20.0, help="x264 CRF, default 20")
    parser.add_argument("--dry-run", action="store_true", help="only print the ffmpeg command that would run; do not execute it")
    return parser.parse_args(argv)


def build_vertical_filter(width: int, height: int) -> tuple[str, str]:
    """按源分辨率选择滤镜：横屏/方屏居中裁切放大，竖屏直接缩放。"""
    src_aspect = width / height if height else 0.0
    if src_aspect > TARGET_ASPECT + 1e-9:
        # 横屏源：先按高度裁出 9:16 宽度，再放大到 1080x1920（crop 默认水平居中）
        filt = f"crop=ih*9/16:ih,scale={OUT_WIDTH}:{OUT_HEIGHT},setsar=1"
        desc = f"landscape source {width}x{height} → crop=ih*9/16:ih → scale={OUT_WIDTH}x{OUT_HEIGHT} (center crop)"
    else:
        filt = f"scale={OUT_WIDTH}:{OUT_HEIGHT},setsar=1"
        desc = f"portrait/narrow source {width}x{height} → scale={OUT_WIDTH}x{OUT_HEIGHT}"
    return filt, desc


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    ffmpeg = resolve_tool("ffmpeg", args.ffmpeg, FFMPEG_ENV, FFMPEG_FALLBACKS)
    ffprobe = resolve_tool("ffprobe", args.ffprobe, FFPROBE_ENV, FFPROBE_FALLBACKS)

    if args.dir:
        if args.start is not None or args.end is not None:
            die("--dir cannot be combined with --start/--end; use --input for that")
        if not args.from_label:
            die("--dir requires --from-label (the start label)")
        if args.to_label is not None and not args.to_label:
            die("--to-label cannot be an empty string; omit it to use the next marker or the end of the source")

        sessions_root = Path(args.sessions_root).expanduser() if args.sessions_root else DEFAULT_SESSIONS_ROOT
        session_dir = Path(args.dir).expanduser()
        if not session_dir.is_absolute():
            cwd_candidate = (Path.cwd() / session_dir).resolve()
            root_candidate = (REPO_ROOT / session_dir).resolve()
            session_dir = cwd_candidate if cwd_candidate.is_dir() else root_candidate
        if not session_dir.is_dir():
            alt = sessions_root / Path(args.dir).name
            die(f"session directory not found: {session_dir}" + (f"\n  did you mean: {alt}" if alt.is_dir() else ""))

        markers_path = session_dir / "markers.json"
        if not markers_path.is_file():
            die(f"markers.json is missing; timestamps must not be guessed: {markers_path}")
        markers_doc = load_json(markers_path, "markers.json")
        markers = normalize_markers(markers_doc, markers_path)

        video_name = markers_doc.get("video")
        if not isinstance(video_name, str) or not video_name.strip():
            video_name = "raw.mkv"
        source = (session_dir / video_name).resolve()
        if not source.is_file():
            die(f"recording file not found: {source}")

        from_idx = index_or_die(markers, args.from_label, "--from-label")
        start = markers[from_idx]["t"]

        if args.to_label is not None:
            to_idx = index_or_die(markers, args.to_label, "--to-label")
            end = markers[to_idx]["t"]
            end_desc = f"label {args.to_label!r} (t={end:g}s)"
        elif from_idx + 1 < len(markers):
            nxt = markers[from_idx + 1]
            end = nxt["t"]
            end_desc = f"next marker {nxt['label']!r} (t={end:g}s)"
        else:
            end = None  # 源结尾，探测后再定
            end_desc = "end of source"

        locate_desc = (
            f"--dir {session_dir}\n"
            f"  from-label {args.from_label!r} → t={start:g}s\n"
            f"  to         {end_desc}"
        )
    else:
        if args.from_label or args.to_label:
            die("--input cannot be combined with --from-label/--to-label; use --dir or --start/--end instead")
        if args.start is None:
            die("--input requires --start (the start time in seconds)")
        source = Path(args.input).expanduser()
        if not source.is_absolute():
            cwd_candidate = (Path.cwd() / source).resolve()
            root_candidate = (REPO_ROOT / source).resolve()
            source = cwd_candidate if cwd_candidate.is_file() else root_candidate
        if not source.is_file():
            die(f"source video not found: {args.input}")
        if args.start < 0:
            die(f"--start cannot be negative: {args.start}")
        start = float(args.start)
        end = float(args.end) if args.end is not None else None
        if end is not None and end <= start:
            die(f"--end ({end:g}s) must be greater than --start ({start:g}s)")
        locate_desc = f"--input {source}\n  start={start:g}s\n  end={'end of source' if end is None else f'{end:g}s'}"

    # ---- 探测源 ----
    probe = ffprobe_info(ffprobe, source)
    duration = media_duration(probe, source)
    vstream = first_stream(probe, "video")
    if vstream is None:
        die(f"source has no video stream: {source}")
    astream = first_stream(probe, "audio")
    has_audio = astream is not None
    if not has_audio:
        warn(f"source has no audio stream; the output will have no audio track: {source}")

    width = int(vstream.get("width") or 0)
    height = int(vstream.get("height") or 0)
    if width <= 0 or height <= 0:
        die(f"cannot determine source resolution: {source}")

    if start >= duration:
        die(f"start {start:g}s is beyond the source duration of {duration:.3f}s")
    if end is None or end > duration + 0.05:
        raw_end = end
        end = duration
        if raw_end is not None:
            warn(f"end {raw_end:.3f}s is beyond the source duration of {duration:.3f}s; clamped to the end of the source")
    if end - start <= 0.001:
        die(f"invalid interval: start={start:g}s, end={end:g}s")

    clip_dur = end - start
    vf, vf_desc = build_vertical_filter(width, height)

    # ---- 输出检查（铁律：默认不覆盖） ----
    out_path = Path(args.out).expanduser()
    if not out_path.is_absolute():
        out_path = (Path.cwd() / out_path).resolve()
    sessions_resolved = (Path(args.sessions_root).expanduser() if args.sessions_root else DEFAULT_SESSIONS_ROOT).resolve()
    try:
        out_path.relative_to(sessions_resolved)
    except ValueError:
        pass
    else:
        die(f"refusing to write output into sessions/ (append-only; recordings must never be overwritten): {out_path}")
    if out_path.exists() and not args.overwrite:
        die(f"output already exists; refusing to overwrite: {out_path}\n  pass --overwrite to replace it")
    if out_path.is_dir():
        die(f"output path is a directory: {out_path}")

    cmd: list[str] = [ffmpeg, "-hide_banner", "-nostdin"]
    cmd += ["-y"] if args.overwrite else ["-n"]
    cmd += ["-loglevel", "warning"]
    cmd += ["-ss", format_num(start), "-i", str(source), "-t", format_num(clip_dur)]
    cmd += ["-vf", vf]
    cmd += [
        "-c:v", "libx264",
        "-preset", str(args.preset),
        "-crf", format_num(args.crf),
        "-pix_fmt", "yuv420p",
    ]
    if has_audio:
        cmd += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
    else:
        cmd += ["-an"]
    cmd += ["-movflags", "+faststart", str(out_path)]

    info(f"Locator    : {locate_desc}")
    info(f"Source     : {source}  ({width}x{height}, {duration:.3f}s)")
    info(f"Crop       : {vf_desc}")
    info(f"Output spec: {OUT_WIDTH}x{OUT_HEIGHT} (9:16), H.264 + AAC")
    info(f"Clip length: {clip_dur:.3f}s  ({start:.3f}s → {end:.3f}s)")
    info(f"Output     : {out_path}")

    if args.dry_run:
        info("dry-run; not executed. Command:")
        info(" ".join(_quote_for_display(c) for c in cmd))
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
    except OSError as exc:
        die(f"cannot run ffmpeg: {exc}")

    if proc.returncode != 0:
        if not args.overwrite and "already exists" in (proc.stderr or "").lower():
            die(f"output already exists; refusing to overwrite: {out_path}\n  pass --overwrite to replace it")
        if proc.stderr and proc.stderr.strip():
            print(proc.stderr.strip(), file=sys.stderr)
        die(f"ffmpeg exited with code {exit_code_text(proc.returncode)}; vertical clip failed.\n  command: {' '.join(_quote_for_display(c) for c in cmd)}")
    if proc.stderr and proc.stderr.strip():
        print(proc.stderr.strip(), file=sys.stderr)

    if not out_path.is_file():
        die(f"ffmpeg reported success but produced no output file: {out_path}")

    out_probe = ffprobe_info(ffprobe, out_path)
    out_dur = media_duration(out_probe, out_path)
    out_v = first_stream(out_probe, "video")
    out_a = first_stream(out_probe, "audio")
    out_w = int(out_v.get("width") or 0) if out_v else 0
    out_h = int(out_v.get("height") or 0) if out_v else 0
    info(f"Done: {out_path}")
    info(f"  measured resolution: {out_w}x{out_h} (expected {OUT_WIDTH}x{OUT_HEIGHT})")
    info(f"  measured duration  : {out_dur:.3f}s (expected ~{clip_dur:.3f}s)")
    info(f"  codecs             : video {out_v.get('codec_name') if out_v else '-'}, audio {out_a.get('codec_name') if out_a else 'none'}")
    if (out_w, out_h) != (OUT_WIDTH, OUT_HEIGHT):
        die(f"output resolution does not meet the 9:16 spec: {out_w}x{out_h} (expected {OUT_WIDTH}x{OUT_HEIGHT})")
    if has_audio and out_a is None:
        die("output is missing an audio track (the source has audio; the spec requires H.264 + AAC)")
    if abs(out_dur - clip_dur) > max(0.5, clip_dur * 0.05):
        warn(f"measured duration differs from the requested duration: {out_dur:.3f}s vs {clip_dur:.3f}s")
    return 0


def _quote_for_display(token: str) -> str:
    if token == "" or any(ch.isspace() for ch in token):
        return '"' + token.replace('"', '\\"') + '"'
    return token


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        sys.exit(130)