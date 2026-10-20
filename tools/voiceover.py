"""
voiceover.py —— AI 配音:旁白稿 -> mp3 + 字幕(用微软 Edge 神经网络语音,免费、无需 API Key)
用法:
  python voiceover.py --script publish/ep01/narration.txt --out publish/ep01/voice
  # 生成 voice.mp3 和 voice.srt(字幕由 edge-tts 同步产出,时间轴天然对齐)

音色:默认 en-US-ChristopherNeural(男声,自然,适合 devlog)
  换音色:--voice en-US-GuyNeural / en-US-EricNeural;查全部:edge-tts --list-voices
依赖:pip install edge-tts(见 requirements.txt);需要联网
后期升级:想要更高质量/克隆自己声音,用 ElevenLabs API,把本脚本换成 API 调用即可,接口不变
"""
import argparse, subprocess, sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", required=True, help="旁白稿纯文本(一行一段,空行分段)")
    ap.add_argument("--out", required=True, help="输出前缀(不带扩展名)")
    ap.add_argument("--voice", default="en-US-ChristopherNeural")
    ap.add_argument("--rate", default="+0%", help="语速,如 +5% / -10%")
    a = ap.parse_args()

    with open(a.script, encoding="utf-8") as f:
        text = " ".join(line.strip() for line in f if line.strip())
    if not text:
        sys.exit("旁白稿是空的")

    mp3, srt = a.out + ".mp3", a.out + ".srt"
    cmd = ["edge-tts", "--voice", a.voice, "--rate", a.rate,
           "--text", text, "--write-media", mp3, "--write-subtitles", srt]
    subprocess.run(cmd, check=True)
    print(f"配音完成: {mp3}")
    print(f"字幕完成: {srt}(可直接给 roughcut.py 烧录)")


if __name__ == "__main__":
    main()
