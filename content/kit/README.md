# content/kit —— 内容流水线脚本包（契约文档）

本目录是 EP 内容流水线的全部可执行工具。**所有脚本共用下面这一份契约**，改动契约必须同时改所有相关脚本。

对应 `README/AGENT-KICKOFF.md` 的 8 步流程。

## 0. 目录约定

```
content/
  kit/                     本脚本包
  sessions/<日期>-ep<期数>/  录像会话:raw.mkv + markers.json
  edits/ep<期数>.json        剪辑单(Shanks 出)
  publish/ep<期数>/          发布包:成品视频 + narration.txt + voice.srt + 文案 .md
```

会话目录名格式：`YYYY-MM-DD-epNN`，例：`content/sessions/2026-10-20-ep01/`。

## 1. markers.json（唯一共享数据结构）

`markers.json` 写在会话目录内，是**打点与剪辑之间唯一的接口**。

```json
{
  "session": "2026-10-20-ep01",
  "started_at": "2026-10-20T21:30:00+08:00",
  "stopped_at": "2026-10-20T22:15:03+08:00",
  "video": "raw.mkv",
  "markers": [
    {"label": "hook-绕圈成功", "t": 12.5, "at": "2026-10-20T21:30:12+08:00"},
    {"label": "fix-跑通了", "t": 1284.0, "at": "2026-10-20T21:51:24+08:00"}
  ]
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `session` | string | 会话目录名 |
| `started_at` | string | 开录时刻，ISO 8601 **带时区偏移** |
| `stopped_at` | string \| null | 停录时刻；未停录时为 `null` |
| `video` | string | 相对会话目录的录像文件名，恒为 `raw.mkv` |
| `markers` | array | 按 `t` 升序 |
| `markers[].label` | string | 标签，**原样照抄设计文档**（可含中文） |
| `markers[].t` | number | 相对开录的秒数，float |
| `markers[].at` | string | 打点墙钟时刻，ISO 8601 带偏移 |

**关键约定：**
- `t` 从 `started_at` 推算（`now - started_at`），**不依赖录制进程内存**，所以 `mark` 可以是独立进程调用。
- 标签可重复（同一标签可打多次）。
- 写文件一律用 UTF-8、`ensure_ascii=False`、`indent=2`。
- 允许未停录的 `markers.json` 被读取；`stopped_at=null` 时以最后一条 marker 之后视为可用范围。

## 2. 命令行契约

所有脚本：Python 3.9+、仅依赖标准库 + `requirements.txt` 所列包、参数用 `argparse`、失败时 `sys.exit(非0)` 并打印可读原因。**任何脚本都必须支持 `--help`。**

### record.py —— 录制与打点

```
python content/kit/record.py start --dir content/sessions/2026-10-20-ep01
python content/kit/record.py mark  --dir content/sessions/2026-10-20-ep01 --label "fail-第一次运行"
python content/kit/record.py stop  --dir content/sessions/2026-10-20-ep01
```

- `start`：建目录 → 启动 ffmpeg 抓屏写 `<dir>/raw.mkv` → 写 `markers.json`（`stopped_at=null`）→ 把 ffmpeg PID 存到 `<dir>/.recording.pid`。若该会话已在录制中则报错退出。
- `mark`：读 `markers.json`，按 `now - started_at` 追加一条 marker 并落盘。**不需要录制进程存活**。
- `stop`：优雅结束 ffmpeg（先发可正常收尾的请求，超时再强杀）→ 回填 `stopped_at` → 删除 `.recording.pid`。若未在录制则报错退出。
- 抓屏用 ffmpeg `gdigrab`（Windows）。录屏帧率、是否含音频由参数控制，默认可在无音频设备时正常工作。

### transcribe.py —— 现场口播转字幕

```
python content/kit/transcribe.py --dir content/sessions/2026-10-20-ep01
python content/kit/transcribe.py --input some.mp4 --out content/publish/ep01/take
```

- 产出 `<out>.srt` 和 `<out>.txt`。`--dir` 时默认取会话的 `raw.mkv`，输出前缀为 `<dir>/take`。
- 语音识别引擎缺失时必须**明确报错并给出安装命令**，不得静默产出空字幕。

### voiceover.py —— 旁白稿 → mp3 + srt

```
python content/kit/voiceover.py --script content/publish/ep01/narration.txt --out content/publish/ep01/voice
```

- 产出 `<out>.mp3` 和 `<out>.srt`（时间轴由渲染引擎同步产出）。
- 参数：`--voice`（默认 `en-US-ChristopherNeural`）、`--rate`（默认 `+0%`）。
- 旁白稿格式：一行一段，空行分段。
- 后期若要换 ElevenLabs 克隆音色，**只换内部实现，CLI 接口不变**。

### roughcut.py —— 按剪辑单粗剪

```
python content/kit/roughcut.py --edit content/edits/ep01.json --out content/publish/ep01/long.mp4
```

剪辑单 `content/edits/ep01.json`：

```json
{
  "episode": "ep01",
  "session": "2026-10-20-ep01",
  "subtitles": "content/publish/ep01/voice.srt",
  "loudness_lufs": -14.0,
  "segments": [
    {"name": "钩子", "from_marker": "beauty-黄昏绕圈", "to_marker": null, "speed": 1.0},
    {"name": "干活", "from_marker": "dev-开始写引力", "to_marker": "fail-第一次运行", "speed": 6.0}
  ]
}
```

- `session` 相对 `content/sessions/` 解析；`video` 恒为 `raw.mkv`。
- `from_marker`/`to_marker` 用**标签**定位；`to_marker` 为 `null` 表示直到录像结尾。
- 同一标签重复出现时，取**第一个匹配**；label 不存在则报错退出并列出可用标签。
- `speed` 为播放倍速（`1.0` 原速，`>1` 快剪）。
- `subtitles` 若存在则烧录字幕；`loudness_lufs` 用于响度归一化。
- 无 `markers.json` 时不得猜测时间点，必须报错。

### clip.py —— 切 9:16 竖版

```
python content/kit/clip.py --dir content/sessions/2026-10-20-ep01 --from-label "fail-第一次运行" --to-label "fail-翻车现场" --out content/publish/ep01/short1.mp4
python content/kit/clip.py --input long.mp4 --start 12.5 --end 40.0 --out content/publish/ep01/short2.mp4
```

- 输出规格：**1080×1920 (9:16)**，H.264 + AAC。
- 定位二选一：`--dir` + `--from-label`/`--to-label`（读 `markers.json`），或 `--input` + `--start`/`--end`（秒）。
- 画面按需求居中裁切放大（横屏源切竖版）。
- `--to-label` 省略时取下一个 marker 或源结尾。

### 各脚本已实现的可选参数

契约之外均为**附加项**，不改变默认行为：

| 脚本 | 可选参数 |
|---|---|
| `record.py` | `--fps` `--audio` `--audio-device` `--ffmpeg` `--timeout` `--log` `--verbose` |
| `transcribe.py` | `--model` `--language` `--device` `--compute-type` `--beam-size` `--no-vad` `--model-dir` `--ffmpeg` `--overwrite` `--allow-empty` |
| `voiceover.py` | `--list-voices` |
| `roughcut.py` | `--overwrite` `--sessions-root` `--ffmpeg` `--ffprobe` `--preset` `--crf` `--dry-run` |
| `clip.py` | `--overwrite` `--sessions-root` `--ffmpeg` `--ffprobe` `--preset` `--crf` `--dry-run` |

`ffmpeg` / `ffprobe` 解析顺序：`--ffmpeg` / `--ffprobe` → `FFMPEG_BIN` / `FFPROBE_BIN` → `PATH` → 常见安装位置。缺失时打印安装提示，不甩堆栈。

### 实现细节与运维注意（实测得出）

- **`record.py stop` 的优雅收尾**：`stop` 是独立进程、拿不到 ffmpeg 的 stdin，因此 `start` 会拉起一个脱离终端的守护进程（隐藏子命令 `_supervise`）由它持有 ffmpeg 的 stdin 管道；`stop` 通过 `<dir>/.stop-request` 通知它写 `'q'`，ffmpeg 才会写完 moov/cues。**录制期间不要手动杀掉 python 进程**，否则只能强杀收尾、`raw.mkv` 会不可读。
- 会话目录除 `raw.mkv` / `markers.json` 外，还会出现诊断文件 `.ffmpeg.log`、`.recording.pid`（均已 gitignore）。
- **编码容忍度**：`markers.json`、剪辑单、字幕都按 **UTF-8（带或不带 BOM 均可）** 读取。剪辑单由人在 Windows 上产出，记事本/PowerShell 默认写 BOM，故读 JSON 一律用 `utf-8-sig`。
- **禁止写进 `sessions/`**：`roughcut.py` / `clip.py` 拒绝把输出写到 `content/sessions/` 下（只增不删，防止覆盖录像）。
- **默认不覆盖**：所有产物已存在时默认报错退出，需显式 `--overwrite`。
- **烧字幕的路径转义**：Windows 盘符冒号在 ffmpeg filtergraph 里要写成 `subtitles=filename='E\:/path/to.srt'`（单引号包住 + 双反斜杠转义）；脚本已处理。
- **响度归一化**：`loudnorm` 单遍模式内部 lookahead 会让音频时间戳非单调（aac 报 `Queue input is backward in time`、成片音频比视频长 ~80ms），脚本在其后接了 `aresample=async=1:first_pts=0` 归一化时间戳。
- **语音识别的设备选择（重要）**：`transcribe.py` 的 `--device auto`（默认）会先尝试 CUDA。但 **ctranslate2 只凭显卡驱动提供的 `nvcuda.dll` 就报告 CUDA 可用**；若本机没装 CUDA Toolkit（缺 `cublas64_12.dll` 等），真正的失败会推迟到第一次计算才抛。脚本已处理：`auto` 命中该错误时**自动回退 CPU 重试**；显式 `--device cuda` 失败时会给出"请改用 `--device cpu`"的可读提示。想固定行为就显式写 `--device cpu`。
- **模型缓存**：faster-whisper 首次运行会下载模型到 `~/.cache/huggingface`（`base` 约 148 MB），需要联网；之后离线可用。
- **TTS 结果不可字节复现**：Edge 合成是远程服务，同一份稿子每次请求返回的音频字节不同（长度一致）。因此 `voice.mp3` 不能按字节复现，重新生成会让 `voice.srt` 出现微小 diff；`voiceover.py` 已对网络超时做 3 次有限重试，并把异常翻译成可读原因而不是甩堆栈。
- **性能**：`roughcut.py` 各段用 filter 级 `trim` 在原始时间轴上定位（保精度），**每个 segment 会把整段录像解码一遍**；长录像多段会明显变慢。

## 3. 铁律（脚本层面必须遵守）

- **绝不代用户发布**：脚本包里不存在任何上传/发布到平台的能力。
- **只增不删**：任何脚本都不得删除或覆盖 `sessions/` 下的 `raw.mkv`；`publish/` 已有同名产物时默认报错，需显式 `--overwrite`。
- 不改动 Godot 工程与 `README/`。

## 4. 依赖

见 `content/kit/requirements.txt`。可选项缺失时，对应脚本必须给出明确安装提示而不是崩溃堆栈。