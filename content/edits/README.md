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
| `hook-orbit-success` | 片头钩子画面 |
| `dev-gravity-start` | 中段代码讲解（原"写代码快剪"，已改为讲解已完成代码） |
| ~~`fail-first-run`~~ | **已取消**（2026-10-21 拍板取消翻车段，不再打点） |
| ~~`fail-flip-scene`~~ | **已取消**（同上） |
| `fix-it-works` | 高潮 |
| `beauty-sunset-orbit` | 结尾 + 封面候选 |

> EP01 实际会打点的只有 4 个：`hook-orbit-success` / `dev-gravity-start` /
> `fix-it-works` / `beauty-sunset-orbit`，对应 `ep01.json` 里的 4 个 segment。
> 翻车段取消的详情见 `README/EP01-DESIGN.md` 第三节的修订注记。