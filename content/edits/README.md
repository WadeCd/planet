# content/edits —— 剪辑单

剪辑单由 Shanks 出**正式版**；本目录里的 `ep01.json` 是照 `README/EP01-DESIGN.md` 第五节
「剪辑单要点」整理的**草稿**，用于打通 `roughcut.py` 的输入链路。

## 使用前必须替换

- `session`：改成真实的会话目录名（`content/sessions/` 下的实际目录）。
  草稿里写的是 `2026-10-20-ep01`，**录制完成前这个值一定是错的**。

## 字段

严格遵循 `content/kit/README.md` 第 2 节的 roughcut 契约：
`episode` / `session` / `subtitles` / `loudness_lufs` / `segments[]`，
其中每个 segment 是 `{name, from_marker, to_marker, speed}`。

`from_marker` / `to_marker` 用的是 `markers.json` 里的**标签**，不是秒数。

## 已知差异（草稿 vs 设计文档）

设计文档第五节的时间轴里，**0:08–0:40 的「问题段」是录屏 + 示意图，不是游戏录像**，
所以它没有对应的 marker，本剪辑单里不包含这一段。
这一段需要在粗剪之后由人工/后续步骤插入，或用同期录屏素材单独合成。

## EP01 的 marker 标签

设计文档第三节约定、`record.py mark` 时**原样照抄**的标签（顺序即时间顺序）：

| 标签 | 用途 |
|---|---|
| `hook-绕圈成功` | 片头钩子画面 |
| `dev-开始写引力` | 中段快剪 |
| `fail-第一次运行` | 翻车点 1 |
| `fail-翻车现场` | 翻车点 2 |
| `fix-跑通了` | 高潮 |
| `beauty-黄昏绕圈` | 结尾 + 封面候选 |