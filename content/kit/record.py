#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""record.py —— 录制与打点（实现 content/kit/README.md 第 1、2 节契约）

用法::

    python content/kit/record.py start --dir content/sessions/2026-10-20-ep01
    python content/kit/record.py mark  --dir content/sessions/2026-10-20-ep01 --label "fail-第一次运行"
    python content/kit/record.py stop  --dir content/sessions/2026-10-20-ep01

设计要点：

* ``mark`` 一定是独立进程：``t`` 由 ``markers.json`` 里的 ``started_at`` 现算
  （``(now - started_at).total_seconds()``），不依赖录制进程的内存。
* ffmpeg 写 mkv 必须收到收尾请求（'q' / Ctrl-Break）才会写完 moov/cues，直接强杀
  会得到不可用的文件。为了让**另一个进程**的 ``stop`` 能写 'q'，``start`` 会拉起一个
  脱离终端的守护进程（``_supervise``，内部子命令）由它持有 ffmpeg 的 stdin 管道；
  ``stop`` 用 ``<dir>/.stop-request`` 通知守护进程写入 'q' 并等待退出，超时才强杀。
* 铁律：绝不删除/覆盖会话目录下的 ``raw.mkv``；``raw.mkv`` 已存在时 ``start`` 直接报错退出。
"""

from __future__ import annotations

import argparse
import ctypes
import datetime
import json
import os
import shutil
import signal
import subprocess
import sys
import time

# ---------------------------------------------------------------- 常量 / 工具

VIDEO_NAME = "raw.mkv"          # 契约：video 恒为 raw.mkv
PID_FILE = ".recording.pid"     # ffmpeg PID
STOP_FILE = ".stop-request"     # stop -> 守护进程 的收尾请求
LOG_FILE = ".ffmpeg.log"        # ffmpeg stderr/stdout 日志（诊断用）

DEFAULT_FPS = 30.0
DEFAULT_STOP_TIMEOUT = 10.0     # 优雅收尾等待秒数（契约：先正常收尾，超时再强杀）

# Windows 进程创建标志
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000

# ctypes 常量
PROCESS_TERMINATE = 0x0001
SYNCHRONIZE = 0x00100000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
WAIT_TIMEOUT = 0x00000102

FFMPEG_FALLBACKS = [
    r"E:\ffmpeg\bin\ffmpeg.exe",
    r"C:\ffmpeg\bin\ffmpeg.exe",
    r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
]

EXIT_USAGE = 2
EXIT_STATE = 3      # 状态冲突（已在录制 / 未在录制 / 文件已存在）
EXIT_RUNTIME = 1    # 运行期失败


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
    """打印可读原因并以非 0 退出。"""
    eprint("错误: %s" % message)
    sys.exit(code)


def warn(message: str) -> None:
    eprint("警告: %s" % message)


# ---------------------------------------------------------------- 时间 / JSON

def now_local() -> datetime.datetime:
    """本机时区的当前时刻（带 tzinfo）。"""
    return datetime.datetime.now().astimezone()


def iso(dt: datetime.datetime) -> str:
    """契约要求：ISO 8601 带时区偏移，例 2026-10-20T21:30:00+08:00。"""
    return dt.isoformat(timespec="seconds")


def parse_iso(text: str) -> datetime.datetime:
    try:
        dt = datetime.datetime.fromisoformat(text)
    except ValueError:
        raise ValueError("时间戳不是合法 ISO 8601: %r" % (text,))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=now_local().tzinfo)
    return dt


def write_json(path: str, data: dict) -> None:
    """UTF-8 + ensure_ascii=False + indent=2，原子替换写入。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def read_json(path: str) -> dict:
    if not os.path.isfile(path):
        die("找不到 %s（该会话还没 start 过？）" % path, EXIT_STATE)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        die("%s 不是合法 JSON: %s" % (path, exc), EXIT_STATE)
    except OSError as exc:
        die("读取 %s 失败: %s" % (path, exc), EXIT_RUNTIME)
    if not isinstance(data, dict):
        die("%s 顶层必须是 object" % path, EXIT_STATE)
    return data


def validate_markers(data: dict, path: str) -> dict:
    """按 README 第 1 节校验 markers.json 结构；不合法就报错退出。"""
    where = "markers.json(%s)" % path
    for key in ("session", "started_at", "stopped_at", "video", "markers"):
        if key not in data:
            die("%s 缺少字段 %r" % (where, key), EXIT_STATE)
    if not isinstance(data["session"], str):
        die("%s 的 session 必须是 string" % where, EXIT_STATE)
    if not isinstance(data["started_at"], str):
        die("%s 的 started_at 必须是 string" % where, EXIT_STATE)
    if data["stopped_at"] is not None and not isinstance(data["stopped_at"], str):
        die("%s 的 stopped_at 必须是 string 或 null" % where, EXIT_STATE)
    if not isinstance(data["video"], str):
        die("%s 的 video 必须是 string" % where, EXIT_STATE)
    if data["video"] != VIDEO_NAME:
        die("%s 的 video 必须是 %r（契约硬约束）" % (where, VIDEO_NAME), EXIT_STATE)
    if not isinstance(data["markers"], list):
        die("%s 的 markers 必须是 array" % where, EXIT_STATE)
    try:
        parse_iso(data["started_at"])
        if data["stopped_at"] is not None:
            parse_iso(data["stopped_at"])
    except ValueError as exc:
        die("%s 时间格式错误: %s" % (where, exc), EXIT_STATE)
    for idx, marker in enumerate(data["markers"]):
        item = "%s 的 markers[%d]" % (where, idx)
        if not isinstance(marker, dict):
            die("%s 必须是 object" % item, EXIT_STATE)
        for key in ("label", "t", "at"):
            if key not in marker:
                die("%s 缺少字段 %r" % (item, key), EXIT_STATE)
        if not isinstance(marker["label"], str):
            die("%s 的 label 必须是 string" % item, EXIT_STATE)
        if isinstance(marker["t"], bool) or not isinstance(marker["t"], (int, float)):
            die("%s 的 t 必须是 number" % item, EXIT_STATE)
        if not isinstance(marker["at"], str):
            die("%s 的 at 必须是 string" % item, EXIT_STATE)
        try:
            parse_iso(marker["at"])
        except ValueError as exc:
            die("%s 时间格式错误: %s" % (item, exc), EXIT_STATE)
    return data


# ---------------------------------------------------------------- 进程工具

def _pid_alive(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
    finally:
        kernel32.CloseHandle(handle)


def _pid_exe(pid: int):
    """返回进程可执行文件全路径；无法查询时返回 None（不猜测）。"""
    if os.name != "nt":
        return None
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        size = ctypes.c_uint(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return None
    finally:
        kernel32.CloseHandle(handle)


def _looks_like_ffmpeg(pid: int) -> bool:
    exe = _pid_exe(pid)
    if not exe:
        return True  # 查不到就按“是”处理（保守：不误杀，也不误判为空闲）
    return "ffmpeg" in os.path.basename(exe).lower()


def _terminate(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, signal.SIGTERM)
            return True
        except OSError:
            return False
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if not handle:
        return False
    try:
        return bool(kernel32.TerminateProcess(handle, 1))
    finally:
        kernel32.CloseHandle(handle)


def _kill(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, signal.SIGKILL)
            return True
        except OSError:
            return False
    return _terminate(pid)


def _send_ctrl_break(pid: int) -> bool:
    """尽力而为：向 ffmpeg 所在进程组发 Ctrl-Break（ffmpeg 会走 sigterm_handler）。"""
    if os.name != "nt":
        return False
    try:
        os.kill(pid, signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
        return True
    except Exception as exc:  # noqa: BLE001 - 跨控制台时必然失败，属预期
        eprint("提示: 发送 Ctrl-Break 失败（%s）；跨控制台/跨进程组时属预期，将改用 terminate。"
               % type(exc).__name__)
        return False


def read_pid(pid_path: str):
    try:
        with open(pid_path, "r", encoding="utf-8") as fh:
            raw = fh.read().strip()
    except OSError as exc:
        die("读取 %s 失败: %s" % (pid_path, exc), EXIT_RUNTIME)
    try:
        return int(raw)
    except ValueError:
        die("%s 内容不是 PID: %r（可手动删除后重新 start）" % (pid_path, raw), EXIT_STATE)


# ---------------------------------------------------------------- ffmpeg

def resolve_ffmpeg(explicit=None) -> str:
    """--ffmpeg > 环境变量 FFMPEG / FFMPEG_BIN > PATH > 常见安装位置。"""
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
    die(
        "找不到 ffmpeg。请用 --ffmpeg 指定，或设置环境变量 FFMPEG，"
        "或把它加入 PATH。已尝试: %s" % ", ".join(candidates),
        EXIT_RUNTIME,
    )


def resolve_ffprobe(ffmpeg_path: str, explicit=None):
    """返回 ffprobe 路径，找不到返回 None（只用于验证，不阻塞录制）。"""
    candidates = []
    if explicit:
        candidates.append(explicit)
    for env_name in ("FFPROBE", "FFPROBE_BIN"):
        value = os.environ.get(env_name)
        if value:
            candidates.append(value)
    found = shutil.which("ffprobe")
    if found:
        candidates.append(found)
    if ffmpeg_path:
        sibling = os.path.join(os.path.dirname(ffmpeg_path), "ffprobe.exe" if os.name == "nt" else "ffprobe")
        candidates.append(sibling)
    for fallback in FFMPEG_FALLBACKS:
        candidates.append(os.path.join(os.path.dirname(fallback), "ffprobe.exe" if os.name == "nt" else "ffprobe"))
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return os.path.abspath(candidate)
    return None


def probe_duration(ffprobe_path, video_path: str):
    """用 ffprobe 读容器时长（秒）；失败返回 (None, 原因)。"""
    if not ffprobe_path:
        return None, "找不到 ffprobe"
    try:
        result = subprocess.run(
            [ffprobe_path, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, "ffprobe 执行失败: %s" % exc
    if result.returncode != 0:
        return None, (result.stderr or "").strip()[:300] or ("ffprobe 退出码 %d" % result.returncode)
    text = (result.stdout or "").strip()
    if not text or text.upper() == "N/A":
        return None, "ffprobe 没给出时长（文件可能不完整/损坏）"
    try:
        return float(text), None
    except ValueError:
        return None, "ffprobe 输出无法解析: %r" % text


def detect_audio_device(ffmpeg_path: str):
    """用 dshow 设备列表猜第一个音频输入设备；猜不到返回 None。"""
    try:
        result = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = (result.stderr or "") + (result.stdout or "")
    for line in output.splitlines():
        if "(audio)" not in line:
            continue
        start = line.find('"')
        end = line.find('"', start + 1)
        if start != -1 and end > start:
            return line[start + 1:end]
    return None


def build_ffmpeg_cmd(ffmpeg_path: str, fps: float, video_out: str,
                     audio: bool = False, audio_device=None) -> list:
    fps_text = ("%g" % fps)
    cmd = [
        ffmpeg_path, "-hide_banner", "-loglevel", "warning", "-nostats",
        "-n",                                  # 绝不覆盖已有文件
        "-f", "gdigrab", "-framerate", fps_text, "-i", "desktop",
    ]
    if audio:
        cmd += ["-f", "dshow", "-i", "audio=%s" % audio_device]
    cmd += [
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-g", "%d" % max(1, int(round(fps * 2))),
    ]
    if audio:
        cmd += ["-c:a", "aac", "-b:a", "160k"]
    cmd.append(video_out)
    return cmd


def tail_log(log_path: str, lines: int = 12) -> str:
    if not log_path or not os.path.isfile(log_path):
        return ""
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
            data = fh.read().splitlines()
    except OSError:
        return ""
    return "\n".join(data[-lines:])


# ---------------------------------------------------------------- start

def cmd_start(args) -> int:
    if os.name != "nt":
        die("record.py 的抓屏使用 ffmpeg gdigrab，目前仅支持 Windows。", EXIT_RUNTIME)

    session_dir = os.path.abspath(args.dir)
    raw_path = os.path.join(session_dir, VIDEO_NAME)
    pid_path = os.path.join(session_dir, PID_FILE)
    stop_path = os.path.join(session_dir, STOP_FILE)
    log_path = os.path.abspath(args.log) if args.log else os.path.join(session_dir, LOG_FILE)

    os.makedirs(session_dir, exist_ok=True)

    # 已在录制中？（先判状态，报错信息才准确）
    if os.path.exists(pid_path):
        old_pid = read_pid(pid_path)
        if _pid_alive(old_pid):
            die(
                "该会话已在录制中（pid=%d）。要结束请先执行: "
                "python content/kit/record.py stop --dir %s" % (old_pid, args.dir),
                EXIT_STATE,
            )
        warn("发现过期的 %s（pid=%d 已不存在），清理后继续。" % (PID_FILE, old_pid))
        try:
            os.remove(pid_path)
        except OSError as exc:
            die("无法清理过期的 %s: %s" % (PID_FILE, exc), EXIT_RUNTIME)

    # 铁律：绝不覆盖 raw.mkv
    if os.path.exists(raw_path):
        die(
            "%s 已存在。绝不覆盖 sessions 下的 raw.mkv；"
            "请换会话目录，或先由你自己确认并移走旧录像。" % raw_path,
            EXIT_STATE,
        )

    if os.path.exists(stop_path):
        try:
            os.remove(stop_path)
        except OSError:
            pass

    ffmpeg_path = resolve_ffmpeg(args.ffmpeg)

    audio_device = args.audio_device
    if args.audio and not audio_device:
        audio_device = detect_audio_device(ffmpeg_path)
        if not audio_device:
            die(
                "--audio 已开启但没检测到音频输入设备。请用 --audio-device 指定设备名，"
                "或去掉 --audio（无音频设备时默认可正常录制）。",
                EXIT_RUNTIME,
            )

    # 清空日志（supervisor 之后以 append 方式继续写）
    try:
        with open(log_path, "w", encoding="utf-8"):
            pass
    except OSError as exc:
        die("无法写入日志文件 %s: %s" % (log_path, exc), EXIT_RUNTIME)

    started_at = now_local()   # 开录时刻：就在拉起 ffmpeg 之前

    supervisor_cmd = [
        sys.executable, os.path.abspath(__file__), "_supervise",
        "--dir", session_dir,
        "--ffmpeg", ffmpeg_path,
        "--fps", "%g" % args.fps,
        "--log", log_path,
        "--timeout", "%g" % args.timeout,
    ]
    if args.audio:
        supervisor_cmd += ["--audio", "--audio-device", str(audio_device)]

    creationflags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    try:
        with open(os.devnull, "wb") as devnull:
            supervisor = subprocess.Popen(
                supervisor_cmd,
                stdin=devnull, stdout=devnull, stderr=devnull,
                creationflags=creationflags, close_fds=True, cwd=session_dir,
            )
    except OSError as exc:
        die("无法启动录制守护进程: %s" % exc, EXIT_RUNTIME)

    # 等守护进程把 ffmpeg 拉起来并写回 PID
    deadline = time.monotonic() + max(10.0, args.timeout)
    ffmpeg_pid = None
    while time.monotonic() < deadline:
        if os.path.exists(pid_path):
            ffmpeg_pid = read_pid(pid_path)
            if _pid_alive(ffmpeg_pid):
                break
            ffmpeg_pid = None
        if supervisor.poll() is not None:
            break                              # 守护进程已退出，不可能再拉起 ffmpeg：快速失败
        time.sleep(0.1)

    if ffmpeg_pid is None:
        detail = tail_log(log_path)
        # supervisor.pid 是我们自己拉起的守护进程，不受 _looks_like_ffmpeg 约束
        if _pid_alive(supervisor.pid):
            _terminate(supervisor.pid)
        if os.path.exists(pid_path):           # 守护进程可能已写回 PID 但进程已死
            _terminate_if_alive(read_pid(pid_path))
            _cleanup_files(pid_path)
        die("ffmpeg 未能在超时内启动。ffmpeg 日志:\n%s" % (detail or "(空)"), EXIT_RUNTIME)

    # 再确认一下不是“写回 PID 之后就立刻挂掉”
    time.sleep(0.6)
    if not _pid_alive(ffmpeg_pid):
        detail = tail_log(log_path)
        _cleanup_files(pid_path, stop_path)
        die("ffmpeg 启动后立刻退出（pid=%d）。日志:\n%s" % (ffmpeg_pid, detail or "(空)"), EXIT_RUNTIME)

    # 确认存活后再写 markers.json（避免留下 stopped_at=null 的假录制状态）
    markers = {
        "session": os.path.basename(session_dir),
        "started_at": iso(started_at),
        "stopped_at": None,
        "video": VIDEO_NAME,
        "markers": [],
    }
    write_json(os.path.join(session_dir, "markers.json"), markers)

    print("已开始录制")
    print("  会话目录 : %s" % session_dir)
    print("  raw.mkv  : %s" % raw_path)
    print("  markers  : %s" % os.path.join(session_dir, "markers.json"))
    print("  ffmpeg   : pid=%d  %s" % (ffmpeg_pid, ffmpeg_path))
    print("  started_at: %s" % markers["started_at"])
    if args.audio:
        print("  音频     : %s" % audio_device)
    return 0


def _terminate_if_alive(pid: int) -> None:
    if pid and _pid_alive(pid) and _looks_like_ffmpeg(pid):
        _terminate(pid)


def _cleanup_files(*paths) -> None:
    for path in paths:
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except OSError:
            pass


# ---------------------------------------------------------------- mark

def cmd_mark(args) -> int:
    session_dir = os.path.abspath(args.dir)
    markers_path = os.path.join(session_dir, "markers.json")
    data = validate_markers(read_json(markers_path), markers_path)

    started_at = parse_iso(data["started_at"])
    mark_at = now_local()
    seconds = (mark_at - started_at).total_seconds()
    if seconds < 0:
        warn("当前时间早于 started_at（系统时钟回拨？），t 记为 0.0。")
        seconds = 0.0

    if data["stopped_at"] is not None:
        eprint("提示: 该会话已在 %s 停录；仍按 started_at 记录这条打点。" % data["stopped_at"])

    marker = {
        "label": args.label,
        "t": round(float(seconds), 3),
        "at": iso(mark_at),
    }
    data["markers"].append(marker)
    data["markers"].sort(key=lambda item: item["t"])   # 契约：markers 按 t 升序
    write_json(markers_path, data)

    print("已打点: label=%s t=%.3f at=%s（共 %d 条）"
          % (marker["label"], marker["t"], marker["at"], len(data["markers"])))
    return 0


# ---------------------------------------------------------------- stop

def cmd_stop(args) -> int:
    session_dir = os.path.abspath(args.dir)
    raw_path = os.path.join(session_dir, VIDEO_NAME)
    pid_path = os.path.join(session_dir, PID_FILE)
    stop_path = os.path.join(session_dir, STOP_FILE)
    markers_path = os.path.join(session_dir, "markers.json")
    log_path = os.path.join(session_dir, LOG_FILE)

    if not os.path.exists(pid_path):
        die(
            "未在录制中：%s 不存在。若确实在录，请检查是否指定了正确的 --dir。" % pid_path,
            EXIT_STATE,
        )

    ffmpeg_pid = read_pid(pid_path)
    graceful = None

    if not _pid_alive(ffmpeg_pid):
        graceful = False
        warn("录制进程 pid=%d 已不存在（可能异常退出或已被结束）。" % ffmpeg_pid)
    elif not _looks_like_ffmpeg(ffmpeg_pid):
        graceful = False
        warn("pid=%d 不是 ffmpeg（%s），拒绝结束别的进程；只回填 stopped_at。"
             % (ffmpeg_pid, _pid_exe(ffmpeg_pid)))
    else:
        # 1) 通知守护进程向 ffmpeg stdin 写 'q'（能触发 mkv 正常收尾）
        try:
            with open(stop_path, "w", encoding="utf-8") as fh:
                fh.write(iso(now_local()))
        except OSError as exc:
            die("无法写入 %s: %s" % (stop_path, exc), EXIT_RUNTIME)

        wait_deadline = time.monotonic() + args.timeout
        while time.monotonic() < wait_deadline:
            if not _pid_alive(ffmpeg_pid):
                break
            time.sleep(0.2)

        # 2) 超时：尽力 Ctrl-Break，再 terminate / kill
        if _pid_alive(ffmpeg_pid):
            warn("ffmpeg 未在 %.0f 秒内响应收尾请求，尝试 Ctrl-Break。" % args.timeout)
            _send_ctrl_break(ffmpeg_pid)
            ctrl_deadline = time.monotonic() + 3.0
            while time.monotonic() < ctrl_deadline and _pid_alive(ffmpeg_pid):
                time.sleep(0.2)
        if _pid_alive(ffmpeg_pid):
            warn("仍在运行，强制结束 ffmpeg（此时 mkv 可能缺少完整索引）。")
            _terminate(ffmpeg_pid)
            term_deadline = time.monotonic() + 5.0
            while time.monotonic() < term_deadline and _pid_alive(ffmpeg_pid):
                time.sleep(0.2)
        if _pid_alive(ffmpeg_pid):
            warn("terminate 无效，kill。")
            _kill(ffmpeg_pid)
            time.sleep(0.5)
        graceful = not _pid_alive(ffmpeg_pid)

    # 3) 回填 stopped_at
    stopped_at = now_local()
    if not os.path.isfile(markers_path):
        _cleanup_files(pid_path, stop_path)
        die("找不到 %s，无法回填 stopped_at（已清理 %s）。" % (markers_path, PID_FILE), EXIT_STATE)

    data = validate_markers(read_json(markers_path), markers_path)
    data["stopped_at"] = iso(stopped_at)
    write_json(markers_path, data)

    _cleanup_files(pid_path, stop_path)

    # 4) 自检：raw.mkv 是否可读、时长是否 > 0
    video_ok = True
    if os.path.exists(raw_path):
        ffprobe_path = resolve_ffprobe(resolve_ffmpeg(args.ffmpeg) if args.ffmpeg else _ffmpeg_hint(), args.ffprobe)
        if not ffprobe_path:
            duration, probe_error = None, "找不到 ffprobe"
            eprint("提示: 系统里没有 ffprobe，跳过 raw.mkv 时长自检。")
        else:
            duration, probe_error = probe_duration(ffprobe_path, raw_path)
            if duration is None or duration <= 0:
                video_ok = False
    else:
        duration, probe_error = None, "raw.mkv 不存在"
        video_ok = False

    print("已停止录制")
    print("  会话目录  : %s" % session_dir)
    print("  stopped_at: %s" % data["stopped_at"])
    print("  markers   : %d 条" % len(data["markers"]))
    if duration is not None:
        print("  raw.mkv   : 时长 %.3f 秒" % duration)

    if not video_ok:
        warn("raw.mkv 不可用: %s" % probe_error)
        if args.verbose:
            detail = tail_log(log_path)
            if detail:
                eprint("---- ffmpeg 日志尾部 ----")
                eprint(detail)
        return EXIT_RUNTIME

    if graceful is False:
        warn("ffmpeg 不是通过收尾请求结束的，录像可能缺少完整索引；建议复核 raw.mkv。")
    if args.verbose:
        detail = tail_log(log_path)
        if detail:
            eprint("---- ffmpeg 日志尾部 ----")
            eprint(detail)
    return 0


def _ffmpeg_hint():
    """stop 没拿到 --ffmpeg 时，用环境变量 / PATH / 常见位置兜底找 ffmpeg（找不到返回 None）。"""
    for name in ("FFMPEG", "FFMPEG_BIN"):
        value = os.environ.get(name)
        if value and os.path.isfile(value):
            return value
    found = shutil.which("ffmpeg")
    if found:
        return found
    for candidate in FFMPEG_FALLBACKS:
        if os.path.isfile(candidate):
            return candidate
    return None


# ---------------------------------------------------------------- _supervise（内部）

def cmd_supervise(args) -> int:
    """持有 ffmpeg 的 stdin，收到 <dir>/.stop-request 就写 'q' 让它正常收尾。

    由 start 以脱离终端的方式拉起，用户不需要手动调用。
    """
    session_dir = os.path.abspath(args.dir)
    raw_path = os.path.join(session_dir, VIDEO_NAME)
    pid_path = os.path.join(session_dir, PID_FILE)
    stop_path = os.path.join(session_dir, STOP_FILE)
    log_path = os.path.abspath(args.log) if args.log else os.path.join(session_dir, LOG_FILE)

    cmd = build_ffmpeg_cmd(
        args.ffmpeg, args.fps, raw_path,
        audio=bool(args.audio), audio_device=args.audio_device,
    )

    creationflags = CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        log_fh = open(log_path, "a", encoding="utf-8", errors="replace")
    except OSError:
        log_fh = None
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE,
            stdout=(log_fh or subprocess.DEVNULL), stderr=(log_fh or subprocess.DEVNULL),
            creationflags=creationflags, close_fds=True, cwd=session_dir,
        )
    except OSError as exc:
        if log_fh:
            log_fh.write("[supervisor] 启动 ffmpeg 失败: %s\n" % exc)
            log_fh.close()
        return EXIT_RUNTIME

    write_json_pid(pid_path, proc.pid)
    if log_fh:
        log_fh.write("[supervisor] ffmpeg pid=%d 启动\n" % proc.pid)
        log_fh.flush()

    deadline = None
    try:
        while True:
            if proc.poll() is not None:
                break
            if deadline is None and os.path.exists(stop_path):
                try:
                    if proc.stdin:
                        proc.stdin.write(b"q")
                        proc.stdin.flush()
                    if log_fh:
                        log_fh.write("[supervisor] 已向 ffmpeg stdin 写入 'q'，等待收尾\n")
                        log_fh.flush()
                    # 收尾请求已发出，交给下面统一超时处理
                    deadline = time.monotonic() + max(1.0, args.timeout)
                except OSError as exc:
                    if log_fh:
                        log_fh.write("[supervisor] 写 stdin 失败: %s\n" % exc)
                        log_fh.flush()
                    deadline = time.monotonic()  # 立刻走强杀分支
                try:
                    os.remove(stop_path)
                except OSError:
                    pass
            if deadline is not None and time.monotonic() > deadline:
                if log_fh:
                    log_fh.write("[supervisor] 收尾超时，terminate\n")
                    log_fh.flush()
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        if log_fh:
                            log_fh.write("[supervisor] terminate 无效，kill\n")
                            log_fh.flush()
                        proc.kill()
                break
            time.sleep(0.2)
    except Exception as exc:  # noqa: BLE001 - 守护进程不能把异常留给用户
        if log_fh:
            log_fh.write("[supervisor] 异常: %r\n" % (exc,))
            log_fh.flush()
    finally:
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        rc = proc.poll()
        if rc is None:
            try:
                rc = proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                rc = proc.poll()
        if log_fh:
            log_fh.write("[supervisor] ffmpeg 退出 rc=%s\n" % rc)
            log_fh.flush()
            log_fh.close()
    return 0


def write_json_pid(pid_path: str, pid: int) -> None:
    tmp = pid_path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("%d\n" % pid)
    os.replace(tmp, pid_path)


# ---------------------------------------------------------------- CLI

def positive_float(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError("必须是数字: %r" % text)
    if value <= 0:
        raise argparse.ArgumentTypeError("必须大于 0: %r" % text)
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="record.py",
        description="录制与打点：ffmpeg gdigrab 抓屏 -> <dir>/raw.mkv，打点写入 <dir>/markers.json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python content/kit/record.py start --dir content/sessions/2026-10-20-ep01\n"
            "  python content/kit/record.py mark  --dir content/sessions/2026-10-20-ep01 --label \"fail-第一次运行\"\n"
            "  python content/kit/record.py stop  --dir content/sessions/2026-10-20-ep01\n"
        ),
    )
    sub = parser.add_subparsers(dest="command", metavar="{start,mark,stop}")

    p_start = sub.add_parser("start", help="开始录制（建目录、起 ffmpeg、写 markers.json、存 .recording.pid）")
    p_start.add_argument("--dir", required=True, help="会话目录，例 content/sessions/2026-10-20-ep01")
    p_start.add_argument("--fps", type=positive_float, default=DEFAULT_FPS, help="抓屏帧率（默认 %g）" % DEFAULT_FPS)
    p_start.add_argument("--audio", action="store_true", help="同时录制音频（默认关；无音频设备时也能正常录制）")
    p_start.add_argument("--audio-device", default=None, help="dshow 音频设备名，缺省时自动探测")
    p_start.add_argument("--ffmpeg", default=None, help="ffmpeg 可执行文件路径（缺省: 环境变量 FFMPEG / PATH / 常见位置）")
    p_start.add_argument("--timeout", type=positive_float, default=DEFAULT_STOP_TIMEOUT,
                         help="优雅收尾等待秒数，超时才强杀（默认 %g）" % DEFAULT_STOP_TIMEOUT)
    p_start.add_argument("--log", default=None, help="ffmpeg 日志路径（默认 <dir>/.ffmpeg.log）")
    p_start.set_defaults(func=cmd_start)

    p_mark = sub.add_parser("mark", help="按 now - started_at 追加一条 marker（可独立进程调用）")
    p_mark.add_argument("--dir", required=True, help="会话目录")
    p_mark.add_argument("--label", required=True, help="标签，原样照抄设计文档（可含中文）")
    p_mark.set_defaults(func=cmd_mark)

    p_stop = sub.add_parser("stop", help="优雅结束 ffmpeg、回填 stopped_at、删除 .recording.pid")
    p_stop.add_argument("--dir", required=True, help="会话目录")
    p_stop.add_argument("--timeout", type=positive_float, default=DEFAULT_STOP_TIMEOUT,
                        help="等待优雅收尾的秒数（默认 %g）" % DEFAULT_STOP_TIMEOUT)
    p_stop.add_argument("--ffmpeg", default=None, help="ffmpeg 路径（仅用于找 ffprobe 做自检）")
    p_stop.add_argument("--ffprobe", default=None, help="ffprobe 路径（仅用于自检 raw.mkv 时长）")
    p_stop.add_argument("--verbose", action="store_true", help="额外打印 ffmpeg 日志尾部")
    p_stop.set_defaults(func=cmd_stop)

    # 内部子命令：由 start 拉起，持有 ffmpeg stdin
    p_sup = sub.add_parser("_supervise", help="(内部) 录制守护进程，由 start 自动拉起，勿手动调用")
    p_sup.add_argument("--dir", required=True)
    p_sup.add_argument("--ffmpeg", required=True)
    p_sup.add_argument("--fps", type=positive_float, default=DEFAULT_FPS)
    p_sup.add_argument("--audio", action="store_true")
    p_sup.add_argument("--audio-device", default=None)
    p_sup.add_argument("--log", default=None)
    p_sup.add_argument("--timeout", type=positive_float, default=DEFAULT_STOP_TIMEOUT)
    p_sup.set_defaults(func=cmd_supervise)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help(sys.stderr)
        return EXIT_USAGE
    return args.func(args)


if __name__ == "__main__":
    _force_utf8_stdio()
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        eprint("已中断。")
        sys.exit(130)