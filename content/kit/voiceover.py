"""
voiceover.py —— AI 配音:旁白稿 -> mp3 + 字幕(用微软 Edge 神经网络语音,免费、无需 API Key)

用法:
  python content/kit/voiceover.py --script content/publish/ep01/narration.txt --out content/publish/ep01/voice
  # 生成 voice.mp3 和 voice.srt(字幕由 TTS 引擎同步产出,时间轴天然对齐)

音色:默认 en-US-ChristopherNeural(男声,自然,适合 devlog)
  换音色:--voice en-US-GuyNeural / en-US-EricNeural;列出全部可用音色:--list-voices
依赖:pip install edge-tts(见 content/kit/requirements.txt);需要联网
后期升级:想要更高质量/克隆自己声音,用 ElevenLabs API,把本脚本换成 API 调用即可,CLI 接口不变

旁白稿格式:一行一段,空行分段。不要写 [HOOK] 这类结构标签 —— 它们会被念出来。
"""
import argparse
import asyncio
import os
import shutil
import subprocess
import sys
import time

DEFAULT_VOICE = "en-US-ChristopherNeural"
RENDER_ATTEMPTS = 3


def _force_utf8_stdio() -> None:
    """控制台代码页可能是 GBK/CP936；统一按 UTF-8 输出，避免中文经管道乱码。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - 被重定向成非文本流时忽略
            pass


def _load_script(path: str) -> str:
    if not os.path.isfile(path):
        sys.exit(f"narration script not found: {path}")
    with open(path, encoding="utf-8") as f:
        lines = [ln.strip() for ln in f]
    if lines and lines[0].lstrip().startswith("\ufeff"):
        lines[0] = lines[0].lstrip("\ufeff")
    text = " ".join(ln for ln in lines if ln)
    if not text:
        sys.exit(f"narration script is empty: {path}")
    return text


def _ensure_parent(path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


def _try_import_edge_tts():
    try:
        import edge_tts  # noqa: F401
        return edge_tts
    except ImportError:
        return None


def _render_via_module(edge_tts, text: str, voice: str, rate: str, mp3: str, srt: str) -> None:
    """流式合成：音频写文件，边界事件喂给 SubMaker 生成真正的 SRT。

    注意：不要用 ``Communicate.save(audio, x)`` —— 它的第二个参数是 **metadata(JSONL
    边界事件)**，不是字幕文件。要得到 SRT 必须自己喂 SubMaker 再取 ``get_srt()``。
    """
    async def _run() -> None:
        communicate = edge_tts.Communicate(text, voice, rate=rate)
        submaker = edge_tts.SubMaker()
        fed = 0
        with open(mp3, "wb") as audio_file:
            async for chunk in communicate.stream():
                kind = chunk.get("type")
                if kind == "audio":
                    audio_file.write(chunk["data"])
                elif kind in ("WordBoundary", "SentenceBoundary"):
                    try:
                        submaker.feed(chunk)
                        fed += 1
                    except ValueError as exc:
                        # 同一路语音里边界类型理应一致；混合时保留已收事件，不让音频白跑
                        print(f"WARNING: skipped a boundary event ({exc})", file=sys.stderr)
        with open(srt, "w", encoding="utf-8", newline="\n") as sub_file:
            sub_file.write(submaker.get_srt())
        if fed == 0:
            print("WARNING: the engine returned no boundary events; the subtitle file is empty.", file=sys.stderr)

    asyncio.run(_run())


def _render_via_cli(text: str, voice: str, rate: str, mp3: str, srt: str) -> None:
    exe = shutil.which("edge-tts")
    if exe is None:
        sys.exit(
            "missing dependency: edge-tts not found (neither as a Python module nor on PATH).\n"
            "install it first:  pip install -r content/kit/requirements.txt"
        )
    cmd = [exe, "--voice", voice, "--rate", rate,
           "--text", text, "--write-media", mp3, "--write-subtitles", srt]
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        sys.exit(f"edge-tts failed (exit code {e.returncode}). Check your network connection and retry.")


def _list_voices(edge_tts) -> None:
    if edge_tts is not None:
        async def _run():
            return await edge_tts.list_voices()
        voices = asyncio.run(_run())
        for v in sorted(voices, key=lambda x: x.get("ShortName", "")):
            print(f"{v.get('ShortName','?'):<40} {v.get('Gender','?'):<8} {v.get('Locale','?')}")
        return

    exe = shutil.which("edge-tts")
    if exe is None:
        sys.exit(
            "missing dependency: edge-tts not found.\n"
            "install it first:  pip install -r content/kit/requirements.txt"
        )
    subprocess.run([exe, "--list-voices"], check=True)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="narration script -> mp3 + srt (Edge neural voice)",
    )
    ap.add_argument("--script", help="narration script as plain text (one paragraph per line, blank line separates paragraphs)")
    ap.add_argument("--out", help="output prefix (without extension); produces <out>.mp3 and <out>.srt")
    ap.add_argument("--voice", default=DEFAULT_VOICE,
                    help=f"voice, default {DEFAULT_VOICE}")
    ap.add_argument("--rate", default="+0%", help="speech rate, e.g. +5%% / -10%%")
    ap.add_argument("--list-voices", action="store_true",
                    help="list all available voices and exit")
    a = ap.parse_args()

    edge_tts = _try_import_edge_tts()

    if a.list_voices:
        _list_voices(edge_tts)
        return

    if not a.script or not a.out:
        ap.error("--script and --out are required (or use --list-voices)")

    text = _load_script(a.script)

    mp3, srt = a.out + ".mp3", a.out + ".srt"
    _ensure_parent(mp3)
    _ensure_parent(srt)

    # 合成：合成服务是远程 websocket，偶发连接超时；做有限次重试，
    # 并把异常翻译成可读原因，避免甩出 Python 堆栈。
    last_error = None
    for attempt in range(1, RENDER_ATTEMPTS + 1):
        try:
            if edge_tts is not None:
                _render_via_module(edge_tts, text, a.voice, a.rate, mp3, srt)
            else:
                _render_via_cli(text, a.voice, a.rate, mp3, srt)
            last_error = None
            break
        except Exception as exc:  # noqa: BLE001 - SystemExit 不在此列，会直接穿过
            last_error = exc
            if attempt < RENDER_ATTEMPTS:
                print(
                    f"WARNING: synthesis attempt {attempt}/{RENDER_ATTEMPTS} failed"
                    f" ({type(exc).__name__}: {exc}); retrying in {attempt * 2}s...",
                    file=sys.stderr,
                )
                time.sleep(attempt * 2)

    if last_error is not None:
        sys.exit(
            f"voiceover failed (after {RENDER_ATTEMPTS} attempts): {type(last_error).__name__}: {last_error}\n"
            "  common causes: no network access / blocked connection (the synthesis service is wss://speech.platform.bing.com), "
            "or temporary rate limiting. Check your network and retry."
        )

    for path in (mp3, srt):
        if not os.path.isfile(path):
            sys.exit(f"the engine exited without producing a file: {path}")

    # 守卫：确认产出的确实是一份 SRT。
    # 曾经踩过：把 Communicate.save(audio, metadata) 的第二参数当成字幕，
    # 结果写出的是 JSONL 边界事件而不是字幕。这里必须挡住，不能静默交付。
    with open(srt, encoding="utf-8-sig") as f:
        head = f.read(8192)
    if not head.strip():
        sys.exit(f"subtitle file is empty (the engine returned no boundary events): {srt}")
    if "-->" not in head:
        sys.exit(
            f"the produced subtitle is not SRT (no '-->' timeline found): {srt}\n"
            "  first line: %s" % head.strip().splitlines()[0][:120]
        )

    print(f"voiceover done: {mp3}")
    print(f"subtitles done: {srt} (ready to burn in with roughcut.py)")


if __name__ == "__main__":
    _force_utf8_stdio()
    try:
        main()
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        sys.exit(130)