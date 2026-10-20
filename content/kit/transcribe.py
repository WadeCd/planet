#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""transcribe.py —— 现场口播转字幕（实现 content/kit/README.md 第 2 节契约）

用法::

    python content/kit/transcribe.py --dir content/sessions/2026-10-20-ep01
    python content/kit/transcribe.py --input some.mp4 --out content/publish/ep01/take

* 产出 ``<out>.srt`` 和 ``<out>.txt``。``--dir`` 时默认取会话的 ``raw.mkv``，
  输出前缀为 ``<dir>/take``。
* 语音识别引擎缺失时**明确报错并给出安装命令**，绝不静默产出空字幕。
* 引擎优先 ``faster-whisper``，回退 ``openai-whisper``。
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys

VIDEO_NAME = "raw.mkv"
DEFAULT_MODEL = "small"
DEFAULT_LANGUAGE = "zh"

EXIT_RUNTIME = 1
EXIT_USAGE = 2
EXIT_MISSING_ENGINE = 3
EXIT_NO_SPEECH = 4

INSTALL_HINT = (
    "未找到可用的语音识别引擎（faster-whisper / openai-whisper）。\n"
    "请任选一种安装后重试:\n"
    "    pip install faster-whisper     # 推荐：更快、更省内存\n"
    "    pip install openai-whisper     # 备选：需要 ffmpeg 在 PATH 上\n"
)

FFMPEG_FALLBACKS = [
    r"E:\ffmpeg\bin\ffmpeg.exe",
    r"C:\ffmpeg\bin\ffmpeg.exe",
    r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
]


def eprint(*args) -> None:
    print(*args, file=sys.stderr)


def _force_utf8_stdio() -> None:
    """控制台代码页可能是 GBK/CP936；统一按 UTF-8 输出，保证信息可读。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - 被重定向成非文本流时忽略
            pass


def die(message: str, code: int = EXIT_RUNTIME) -> "NoReturn":  # type: ignore[name-defined]
    eprint("错误: %s" % message)
    sys.exit(code)


# ---------------------------------------------------------------- 引擎

def detect_engine():
    """返回 (engine_name, detail)；都不存在时 engine_name 为 None。"""
    reasons = []
    try:
        import faster_whisper  # noqa: F401
        return "faster_whisper", None
    except Exception as exc:  # noqa: BLE001 - 缺包 / 依赖损坏都算不可用
        reasons.append("faster_whisper: %s" % exc)
    try:
        import whisper  # noqa: F401
        return "whisper", None
    except Exception as exc:  # noqa: BLE001
        reasons.append("whisper: %s" % exc)
    return None, "; ".join(reasons) or "两个模块都不可导入"


def resolve_ffmpeg(explicit=None):
    """openai-whisper 需要 ffmpeg 在 PATH 上；这里帮它找到并注入 PATH。"""
    candidates = []
    if explicit:
        candidates.append(explicit)
    for env_name in ("FFMPEG", "FFMPEG_BIN"):
        value = os.environ.get(env_name)
        if value:
            candidates.append(value)
    found = shutil.which("ffmpeg")
    if found:
        candidates.append(found)
    candidates.extend(FFMPEG_FALLBACKS)
    for candidate in candidates:
        if not candidate:
            continue
        if os.sep in candidate or "/" in candidate:
            if os.path.isfile(candidate):
                return os.path.abspath(candidate)
        else:
            hit = shutil.which(candidate)
            if hit:
                return os.path.abspath(hit)
    return None


def ensure_ffmpeg_on_path(ffmpeg_path) -> None:
    if not ffmpeg_path:
        return
    folder = os.path.dirname(ffmpeg_path)
    if folder and folder not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = folder + os.pathsep + os.environ.get("PATH", "")


# ---------------------------------------------------------------- 运行

def run_faster_whisper(audio_path: str, args) -> list:
    from faster_whisper import WhisperModel

    model = WhisperModel(
        args.model,
        device=args.device,
        compute_type=args.compute_type,
        download_root=args.model_dir,
    )
    segments, info = model.transcribe(
        audio_path,
        language=args.language,
        beam_size=args.beam_size,
        vad_filter=not args.no_vad,
    )
    eprint("提示: faster-whisper 识别语言=%s(概率 %.2f)"
           % (getattr(info, "language", args.language), getattr(info, "language_probability", 0.0)))
    result = []
    for segment in segments:
        result.append((float(segment.start), float(segment.end), (segment.text or "").strip()))
    return result


def run_whisper(audio_path: str, args) -> list:
    import whisper

    model = whisper.load_model(args.model, download_root=args.model_dir)
    transcription = model.transcribe(
        audio_path,
        language=args.language,
        verbose=False,
    )
    return [
        (float(seg["start"]), float(seg["end"]), str(seg.get("text", "")).strip())
        for seg in transcription.get("segments", [])
    ]


# ---------------------------------------------------------------- 输出

def format_timestamp(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    hours, rest = divmod(total_ms, 3600 * 1000)
    minutes, rest = divmod(rest, 60 * 1000)
    secs, millis = divmod(rest, 1000)
    return "%02d:%02d:%02d,%03d" % (hours, minutes, secs, millis)


def write_outputs(out_prefix: str, segments: list) -> tuple:
    srt_path = out_prefix + ".srt"
    txt_path = out_prefix + ".txt"

    blocks = []
    lines = []
    for index, (start, end, text) in enumerate(segments, start=1):
        if end < start:
            end = start
        blocks.append("%d\n%s --> %s\n%s\n" % (index, format_timestamp(start), format_timestamp(end), text))
        if text:
            lines.append(text)

    folder = os.path.dirname(os.path.abspath(srt_path))
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(srt_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(blocks))
    with open(txt_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("".join(line + "\n" for line in lines))
    return srt_path, txt_path


# ---------------------------------------------------------------- CLI

def positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("必须是整数: %r" % text)
    if value <= 0:
        raise argparse.ArgumentTypeError("必须大于 0: %r" % text)
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="transcribe.py",
        description="现场口播转字幕：输出 <out>.srt 与 <out>.txt",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python content/kit/transcribe.py --dir content/sessions/2026-10-20-ep01\n"
            "  python content/kit/transcribe.py --input some.mp4 --out content/publish/ep01/take\n"
        ),
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--dir", default=None,
                        help="会话目录：输入取 <dir>/raw.mkv，输出前缀默认 <dir>/take")
    source.add_argument("--input", default=None, help="输入媒体文件（与 --dir 二选一）")
    parser.add_argument("--out", default=None,
                        help="输出前缀（产出 <out>.srt / <out>.txt）；--dir 时默认 <dir>/take")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help="模型名（默认 %s；faster-whisper 用 small/medium/large-v3 等）" % DEFAULT_MODEL)
    parser.add_argument("--language", default=DEFAULT_LANGUAGE,
                        help="语言代码（默认 %s；传 auto 让引擎自己判断）" % DEFAULT_LANGUAGE)
    parser.add_argument("--device", default="auto", help="faster-whisper: auto/cpu/cuda（默认 auto）")
    parser.add_argument("--compute-type", dest="compute_type", default="default",
                        help="faster-whisper: default/float16/int8 等（默认 default）")
    parser.add_argument("--beam-size", dest="beam_size", type=positive_int, default=5,
                        help="faster-whisper beam size（默认 5）")
    parser.add_argument("--no-vad", dest="no_vad", action="store_true",
                        help="关闭 faster-whisper 的 VAD 过滤（默认开启）")
    parser.add_argument("--model-dir", dest="model_dir", default=None,
                        help="模型下载/缓存目录（缺省用引擎默认）")
    parser.add_argument("--ffmpeg", default=None,
                        help="ffmpeg 路径（openai-whisper 需要；缺省自动查找并注入 PATH）")
    parser.add_argument("--overwrite", action="store_true",
                        help="允许覆盖已存在的 <out>.srt / <out>.txt（默认报错）")
    parser.add_argument("--allow-empty", dest="allow_empty", action="store_true",
                        help="没识别出任何语音时也写出空文件（默认报错退出）")
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.dir and not args.input:
        die("必须指定 --dir 或 --input（看 --help）。", EXIT_USAGE)
    if args.input and not args.out:
        die("用 --input 时必须同时给出 --out（输出前缀）。", EXIT_USAGE)

    if args.dir:
        session_dir = os.path.abspath(args.dir)
        media_path = os.path.join(session_dir, VIDEO_NAME)
        out_prefix = args.out or os.path.join(session_dir, "take")
    else:
        media_path = args.input
        out_prefix = args.out

    # 用户可能传 "take.srt"，这里统一去掉后缀，产出 <out>.srt / <out>.txt
    for suffix in (".srt", ".txt"):
        if out_prefix.lower().endswith(suffix):
            out_prefix = out_prefix[: -len(suffix)]
            break

    media_path = os.path.abspath(media_path)
    out_prefix = os.path.abspath(out_prefix)

    if not os.path.isfile(media_path):
        die("找不到输入媒体文件: %s" % media_path, EXIT_RUNTIME)

    srt_path = out_prefix + ".srt"
    txt_path = out_prefix + ".txt"
    if not args.overwrite:
        existing = [p for p in (srt_path, txt_path) if os.path.exists(p)]
        if existing:
            die("输出已存在（默认不覆盖）: %s\n需要覆盖请加 --overwrite" % ", ".join(existing),
                EXIT_RUNTIME)

    engine, detail = detect_engine()
    if engine is None:
        eprint("错误: %s" % INSTALL_HINT)
        eprint("（当前导入失败原因: %s）" % detail)
        eprint("（音频文件本身没问题: %s）" % media_path)
        sys.exit(EXIT_MISSING_ENGINE)

    ffmpeg_path = resolve_ffmpeg(args.ffmpeg)
    ensure_ffmpeg_on_path(ffmpeg_path)
    if engine == "whisper" and not ffmpeg_path:
        eprint("警告: 没找到 ffmpeg，openai-whisper 可能无法解码音频；可用 --ffmpeg 指定。")

    eprint("引擎: %s | 模型: %s | 语言: %s" % (engine, args.model, args.language))
    eprint("输入: %s" % media_path)

    language = None if str(args.language).lower() in ("auto", "") else args.language
    args.language = language
    try:
        if engine == "faster_whisper":
            segments = run_faster_whisper(media_path, args)
        else:
            segments = run_whisper(media_path, args)
    except Exception as exc:  # noqa: BLE001 - 转成可读原因，别甩堆栈
        die("识别失败（%s）: %s" % (engine, exc), EXIT_RUNTIME)

    segments = [seg for seg in segments if seg[2]]
    if not segments:
        if not args.allow_empty:
            die("没有识别出任何语音，未写出空字幕文件（如需空文件请加 --allow-empty）。"
                "请检查音轨是否存在、音量是否过低。", EXIT_NO_SPEECH)
        eprint("警告: 没有识别出任何语音，按 --allow-empty 写出空文件。")

    srt_path, txt_path = write_outputs(out_prefix, segments)
    print("已生成字幕")
    print("  %s（%d 条）" % (srt_path, len(segments)))
    print("  %s" % txt_path)
    return 0


if __name__ == "__main__":
    _force_utf8_stdio()
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        eprint("已中断。")
        sys.exit(130)