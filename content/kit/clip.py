#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""clip.py —— 切 9:16 竖版短片。

契约: content/kit/README.md 第 2 节 "clip.py —— 切 9:16 竖版"

用法（定位二选一）:
    python content/kit/clip.py --dir content/sessions/2026-10-20-ep01 \
        --from-label "fail-第一次运行" --to-label "fail-翻车现场" --out content/publish/ep01/short1.mp4
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
    "请安装 ffmpeg（含 ffprobe）并加入 PATH，或：\n"
    "  · 设置环境变量 {env}=<可执行文件路径>\n"
    "  · 或加命令行参数 {flag} <可执行文件路径>\n"
    "  · 本机常见位置: E:\\ffmpeg\\bin\\ffmpeg.exe"
)

# ---------------------------------------------------------------- 输出辅助


def warn(msg: str) -> None:
    print("警告: " + str(msg), file=sys.stderr)


def info(msg: str) -> None:
    print(str(msg))


def die(msg: str, code: int = 1) -> "None":
    print("错误: " + str(msg), file=sys.stderr)
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
    die(f"未找到 {tool}。\n{hint}")


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
        die(f"无法执行 ffprobe: {exc}")
    if proc.returncode != 0:
        die(f"ffprobe 无法读取 {path}\n{proc.stderr.strip()}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        die(f"ffprobe 输出无法解析（{path}）: {exc}")


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
        die(f"无法确定媒体时长（ffprobe 未给出 duration）: {path}")


# ---------------------------------------------------------------- JSON


def load_json(path: Path, what: str) -> dict:
    if not path.is_file():
        die(f"{what} 不存在: {path}")
    try:
        # utf-8-sig：兼容 Windows 上被记事本/PowerShell 写出 BOM 的 UTF-8 文件。
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        die(f"无法读取 {what}（{path}）: {exc}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        die(f"{what} 不是合法 JSON（{path}）: {exc}")
    if not isinstance(data, dict):
        die(f"{what} 顶层必须是 JSON 对象: {path}")
    return data


# ---------------------------------------------------------------- 定位


def normalize_markers(doc: dict, markers_path: Path) -> list[dict]:
    raw_markers = doc.get("markers")
    if not isinstance(raw_markers, list) or not raw_markers:
        die(f"markers.json 中没有可用的 markers 数组: {markers_path}")
    markers: list[dict] = []
    for idx, item in enumerate(raw_markers):
        if not isinstance(item, dict):
            die(f"markers.json 的 markers[{idx}] 不是对象: {markers_path}")
        label = item.get("label")
        t = item.get("t")
        if not isinstance(label, str) or not label:
            die(f"markers.json 的 markers[{idx}] 缺少非空 label: {markers_path}")
        if isinstance(t, bool) or not isinstance(t, (int, float)):
            die(f"markers.json 的 markers[{idx}].t 必须是数字: {markers_path}")
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
    return "\n".join(lines) if lines else "    （markers.json 中没有标签）"


def index_or_die(markers: list[dict], label: str, where: str) -> int:
    idx = find_marker_index(markers, label)
    if idx is None:
        die(
            f"{where}: 找不到标签 {label!r}。\n"
            f"  可用标签（markers.json 共 {len(markers)} 条）:\n{available_labels(markers)}"
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
        description="切 9:16 竖版短片（输出固定 1080x1920，H.264 + AAC）。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python content/kit/clip.py --dir content/sessions/2026-10-20-ep01 \\\n"
            "      --from-label \"fail-第一次运行\" --to-label \"fail-翻车现场\" --out content/publish/ep01/short1.mp4\n"
            "  python content/kit/clip.py --input long.mp4 --start 12.5 --end 40.0 \\\n"
            "      --out content/publish/ep01/short2.mp4\n"
            "\n"
            "定位二选一:\n"
            "  · --dir + --from-label/--to-label（读 <dir>/markers.json）\n"
            "  · --input + --start/--end（秒）\n"
            "  --to-label 省略时取下一个 marker 或源结尾。\n"
            "  publish/ 已有同名产物时默认报错，需显式 --overwrite。\n"
        ),
    )

    locator = parser.add_mutually_exclusive_group(required=True)
    locator.add_argument("--dir", metavar="DIR", help="会话目录（内含 markers.json 与 raw.mkv）")
    locator.add_argument("--input", metavar="PATH", help="源视频路径（配合 --start/--end）")

    parser.add_argument("--from-label", dest="from_label", metavar="LABEL",
                        help="起点标签（配合 --dir）")
    parser.add_argument("--to-label", dest="to_label", metavar="LABEL",
                        help="终点标签（配合 --dir）；省略则取下一个 marker 或源结尾")
    parser.add_argument("--start", type=float, metavar="SEC",
                        help="起点秒数（配合 --input，必填）")
    parser.add_argument("--end", type=float, metavar="SEC",
                        help="终点秒数（配合 --input）；省略则到源结尾")

    parser.add_argument("--out", required=True, metavar="PATH", help="输出 mp4 路径")
    parser.add_argument("--overwrite", action="store_true", help="允许覆盖已存在的输出文件")
    parser.add_argument("--sessions-root", default=None, metavar="DIR",
                        help=f"会话根目录，默认 {DEFAULT_SESSIONS_ROOT}")
    parser.add_argument("--ffmpeg", default=None, metavar="PATH",
                        help="ffmpeg 可执行文件路径（也可用环境变量 FFMPEG_BIN）")
    parser.add_argument("--ffprobe", default=None, metavar="PATH",
                        help="ffprobe 可执行文件路径（也可用环境变量 FFPROBE_BIN）")
    parser.add_argument("--preset", default="medium", help="x264 preset，默认 medium")
    parser.add_argument("--crf", type=float, default=20.0, help="x264 CRF，默认 20")
    parser.add_argument("--dry-run", action="store_true", help="只打印将要执行的 ffmpeg 命令，不执行")
    return parser.parse_args(argv)


def build_vertical_filter(width: int, height: int) -> tuple[str, str]:
    """按源分辨率选择滤镜：横屏/方屏居中裁切放大，竖屏直接缩放。"""
    src_aspect = width / height if height else 0.0
    if src_aspect > TARGET_ASPECT + 1e-9:
        # 横屏源：先按高度裁出 9:16 宽度，再放大到 1080x1920（crop 默认水平居中）
        filt = f"crop=ih*9/16:ih,scale={OUT_WIDTH}:{OUT_HEIGHT},setsar=1"
        desc = f"横屏源 {width}x{height} → crop=ih*9/16:ih → scale={OUT_WIDTH}x{OUT_HEIGHT}（居中裁切）"
    else:
        filt = f"scale={OUT_WIDTH}:{OUT_HEIGHT},setsar=1"
        desc = f"竖屏/窄源 {width}x{height} → scale={OUT_WIDTH}x{OUT_HEIGHT}"
    return filt, desc


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    ffmpeg = resolve_tool("ffmpeg", args.ffmpeg, FFMPEG_ENV, FFMPEG_FALLBACKS)
    ffprobe = resolve_tool("ffprobe", args.ffprobe, FFPROBE_ENV, FFPROBE_FALLBACKS)

    if args.dir:
        if args.start is not None or args.end is not None:
            die("--dir 不能用 --start/--end；混用请改用 --input")
        if not args.from_label:
            die("--dir 必须配合 --from-label（起点标签）")
        if args.to_label is not None and not args.to_label:
            die("--to-label 不能为空字符串；省略它表示取下一个 marker 或源结尾")

        sessions_root = Path(args.sessions_root).expanduser() if args.sessions_root else DEFAULT_SESSIONS_ROOT
        session_dir = Path(args.dir).expanduser()
        if not session_dir.is_absolute():
            cwd_candidate = (Path.cwd() / session_dir).resolve()
            root_candidate = (REPO_ROOT / session_dir).resolve()
            session_dir = cwd_candidate if cwd_candidate.is_dir() else root_candidate
        if not session_dir.is_dir():
            alt = sessions_root / Path(args.dir).name
            die(f"会话目录不存在: {session_dir}" + (f"\n  是否想用: {alt}" if alt.is_dir() else ""))

        markers_path = session_dir / "markers.json"
        if not markers_path.is_file():
            die(f"缺少 markers.json，不得猜测时间点: {markers_path}")
        markers_doc = load_json(markers_path, "markers.json")
        markers = normalize_markers(markers_doc, markers_path)

        video_name = markers_doc.get("video")
        if not isinstance(video_name, str) or not video_name.strip():
            video_name = "raw.mkv"
        source = (session_dir / video_name).resolve()
        if not source.is_file():
            die(f"录像文件不存在: {source}")

        from_idx = index_or_die(markers, args.from_label, "--from-label")
        start = markers[from_idx]["t"]

        if args.to_label is not None:
            to_idx = index_or_die(markers, args.to_label, "--to-label")
            end = markers[to_idx]["t"]
            end_desc = f"标签 {args.to_label!r} (t={end:g}s)"
        elif from_idx + 1 < len(markers):
            nxt = markers[from_idx + 1]
            end = nxt["t"]
            end_desc = f"下一个 marker {nxt['label']!r} (t={end:g}s)"
        else:
            end = None  # 源结尾，探测后再定
            end_desc = "源结尾"

        locate_desc = (
            f"--dir {session_dir}\n"
            f"  from-label {args.from_label!r} → t={start:g}s\n"
            f"  to         {end_desc}"
        )
    else:
        if args.from_label or args.to_label:
            die("--input 不能用 --from-label/--to-label；请改用 --dir 或 --start/--end")
        if args.start is None:
            die("--input 必须配合 --start（起点秒数）")
        source = Path(args.input).expanduser()
        if not source.is_absolute():
            cwd_candidate = (Path.cwd() / source).resolve()
            root_candidate = (REPO_ROOT / source).resolve()
            source = cwd_candidate if cwd_candidate.is_file() else root_candidate
        if not source.is_file():
            die(f"源视频不存在: {args.input}")
        if args.start < 0:
            die(f"--start 不能为负: {args.start}")
        start = float(args.start)
        end = float(args.end) if args.end is not None else None
        if end is not None and end <= start:
            die(f"--end ({end:g}s) 必须大于 --start ({start:g}s)")
        locate_desc = f"--input {source}\n  start={start:g}s\n  end={'源结尾' if end is None else f'{end:g}s'}"

    # ---- 探测源 ----
    probe = ffprobe_info(ffprobe, source)
    duration = media_duration(probe, source)
    vstream = first_stream(probe, "video")
    if vstream is None:
        die(f"源没有视频流: {source}")
    astream = first_stream(probe, "audio")
    has_audio = astream is not None
    if not has_audio:
        warn(f"源没有音频流，输出将无音轨: {source}")

    width = int(vstream.get("width") or 0)
    height = int(vstream.get("height") or 0)
    if width <= 0 or height <= 0:
        die(f"无法确定源分辨率: {source}")

    if start >= duration:
        die(f"起点 {start:g}s 超出源时长 {duration:.3f}s")
    if end is None or end > duration + 0.05:
        raw_end = end
        end = duration
        if raw_end is not None:
            warn(f"终点 {raw_end:.3f}s 超出源时长 {duration:.3f}s，已截到源结尾")
    if end - start <= 0.001:
        die(f"区间无效: start={start:g}s, end={end:g}s")

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
        die(f"拒绝把输出写进 sessions/（只增不删，禁止覆盖录像）: {out_path}")
    if out_path.exists() and not args.overwrite:
        die(f"输出已存在，拒绝覆盖: {out_path}\n  确认要覆盖请加 --overwrite")
    if out_path.is_dir():
        die(f"输出路径是目录: {out_path}")

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

    info(f"定位       : {locate_desc}")
    info(f"源         : {source}  ({width}x{height}, {duration:.3f}s)")
    info(f"裁切       : {vf_desc}")
    info(f"输出规格   : {OUT_WIDTH}x{OUT_HEIGHT} (9:16), H.264 + AAC")
    info(f"片段时长   : {clip_dur:.3f}s  ({start:.3f}s → {end:.3f}s)")
    info(f"输出       : {out_path}")

    if args.dry_run:
        info("dry-run，未执行。命令:")
        info(" ".join(_quote_for_display(c) for c in cmd))
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
    except OSError as exc:
        die(f"无法执行 ffmpeg: {exc}")

    if proc.returncode != 0:
        if not args.overwrite and "already exists" in (proc.stderr or "").lower():
            die(f"输出已存在，拒绝覆盖: {out_path}\n  确认要覆盖请加 --overwrite")
        if proc.stderr and proc.stderr.strip():
            print(proc.stderr.strip(), file=sys.stderr)
        die(f"ffmpeg 退出码 {exit_code_text(proc.returncode)}，切竖版失败。\n  命令: {' '.join(_quote_for_display(c) for c in cmd)}")
    if proc.stderr and proc.stderr.strip():
        print(proc.stderr.strip(), file=sys.stderr)

    if not out_path.is_file():
        die(f"ffmpeg 报告成功但没有生成输出文件: {out_path}")

    out_probe = ffprobe_info(ffprobe, out_path)
    out_dur = media_duration(out_probe, out_path)
    out_v = first_stream(out_probe, "video")
    out_a = first_stream(out_probe, "audio")
    out_w = int(out_v.get("width") or 0) if out_v else 0
    out_h = int(out_v.get("height") or 0) if out_v else 0
    info(f"完成: {out_path}")
    info(f"  实测分辨率: {out_w}x{out_h} (期望 {OUT_WIDTH}x{OUT_HEIGHT})")
    info(f"  实测时长  : {out_dur:.3f}s（期望 ≈{clip_dur:.3f}s）")
    info(f"  编码      : 视频 {out_v.get('codec_name') if out_v else '-'}, 音频 {out_a.get('codec_name') if out_a else '无'}")
    if (out_w, out_h) != (OUT_WIDTH, OUT_HEIGHT):
        die(f"输出分辨率不符合 9:16 规格: {out_w}x{out_h}（期望 {OUT_WIDTH}x{OUT_HEIGHT}）")
    if has_audio and out_a is None:
        die("输出缺少音轨（源有音频，规格要求 H.264 + AAC）")
    if abs(out_dur - clip_dur) > max(0.5, clip_dur * 0.05):
        warn(f"实测时长与请求时长相差较大: {out_dur:.3f}s vs {clip_dur:.3f}s")
    return 0


def _quote_for_display(token: str) -> str:
    if token == "" or any(ch.isspace() for ch in token):
        return '"' + token.replace('"', '\\"') + '"'
    return token


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("已中断", file=sys.stderr)
        sys.exit(130)