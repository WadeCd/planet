#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""roughcut.py —— 按剪辑单粗剪。

契约: content/kit/README.md 第 2 节 "roughcut.py —— 按剪辑单粗剪"

用法:
    python content/kit/roughcut.py --edit content/edits/ep01.json --out content/publish/ep01/long.mp4

剪辑单结构:
    {
      "episode": "ep01",
      "session": "2026-10-20-ep01",
      "subtitles": "content/publish/ep01/voice.srt",
      "loudness_lufs": -14.0,
      "segments": [
        {"name": "钩子", "from_marker": "beauty-sunset-orbit", "to_marker": null, "speed": 1.0},
        {"name": "干活", "from_marker": "dev-gravity-start", "to_marker": "fail-first-run", "speed": 6.0}
      ]
    }

只依赖标准库 + ffmpeg/ffprobe。
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------- 路径与常量

KIT_DIR = Path(__file__).resolve().parent
CONTENT_DIR = KIT_DIR.parent
REPO_ROOT = CONTENT_DIR.parent
DEFAULT_SESSIONS_ROOT = CONTENT_DIR / "sessions"

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
    """按 显式参数 → 环境变量 → PATH → 常见位置 的顺序解析 ffmpeg/ffprobe。"""
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
        die(f"cannot determine recording duration (ffprobe reported no duration): {path}")


def frame_rate_arg(stream: dict | None, default: str = "30") -> str:
    if not stream:
        return default
    for key in ("avg_frame_rate", "r_frame_rate"):
        rate = stream.get(key)
        if not rate or rate in ("0/0", "N/A"):
            continue
        num, _, den = rate.partition("/")
        try:
            if float(num) > 0 and float(den) > 0:
                return rate
        except ValueError:
            continue
    return default


# ---------------------------------------------------------------- JSON 读写


def load_json(path: Path, what: str) -> dict:
    if not path.is_file():
        die(f"{what} not found: {path}")
    try:
        # utf-8-sig：剪辑单由人（Shanks）在 Windows 上产出，记事本/PowerShell 默认写 BOM。
        # 用 utf-8-sig 可同时读带 BOM 与不带 BOM 的 UTF-8，避免 "Unexpected UTF-8 BOM" 硬失败。
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


def resolve_input_path(raw: str, what: str) -> Path:
    """相对路径先按当前工作目录解析，再按工程根解析。"""
    p = Path(raw).expanduser()
    if p.is_absolute():
        if p.is_file():
            return p
        die(f"{what} not found: {p}")
    cwd_candidate = (Path.cwd() / p).resolve()
    root_candidate = (REPO_ROOT / p).resolve()
    for cand in (cwd_candidate, root_candidate):
        if cand.is_file():
            return cand
    die(f"{what} not found: {raw}\n  tried: {cwd_candidate}\n         {root_candidate}")


# ---------------------------------------------------------------- 定位辅助


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
    markers.sort(key=lambda m: m["t"])  # 契约保证按 t 升序；这里兜底排序
    return markers


def find_marker(markers: list[dict], label: str) -> dict | None:
    """标签重复时取第一个匹配（markers 已按 t 升序）。"""
    for marker in markers:
        if marker["label"] == label:
            return marker
    return None


def available_labels(markers: list[dict]) -> str:
    lines = []
    for marker in markers:
        lines.append(f"    - {marker['label']}  (t={marker['t']:g}s)")
    return "\n".join(lines) if lines else "    (no labels in markers.json)"


def label_or_die(markers: list[dict], label: str, where: str) -> dict:
    marker = find_marker(markers, label)
    if marker is None:
        die(
            f"{where}: label {label!r} not found.\n"
            f"  Available labels ({len(markers)} total in markers.json):\n{available_labels(markers)}"
        )
    return marker


# ---------------------------------------------------------------- 变速


def atempo_chain(speed: float) -> str:
    """把任意倍速拆成 ffmpeg atempo 支持的 0.5~2.0 串联链。"""
    parts: list[str] = []
    remaining = float(speed)
    while remaining > 2.0 + 1e-9:
        parts.append(2.0)
        remaining /= 2.0
    while remaining < 0.5 - 1e-9:
        parts.append(0.5)
        remaining /= 0.5
    if abs(remaining - 1.0) > 1e-6 or not parts:
        parts.append(remaining)
    return ",".join(f"atempo={p:.6f}".rstrip("0").rstrip(".") for p in parts)


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


# ---------------------------------------------------------------- 字幕路径转义

# ffmpeg 有两层解析：filtergraph 一层 + 滤镜自身选项一层。
# Windows 绝对路径 E:\...\voice.srt 里的盘符冒号会被两层各吃一次，所以：
#   1) 反斜杠统一成正斜杠（Windows 路径分隔符）
#   2) 整个值用单引号包起来 —— filtergraph 层会把引号内的内容原样保留（含反斜杠）
#   3) 引号内把 ":" "=" "," "[" "]" ";" 各加一个反斜杠，交给第二层选项解析去反转义
# 只加一层反斜杠（不加引号）会被 filtergraph 层提前吃掉，报
# "No option name near '/Godot-Project/...'"。已实测验证。
_FILTER_SPECIAL = "\\':,[];"


def escape_filter_value(value: str) -> str:
    """把路径转成可以安全塞进 ffmpeg filtergraph 选项值的字符串。"""
    text = str(value).replace("\\", "/")
    out = ["'"]
    for ch in text:
        if ch in _FILTER_SPECIAL:
            out.append("\\" + ch)
        else:
            out.append(ch)
    out.append("'")
    return "".join(out)


def subtitle_filter(subtitle_path: Path) -> tuple[str, Path | None]:
    """返回 (subtitles 滤镜参数, 需要事后删除的临时文件或 None)。

    单引号无法在 filtergraph 单引号内表达，遇到这种路径就把字幕复制到
    一个安全临时文件名再烧录，避免静默产出错误结果。
    """
    text = str(subtitle_path)
    if "'" in text or "\n" in text or "\r" in text:
        tmp_dir = Path(tempfile.mkdtemp(prefix="roughcut-subs-"))
        tmp_file = tmp_dir / ("voice" + (subtitle_path.suffix or ".srt"))
        try:
            shutil.copyfile(subtitle_path, tmp_file)
        except OSError as exc:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            die(f"subtitle path contains a single quote and copying a temporary copy failed ({subtitle_path}): {exc}")
        warn(f"subtitle path contains a single quote; copied a temporary copy for burn-in: {tmp_file}")
        return f"subtitles=filename={escape_filter_value(str(tmp_file))}", tmp_file
    return f"subtitles=filename={escape_filter_value(text)}", None


def _cleanup_temp_file(path: Path) -> None:
    """删除字幕临时副本所在的临时目录（atexit 兜底，覆盖所有退出路径）。"""
    try:
        shutil.rmtree(path.parent, ignore_errors=True)
    except OSError:
        pass


# ---------------------------------------------------------------- 主流程


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="roughcut.py",
        description="Rough-cut session recordings according to an edit list (content/edits/epNN.json) into one stitched output.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  python content/kit/roughcut.py --edit content/edits/ep01.json --out content/publish/ep01/long.mp4\n"
            "\n"
            "Conventions:\n"
            "  · session is resolved relative to content/sessions/; the recording filename comes from the video field in markers.json (always raw.mkv)\n"
            "  · from_marker/to_marker locate segments by label; to_marker=null means up to the end of the recording\n"
            "  · a repeated label matches the first occurrence; a missing label errors out and lists the available labels\n"
            "  · a missing markers.json errors out immediately; timestamps are never guessed\n"
            "  · publish/ refuses to overwrite an existing output by default; pass --overwrite explicitly\n"
        ),
    )
    parser.add_argument("--edit", required=True, metavar="PATH", help="path to the edit-list JSON (content/edits/epNN.json)")
    parser.add_argument("--out", required=True, metavar="PATH", help="output video path (content/publish/epNN/long.mp4)")
    parser.add_argument("--overwrite", action="store_true", help="allow overwriting an existing output file")
    parser.add_argument("--sessions-root", default=None, metavar="DIR",
                        help=f"session root directory, default {DEFAULT_SESSIONS_ROOT}")
    parser.add_argument("--ffmpeg", default=None, metavar="PATH", help="path to the ffmpeg executable (or use the FFMPEG_BIN environment variable)")
    parser.add_argument("--ffprobe", default=None, metavar="PATH", help="path to the ffprobe executable (or use the FFPROBE_BIN environment variable)")
    parser.add_argument("--preset", default="medium", help="x264 preset, default medium")
    parser.add_argument("--crf", type=float, default=20.0, help="x264 CRF, default 20")
    parser.add_argument("--dry-run", action="store_true", help="only print the ffmpeg command that would run; do not execute it")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    ffmpeg = resolve_tool("ffmpeg", args.ffmpeg, FFMPEG_ENV, FFMPEG_FALLBACKS)
    ffprobe = resolve_tool("ffprobe", args.ffprobe, FFPROBE_ENV, FFPROBE_FALLBACKS)

    edit_path = resolve_input_path(args.edit, "edit list")
    edit = load_json(edit_path, "edit list")

    session = edit.get("session")
    if not isinstance(session, str) or not session.strip():
        die(f"edit list is missing a non-empty session field: {edit_path}")
    session = session.strip()
    if Path(session).name != session:
        die(f"session must be a session directory name (e.g. 2026-10-20-ep01), not a path: {session!r}")

    segments_raw = edit.get("segments")
    if not isinstance(segments_raw, list) or not segments_raw:
        die(f"edit list is missing a non-empty segments array: {edit_path}")

    sessions_root = Path(args.sessions_root).expanduser() if args.sessions_root else DEFAULT_SESSIONS_ROOT
    session_dir = (sessions_root / session).resolve()
    if not session_dir.is_dir():
        die(f"session directory not found: {session_dir}\n  (session is resolved relative to {sessions_root})")

    markers_path = session_dir / "markers.json"
    if not markers_path.is_file():
        die(f"markers.json is missing; timestamps must not be guessed: {markers_path}")

    markers_doc = load_json(markers_path, "markers.json")
    markers = normalize_markers(markers_doc, markers_path)

    video_name = markers_doc.get("video")
    if not isinstance(video_name, str) or not video_name.strip():
        video_name = "raw.mkv"
    video_path = (session_dir / video_name).resolve()
    if not video_path.is_file():
        die(f"recording file not found: {video_path}")

    probe = ffprobe_info(ffprobe, video_path)
    duration = media_duration(probe, video_path)
    vstream = first_stream(probe, "video")
    if vstream is None:
        die(f"recording has no video stream: {video_path}")
    astream = first_stream(probe, "audio")
    has_audio = astream is not None
    if not has_audio:
        warn(f"recording has no audio stream; output will be video only: {video_path}")

    width = int(vstream.get("width") or 0)
    height = int(vstream.get("height") or 0)
    if width <= 0 or height <= 0:
        die(f"cannot determine recording resolution: {video_path}")
    fps = frame_rate_arg(vstream)

    # ---- 解析 segments ----
    segments: list[dict] = []
    for idx, raw in enumerate(segments_raw):
        if not isinstance(raw, dict):
            die(f"segments[{idx}] is not an object: {edit_path}")
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            name = f"seg{idx + 1}"
        else:
            name = name.strip()

        from_label = raw.get("from_marker")
        if not isinstance(from_label, str) or not from_label:
            die(f"segments[{idx}] ({name}) is missing a non-empty from_marker: {edit_path}")
        to_label = raw.get("to_marker")
        if to_label is not None and (not isinstance(to_label, str) or not to_label):
            die(f"segments[{idx}] ({name}) to_marker must be a label string or null: {edit_path}")

        speed_raw = raw.get("speed", 1.0)
        if isinstance(speed_raw, bool) or not isinstance(speed_raw, (int, float)):
            die(f"segments[{idx}] ({name}) speed must be a number: {edit_path}")
        speed = float(speed_raw)
        if speed <= 0:
            die(f"segments[{idx}] ({name}) speed must be > 0, got {speed}")

        start_marker = label_or_die(markers, from_label, f"segments[{idx}] ({name}) from_marker")
        start = start_marker["t"]

        if to_label is None:
            end = duration
            end_desc = "end of recording"
        else:
            end_marker = label_or_die(markers, to_label, f"segments[{idx}] ({name}) to_marker")
            end = end_marker["t"]
            end_desc = f"label {to_label!r} (t={end:g}s)"

        if start >= duration:
            die(
                f"segments[{idx}] ({name}): from_marker t={start:g}s is beyond the recording duration of {duration:.3f}s"
            )
        if end > duration + 0.05:
            warn(
                f"segments[{idx}] ({name}): end {end:.3f}s is beyond the recording duration of {duration:.3f}s; clamped to the end"
            )
            end = duration
        if end - start <= 0.001:
            die(
                f"segments[{idx}] ({name}): invalid interval (from={start:g}s, to={end:g}s), "
                f"to_marker {'is null' if to_label is None else repr(to_label)}"
            )

        seg_dur = end - start
        segments.append({
            "name": name,
            "start": start,
            "end": end,
            "speed": speed,
            "out_dur": seg_dur / speed,
            "end_desc": end_desc,
        })

    expected_total = sum(s["out_dur"] for s in segments)

    # ---- 字幕 ----
    subtitle_path: Path | None = None
    subs_raw = edit.get("subtitles")
    if subs_raw not in (None, "", False):
        if not isinstance(subs_raw, str):
            die(f"edit-list subtitles must be a path string: {edit_path}")
        subtitle_path = resolve_input_path(subs_raw, "subtitle file")
        if subtitle_path.suffix.lower() not in (".srt", ".ass", ".ssa", ".vtt"):
            warn(f"uncommon subtitle extension ({subtitle_path.suffix}); will still try to burn it in: {subtitle_path}")

    # ---- 响度 ----
    loudness_raw = edit.get("loudness_lufs")
    loudness: float | None = None
    if loudness_raw is not None:
        if isinstance(loudness_raw, bool) or not isinstance(loudness_raw, (int, float)):
            die(f"edit-list loudness_lufs must be a number or null: {edit_path}")
        loudness = float(loudness_raw)
        if not (-70.0 <= loudness <= 0.0):
            warn(f"loudness_lufs={loudness} is outside the usual range [-70, 0]; normalizing with the given value anyway")

    # ---- 输出检查（铁律：默认不覆盖） ----
    out_path = Path(args.out).expanduser()
    if not out_path.is_absolute():
        out_path = (Path.cwd() / out_path).resolve()
    sessions_resolved = sessions_root.resolve()
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

    # ---- 组装 filter_complex ----
    n = len(segments)
    parts: list[str] = []
    for i, seg in enumerate(segments):
        parts.append(
            f"[{i}:v]trim=start={format_num(seg['start'])}:end={format_num(seg['end'])}"
            f",setpts=(PTS-STARTPTS)/{format_num(seg['speed'])}"
            f",scale={width}:{height},setsar=1,fps={fps},format=yuv420p[v{i}]"
        )
        if has_audio:
            parts.append(
                f"[{i}:a]atrim=start={format_num(seg['start'])}:end={format_num(seg['end'])}"
                f",asetpts=PTS-STARTPTS"
                f",aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
                f",{atempo_chain(seg['speed'])}[a{i}]"
            )

    v_label = "vcat"
    a_label = "acat"
    if n == 1:
        parts.append(f"[v0]null[vcat]")
        if has_audio:
            parts.append(f"[a0]anull[acat]")
    else:
        feeds = "".join(f"[v{i}][a{i}]" if has_audio else f"[v{i}]" for i in range(n))
        if has_audio:
            parts.append(f"{feeds}concat=n={n}:v=1:a=1[vcat][acat]")
        else:
            parts.append(f"{feeds}concat=n={n}:v=1:a=0[vcat]")

    v_out = v_label
    sub_tmp_file: Path | None = None
    if subtitle_path is not None:
        sub_arg, sub_tmp_file = subtitle_filter(subtitle_path)
        if sub_tmp_file is not None:
            atexit.register(_cleanup_temp_file, sub_tmp_file)
        parts.append(f"[{v_label}]{sub_arg}[vsub]")
        v_out = "vsub"

    a_out = a_label
    if has_audio and loudness is not None:
        # loudnorm 单遍（动态）模式内部有 lookahead，输出帧的时间戳与内容有偏移，
        # 会让 aac 编码器收到非单调 DTS（"Queue input is backward in time"，
        # 并让成片音频比视频长约 80ms）。后面接一个 aresample 把时间戳归一化。
        parts.append(
            f"[{a_label}]loudnorm=I={format_num(loudness)}:TP=-1.5:LRA=11"
            f",aresample=async=1:first_pts=0[aloud]"
        )
        a_out = "aloud"

    filter_complex = ";".join(parts)

    cmd: list[str] = [ffmpeg, "-hide_banner", "-nostdin"]
    cmd += ["-y"] if args.overwrite else ["-n"]
    cmd += ["-loglevel", "warning"]
    for _ in segments:
        cmd += ["-i", str(video_path)]
    cmd += ["-filter_complex", filter_complex]
    cmd += ["-map", f"[{v_out}]"]
    if has_audio:
        cmd += ["-map", f"[{a_out}]"]
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

    # ---- 打印计划 ----
    info(f"Edit list  : {edit_path}")
    info(f"Session    : {session_dir}")
    info(f"Recording  : {video_path}  ({width}x{height}, {fps} fps, {duration:.3f}s)")
    info(f"Subtitles  : {subtitle_path if subtitle_path else '(none)'}")
    info(f"Loudness   : " + (f"loudnorm I={format_num(loudness)}:TP=-1.5:LRA=11" if (has_audio and loudness is not None) else "(none)"))
    for seg in segments:
        info(
            f"  seg {seg['name']}: {seg['start']:.3f}s → {seg['end']:.3f}s"
            f" ({seg['end_desc']})  ×{format_num(seg['speed'])}"
            f"  →  {seg['out_dur']:.3f}s"
        )
    info(f"Expected   : {expected_total:.3f}s")
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

    if proc.stdout and proc.stdout.strip():
        print(proc.stdout.strip(), file=sys.stderr)
    if proc.returncode != 0:
        if not args.overwrite and "already exists" in (proc.stderr or "").lower():
            die(f"output already exists; refusing to overwrite: {out_path}\n  pass --overwrite to replace it")
        print(proc.stderr.strip(), file=sys.stderr)
        die(f"ffmpeg exited with code {exit_code_text(proc.returncode)}; rough cut failed.\n  command: {' '.join(_quote_for_display(c) for c in cmd)}")
    if proc.stderr and proc.stderr.strip():
        print(proc.stderr.strip(), file=sys.stderr)

    if not out_path.is_file():
        die(f"ffmpeg reported success but produced no output file: {out_path}")

    # ---- 校验输出 ----
    out_probe = ffprobe_info(ffprobe, out_path)
    out_dur = media_duration(out_probe, out_path)
    out_v = first_stream(out_probe, "video")
    out_a = first_stream(out_probe, "audio")
    res = f"{out_v.get('width')}x{out_v.get('height')}" if out_v else "no video stream"
    info(f"Done: {out_path}")
    info(f"  measured duration: {out_dur:.3f}s (expected {expected_total:.3f}s)")
    info(f"  resolution       : {res}, video {out_v.get('codec_name') if out_v else '-'}, audio {(out_a.get('codec_name') if out_a else 'none')}")
    if abs(out_dur - expected_total) > max(0.5, expected_total * 0.05):
        warn(f"measured duration differs from expected: {out_dur:.3f}s vs {expected_total:.3f}s")
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