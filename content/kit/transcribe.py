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
    "No usable speech recognition engine found (faster-whisper / openai-whisper).\n"
    "Install either one, then try again:\n"
    "    pip install faster-whisper     # recommended: faster and lighter on memory\n"
    "    pip install openai-whisper     # alternative: requires ffmpeg on PATH\n"
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
    eprint("Error: %s" % message)
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
    return None, "; ".join(reasons) or "neither module could be imported"


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

# CUDA 运行库缺失时 ctranslate2 仍会报告 cuda 可用（nvcuda.dll 是显卡驱动自带的），
# 真正的失败会推迟到第一次计算才抛，表现为加载 cublas/cudart 失败。
_CUDA_FAILURE_HINTS = (
    "cublas", "cudart", "cudnn", "cuda", "not found or cannot be loaded",
)


def _looks_like_cuda_failure(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(hint in text for hint in _CUDA_FAILURE_HINTS)


def run_faster_whisper(audio_path: str, args) -> list:
    from faster_whisper import WhisperModel

    def _attempt(device: str, compute_type: str) -> list:
        model = WhisperModel(
            args.model,
            device=device,
            compute_type=compute_type,
            download_root=args.model_dir,
        )
        segments, info = model.transcribe(
            audio_path,
            language=args.language,
            beam_size=args.beam_size,
            vad_filter=not args.no_vad,
        )
        if args.language:
            eprint("Note: faster-whisper language=%s (--language was set explicitly, not detected)" % args.language)
        else:
            eprint("Note: faster-whisper detected language=%s (probability %.2f)"
                   % (getattr(info, "language", "?"), getattr(info, "language_probability", 0.0)))
        eprint("Note: faster-whisper device=%s compute_type=%s" % (device, compute_type))
        # 必须在此处就地把 segments 取完：CUDA 运行库是懒加载的，
        # 只有真正开始计算才会暴露 "cublas64_12.dll is not found" 这类错误。
        result = []
        for segment in segments:
            result.append((float(segment.start), float(segment.end), (segment.text or "").strip()))
        return result

    try:
        return _attempt(args.device, args.compute_type)
    except Exception as exc:  # noqa: BLE001 - 下面按类型分流
        if _looks_like_cuda_failure(exc):
            if args.device == "auto":
                eprint(
                    "Warning: device=auto resolved to CUDA, but the CUDA runtime libraries failed to load (%s).\n"
                    "      Falling back to CPU and retrying; pass --device cpu to pin this down." % exc
                )
                return _attempt("cpu", "default")
            raise RuntimeError(
                "%s\nNote: this machine may have only a GPU driver and no CUDA Toolkit"
                " (missing cublas64_12.dll and similar runtime libraries). Use --device cpu, "
                "or install the CUDA 12 runtime libraries." % exc
            ) from exc
        raise


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
        raise argparse.ArgumentTypeError("must be an integer: %r" % text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0: %r" % text)
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="transcribe.py",
        description="Transcribe live narration into subtitles: writes <out>.srt and <out>.txt",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python content/kit/transcribe.py --dir content/sessions/2026-10-20-ep01\n"
            "  python content/kit/transcribe.py --input some.mp4 --out content/publish/ep01/take\n"
        ),
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--dir", default=None,
                        help="session directory: reads <dir>/raw.mkv and defaults the output prefix to <dir>/take")
    source.add_argument("--input", default=None, help="input media file (mutually exclusive with --dir)")
    parser.add_argument("--out", default=None,
                        help="output prefix (produces <out>.srt / <out>.txt); defaults to <dir>/take with --dir")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help="model name (default %s; faster-whisper uses small/medium/large-v3, etc.)" % DEFAULT_MODEL)
    parser.add_argument("--language", default=DEFAULT_LANGUAGE,
                        help="language code (default %s; pass auto to let the engine decide)" % DEFAULT_LANGUAGE)
    parser.add_argument("--device", default="auto", help="faster-whisper: auto/cpu/cuda (default auto)")
    parser.add_argument("--compute-type", dest="compute_type", default="default",
                        help="faster-whisper: default/float16/int8, etc. (defaults to default)")
    parser.add_argument("--beam-size", dest="beam_size", type=positive_int, default=5,
                        help="faster-whisper beam size (default 5)")
    parser.add_argument("--no-vad", dest="no_vad", action="store_true",
                        help="disable faster-whisper VAD filtering (enabled by default)")
    parser.add_argument("--model-dir", dest="model_dir", default=None,
                        help="model download/cache directory (defaults to the engine's own)")
    parser.add_argument("--ffmpeg", default=None,
                        help="ffmpeg path (needed by openai-whisper; located automatically and injected into PATH when omitted)")
    parser.add_argument("--overwrite", action="store_true",
                        help="allow overwriting an existing <out>.srt / <out>.txt (errors out by default)")
    parser.add_argument("--allow-empty", dest="allow_empty", action="store_true",
                        help="write empty files even when no speech was recognized (errors out by default)")
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.dir and not args.input:
        die("You must pass --dir or --input (see --help).", EXIT_USAGE)
    if args.input and not args.out:
        die("--input also requires --out (the output prefix).", EXIT_USAGE)

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
        die("Input media file not found: %s" % media_path, EXIT_RUNTIME)

    srt_path = out_prefix + ".srt"
    txt_path = out_prefix + ".txt"
    if not args.overwrite:
        existing = [p for p in (srt_path, txt_path) if os.path.exists(p)]
        if existing:
            die("Output already exists (not overwritten by default): %s\nPass --overwrite to replace it" % ", ".join(existing),
                EXIT_RUNTIME)

    engine, detail = detect_engine()
    if engine is None:
        eprint("Error: %s" % INSTALL_HINT)
        eprint("(current import failure: %s)" % detail)
        eprint("(the audio file itself is fine: %s)" % media_path)
        sys.exit(EXIT_MISSING_ENGINE)

    ffmpeg_path = resolve_ffmpeg(args.ffmpeg)
    ensure_ffmpeg_on_path(ffmpeg_path)
    if engine == "whisper" and not ffmpeg_path:
        eprint("Warning: ffmpeg not found; openai-whisper may be unable to decode the audio. Pass --ffmpeg to point at it.")

    eprint("engine: %s | model: %s | language: %s" % (engine, args.model, args.language))
    eprint("input: %s" % media_path)

    language = None if str(args.language).lower() in ("auto", "") else args.language
    args.language = language
    try:
        if engine == "faster_whisper":
            segments = run_faster_whisper(media_path, args)
        else:
            segments = run_whisper(media_path, args)
    except Exception as exc:  # noqa: BLE001 - 转成可读原因，别甩堆栈
        die("Transcription failed (%s): %s" % (engine, exc), EXIT_RUNTIME)

    segments = [seg for seg in segments if seg[2]]
    if not segments:
        if not args.allow_empty:
            die("No speech was recognized, so no empty subtitle files were written (add --allow-empty if you want them)."
                " Check that the audio track exists and is not too quiet.", EXIT_NO_SPEECH)
        eprint("Warning: no speech was recognized; writing empty files because --allow-empty was given.")

    srt_path, txt_path = write_outputs(out_prefix, segments)
    print("Subtitles written")
    print("  %s (%d segments)" % (srt_path, len(segments)))
    print("  %s" % txt_path)
    return 0


if __name__ == "__main__":
    _force_utf8_stdio()
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        eprint("Interrupted.")
        sys.exit(130)