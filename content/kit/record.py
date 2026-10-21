#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""record.py —— 录制与打点（实现 content/kit/README.md 第 1、2 节契约）

用法::

    python content/kit/record.py start --dir content/sessions/2026-10-20-ep01
    python content/kit/record.py mark  --dir content/sessions/2026-10-20-ep01 --label "fix-it-works"
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
    eprint("Error: %s" % message)
    sys.exit(code)


def warn(message: str) -> None:
    eprint("Warning: %s" % message)


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
        raise ValueError("not a valid ISO 8601 timestamp: %r" % (text,))
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
        die("Cannot find %s (was this session ever started?)" % path, EXIT_STATE)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        die("%s is not valid JSON: %s" % (path, exc), EXIT_STATE)
    except OSError as exc:
        die("Failed to read %s: %s" % (path, exc), EXIT_RUNTIME)
    if not isinstance(data, dict):
        die("%s must be a top-level object" % path, EXIT_STATE)
    return data


def validate_markers(data: dict, path: str) -> dict:
    """按 README 第 1 节校验 markers.json 结构；不合法就报错退出。"""
    where = "markers.json(%s)" % path
    for key in ("session", "started_at", "stopped_at", "video", "markers"):
        if key not in data:
            die("%s is missing field %r" % (where, key), EXIT_STATE)
    if not isinstance(data["session"], str):
        die("%s: \"session\" must be a string" % where, EXIT_STATE)
    if not isinstance(data["started_at"], str):
        die("%s: \"started_at\" must be a string" % where, EXIT_STATE)
    if data["stopped_at"] is not None and not isinstance(data["stopped_at"], str):
        die("%s: \"stopped_at\" must be a string or null" % where, EXIT_STATE)
    if not isinstance(data["video"], str):
        die("%s: \"video\" must be a string" % where, EXIT_STATE)
    if data["video"] != VIDEO_NAME:
        die("%s: \"video\" must be %r (contract requirement)" % (where, VIDEO_NAME), EXIT_STATE)
    if not isinstance(data["markers"], list):
        die("%s: \"markers\" must be an array" % where, EXIT_STATE)
    try:
        parse_iso(data["started_at"])
        if data["stopped_at"] is not None:
            parse_iso(data["stopped_at"])
    except ValueError as exc:
        die("%s has an invalid timestamp: %s" % (where, exc), EXIT_STATE)
    for idx, marker in enumerate(data["markers"]):
        item = "%s, markers[%d]" % (where, idx)
        if not isinstance(marker, dict):
            die("%s must be an object" % item, EXIT_STATE)
        for key in ("label", "t", "at"):
            if key not in marker:
                die("%s is missing field %r" % (item, key), EXIT_STATE)
        if not isinstance(marker["label"], str):
            die("%s: \"label\" must be a string" % item, EXIT_STATE)
        if isinstance(marker["t"], bool) or not isinstance(marker["t"], (int, float)):
            die("%s: \"t\" must be a number" % item, EXIT_STATE)
        if not isinstance(marker["at"], str):
            die("%s: \"at\" must be a string" % item, EXIT_STATE)
        try:
            parse_iso(marker["at"])
        except ValueError as exc:
            die("%s has an invalid timestamp: %s" % (item, exc), EXIT_STATE)
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
        eprint("Note: could not send Ctrl-Break (%s); expected across consoles or process groups, falling back to terminate."
               % type(exc).__name__)
        return False


def read_pid(pid_path: str):
    try:
        with open(pid_path, "r", encoding="utf-8") as fh:
            raw = fh.read().strip()
    except OSError as exc:
        die("Failed to read %s: %s" % (pid_path, exc), EXIT_RUNTIME)
    try:
        return int(raw)
    except ValueError:
        die("%s does not contain a PID: %r (delete it manually, then run start again)" % (pid_path, raw), EXIT_STATE)


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
        "Could not find ffmpeg. Pass --ffmpeg, set the FFMPEG environment variable, "
        "or add it to PATH. Tried: %s" % ", ".join(candidates),
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
        return None, "ffprobe not found"
    try:
        result = subprocess.run(
            [ffprobe_path, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, "ffprobe failed to run: %s" % exc
    if result.returncode != 0:
        return None, (result.stderr or "").strip()[:300] or ("ffprobe exited with code %d" % result.returncode)
    text = (result.stdout or "").strip()
    if not text or text.upper() == "N/A":
        return None, "ffprobe reported no duration (the file may be incomplete or corrupt)"
    try:
        return float(text), None
    except ValueError:
        return None, "could not parse ffprobe output: %r" % text


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
        die("record.py captures the screen with ffmpeg gdigrab, which is Windows-only.", EXIT_RUNTIME)

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
                "This session is already recording (pid=%d). To finish it, run: "
                "python content/kit/record.py stop --dir %s" % (old_pid, args.dir),
                EXIT_STATE,
            )
        warn("Found a stale %s (pid=%d no longer exists); cleaning it up and continuing." % (PID_FILE, old_pid))
        try:
            os.remove(pid_path)
        except OSError as exc:
            die("Could not clean up the stale %s: %s" % (PID_FILE, exc), EXIT_RUNTIME)

    # 铁律：绝不覆盖 raw.mkv
    if os.path.exists(raw_path):
        die(
            "%s already exists. raw.mkv under sessions is never overwritten; "
            "use another session directory, or move the old recording away yourself after checking it." % raw_path,
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
                "--audio was requested but no audio input device was detected. Pass --audio-device with a device name, "
                "or drop --audio (recording works fine without one).",
                EXIT_RUNTIME,
            )

    # 清空日志（supervisor 之后以 append 方式继续写）
    try:
        with open(log_path, "w", encoding="utf-8"):
            pass
    except OSError as exc:
        die("Could not write the log file %s: %s" % (log_path, exc), EXIT_RUNTIME)

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
        die("Could not start the recording supervisor: %s" % exc, EXIT_RUNTIME)

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
        die("ffmpeg did not start before the timeout. ffmpeg log:\n%s" % (detail or "(empty)"), EXIT_RUNTIME)

    # 再确认一下不是“写回 PID 之后就立刻挂掉”
    time.sleep(0.6)
    if not _pid_alive(ffmpeg_pid):
        detail = tail_log(log_path)
        _cleanup_files(pid_path, stop_path)
        die("ffmpeg exited right after starting (pid=%d). Log:\n%s" % (ffmpeg_pid, detail or "(empty)"), EXIT_RUNTIME)

    # 确认存活后再写 markers.json（避免留下 stopped_at=null 的假录制状态）
    markers = {
        "session": os.path.basename(session_dir),
        "started_at": iso(started_at),
        "stopped_at": None,
        "video": VIDEO_NAME,
        "markers": [],
    }
    write_json(os.path.join(session_dir, "markers.json"), markers)

    print("Recording started")
    print("  session   : %s" % session_dir)
    print("  raw.mkv   : %s" % raw_path)
    print("  markers   : %s" % os.path.join(session_dir, "markers.json"))
    print("  ffmpeg    : pid=%d  %s" % (ffmpeg_pid, ffmpeg_path))
    print("  started_at: %s" % markers["started_at"])
    if args.audio:
        print("  audio     : %s" % audio_device)
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
        warn("current time is earlier than started_at (system clock moved back?); recording t as 0.0.")
        seconds = 0.0

    if data["stopped_at"] is not None:
        eprint("Note: this session already stopped at %s; the marker is still recorded relative to started_at." % data["stopped_at"])

    marker = {
        "label": args.label,
        "t": round(float(seconds), 3),
        "at": iso(mark_at),
    }
    data["markers"].append(marker)
    data["markers"].sort(key=lambda item: item["t"])   # 契约：markers 按 t 升序
    write_json(markers_path, data)

    print("Marker added: label=%s t=%.3f at=%s (%d total)"
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
            "Not recording: %s does not exist. If you are recording, check that --dir points to the right session." % pid_path,
            EXIT_STATE,
        )

    ffmpeg_pid = read_pid(pid_path)
    graceful = None

    if not _pid_alive(ffmpeg_pid):
        graceful = False
        warn("the recording process pid=%d no longer exists (it may have crashed or been killed)." % ffmpeg_pid)
    elif not _looks_like_ffmpeg(ffmpeg_pid):
        graceful = False
        warn("pid=%d is not ffmpeg (%s); refusing to kill another process, only backfilling stopped_at."
             % (ffmpeg_pid, _pid_exe(ffmpeg_pid)))
    else:
        # 1) 通知守护进程向 ffmpeg stdin 写 'q'（能触发 mkv 正常收尾）
        try:
            with open(stop_path, "w", encoding="utf-8") as fh:
                fh.write(iso(now_local()))
        except OSError as exc:
            die("Could not write %s: %s" % (stop_path, exc), EXIT_RUNTIME)

        wait_deadline = time.monotonic() + args.timeout
        while time.monotonic() < wait_deadline:
            if not _pid_alive(ffmpeg_pid):
                break
            time.sleep(0.2)

        # 2) 超时：尽力 Ctrl-Break，再 terminate / kill
        if _pid_alive(ffmpeg_pid):
            warn("ffmpeg did not respond to the stop request within %.0f s; trying Ctrl-Break." % args.timeout)
            _send_ctrl_break(ffmpeg_pid)
            ctrl_deadline = time.monotonic() + 3.0
            while time.monotonic() < ctrl_deadline and _pid_alive(ffmpeg_pid):
                time.sleep(0.2)
        if _pid_alive(ffmpeg_pid):
            warn("still running; force-terminating ffmpeg (the mkv may end up without a complete index).")
            _terminate(ffmpeg_pid)
            term_deadline = time.monotonic() + 5.0
            while time.monotonic() < term_deadline and _pid_alive(ffmpeg_pid):
                time.sleep(0.2)
        if _pid_alive(ffmpeg_pid):
            warn("terminate had no effect; killing the process.")
            _kill(ffmpeg_pid)
            time.sleep(0.5)
        graceful = not _pid_alive(ffmpeg_pid)

    # 3) 回填 stopped_at
    stopped_at = now_local()
    if not os.path.isfile(markers_path):
        _cleanup_files(pid_path, stop_path)
        die("Cannot find %s, so stopped_at cannot be backfilled (cleaned up %s)." % (markers_path, PID_FILE), EXIT_STATE)

    data = validate_markers(read_json(markers_path), markers_path)
    data["stopped_at"] = iso(stopped_at)
    write_json(markers_path, data)

    _cleanup_files(pid_path, stop_path)

    # 4) 自检：raw.mkv 是否可读、时长是否 > 0
    video_ok = True
    if os.path.exists(raw_path):
        ffprobe_path = resolve_ffprobe(resolve_ffmpeg(args.ffmpeg) if args.ffmpeg else _ffmpeg_hint(), args.ffprobe)
        if not ffprobe_path:
            duration, probe_error = None, "ffprobe not found"
            eprint("Note: no ffprobe on this system; skipping the raw.mkv duration check.")
        else:
            duration, probe_error = probe_duration(ffprobe_path, raw_path)
            if duration is None or duration <= 0:
                video_ok = False
    else:
        duration, probe_error = None, "raw.mkv does not exist"
        video_ok = False

    print("Recording stopped")
    print("  session   : %s" % session_dir)
    print("  stopped_at: %s" % data["stopped_at"])
    print("  markers   : %d" % len(data["markers"]))
    if duration is not None:
        print("  raw.mkv   : duration %.3f s" % duration)

    if not video_ok:
        warn("raw.mkv is unusable: %s" % probe_error)
        if args.verbose:
            detail = tail_log(log_path)
            if detail:
                eprint("---- end of ffmpeg log ----")
                eprint(detail)
        return EXIT_RUNTIME

    if graceful is False:
        warn("ffmpeg did not exit via the stop request; the recording may lack a complete index, so double-check raw.mkv.")
    if args.verbose:
        detail = tail_log(log_path)
        if detail:
            eprint("---- end of ffmpeg log ----")
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
            log_fh.write("[supervisor] failed to start ffmpeg: %s\n" % exc)
            log_fh.close()
        return EXIT_RUNTIME

    write_json_pid(pid_path, proc.pid)
    if log_fh:
        log_fh.write("[supervisor] ffmpeg started, pid=%d\n" % proc.pid)
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
                        log_fh.write("[supervisor] wrote 'q' to ffmpeg stdin; waiting for it to finish\n")
                        log_fh.flush()
                    # 收尾请求已发出，交给下面统一超时处理
                    deadline = time.monotonic() + max(1.0, args.timeout)
                except OSError as exc:
                    if log_fh:
                        log_fh.write("[supervisor] failed to write stdin: %s\n" % exc)
                        log_fh.flush()
                    deadline = time.monotonic()  # 立刻走强杀分支
                try:
                    os.remove(stop_path)
                except OSError:
                    pass
            if deadline is not None and time.monotonic() > deadline:
                if log_fh:
                    log_fh.write("[supervisor] stop timed out; terminating\n")
                    log_fh.flush()
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        if log_fh:
                            log_fh.write("[supervisor] terminate had no effect; killing\n")
                            log_fh.flush()
                        proc.kill()
                break
            time.sleep(0.2)
    except Exception as exc:  # noqa: BLE001 - 守护进程不能把异常留给用户
        if log_fh:
            log_fh.write("[supervisor] exception: %r\n" % (exc,))
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
            log_fh.write("[supervisor] ffmpeg exited rc=%s\n" % rc)
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
        raise argparse.ArgumentTypeError("must be a number: %r" % text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0: %r" % text)
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="record.py",
        description="Record and mark: capture the screen with ffmpeg gdigrab into <dir>/raw.mkv; markers go to <dir>/markers.json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python content/kit/record.py start --dir content/sessions/2026-10-20-ep01\n"
            "  python content/kit/record.py mark  --dir content/sessions/2026-10-20-ep01 --label \"fix-it-works\"\n"
            "  python content/kit/record.py stop  --dir content/sessions/2026-10-20-ep01\n"
        ),
    )
    sub = parser.add_subparsers(dest="command", metavar="{start,mark,stop}")

    p_start = sub.add_parser("start", help="Start recording: create the directory, launch ffmpeg, write markers.json, save .recording.pid")
    p_start.add_argument("--dir", required=True, help="session directory, e.g. content/sessions/2026-10-20-ep01")
    p_start.add_argument("--fps", type=positive_float, default=DEFAULT_FPS, help="capture frame rate (default %g)" % DEFAULT_FPS)
    p_start.add_argument("--audio", action="store_true", help="also record audio (off by default; recording works without an audio device)")
    p_start.add_argument("--audio-device", default=None, help="dshow audio device name; auto-detected when omitted")
    p_start.add_argument("--ffmpeg", default=None, help="path to the ffmpeg executable (default: FFMPEG env var / PATH / common locations)")
    p_start.add_argument("--timeout", type=positive_float, default=DEFAULT_STOP_TIMEOUT,
                         help="seconds to wait for a graceful stop before force-killing (default %g)" % DEFAULT_STOP_TIMEOUT)
    p_start.add_argument("--log", default=None, help="ffmpeg log path (default <dir>/.ffmpeg.log)")
    p_start.set_defaults(func=cmd_start)

    p_mark = sub.add_parser("mark", help="Append one marker at now - started_at (can run as a separate process)")
    p_mark.add_argument("--dir", required=True, help="session directory")
    p_mark.add_argument("--label", required=True, help="label, copied verbatim from the design doc")
    p_mark.set_defaults(func=cmd_mark)

    p_stop = sub.add_parser("stop", help="Gracefully stop ffmpeg, backfill stopped_at, remove .recording.pid")
    p_stop.add_argument("--dir", required=True, help="session directory")
    p_stop.add_argument("--timeout", type=positive_float, default=DEFAULT_STOP_TIMEOUT,
                        help="seconds to wait for the graceful stop (default %g)" % DEFAULT_STOP_TIMEOUT)
    p_stop.add_argument("--ffmpeg", default=None, help="ffmpeg path (only used to locate ffprobe for the sanity check)")
    p_stop.add_argument("--ffprobe", default=None, help="ffprobe path (only used to check the raw.mkv duration)")
    p_stop.add_argument("--verbose", action="store_true", help="also print the tail of the ffmpeg log")
    p_stop.set_defaults(func=cmd_stop)

    # 内部子命令：由 start 拉起，持有 ffmpeg stdin
    p_sup = sub.add_parser("_supervise", help="(internal) recording supervisor, launched by start; do not call manually")
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
        eprint("Interrupted.")
        sys.exit(130)