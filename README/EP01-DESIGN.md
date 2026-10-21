# EP01 设计文档：《我让 AI 给我的小星球造了引力》

> 本文档有两用：①给本地 Agent 的开发设计（照着建场景）；②给内容流水线的拍摄/剪辑设计。
> 路线：Godot 4.7 + GDScript ｜ 平台：YouTube 长视频（主）+ Shorts/TikTok 竖版 ×2–3 + X 图文帖

---

## 一、这期讲什么

一句话：一个没做过 3D 游戏的 Web 开发者，把「小星球引力」写成设计文档交给 AI Agent 实现，全程公开翻车和修复。

标题候选（英文，发布时三选一）：
1. I Let an AI Build Gravity for My Tiny Planet
2. A Web Dev Builds a Tiny Planet He Can Walk Around (With AI)
3. Tiny Planet Gravity in Godot — Built by an AI Agent

叙事结构（照《星辰之旅》拆解的路子）：钩子画面先行 → 讲清问题 → Agent 干活 → 翻车 → 一句话点破关键 → 跑通 → 预告下期。

## 二、给 Agent 的场景设计（开发规格）

**场景**：一个可以绕着走一整圈的小星球。
- 星球：球体半径 20，带静态碰撞；表面先用纯色+简单光照，后期再美化
- 角色：CharacterBody3D + 胶囊体，能在球面上任意方向行走
- 相机：第三人称跟随，始终站在角色"头顶方向"一侧

**核心规则（引力）**：
- 每物理帧计算：`up = (角色位置 - 星球中心).normalized()`，角色的 up_direction 设为该向量
- 重力方向恒为 `-up`（永远指向球心），大小 9.8 的等效值
- 角色朝向平滑对齐 up（用四元数 slerp，禁止瞬间翻转）
- 移动方向以相机朝向为参照，投影到球面切平面

**验收标准（给 Agent 自检）**：
- [ ] 角色能绕星球走满一圈，不掉落、不翻转、不在"极点"打滑失控
- [ ] 静止时角色稳稳站在球面上，不抖动
- [ ] 60fps；`--headless` 校验无脚本错误

## 三、录制点清单（对应 record.py 的 mark 标签）

| 时点 | 标签（原样打点） | 用途 |
|---|---|---|
| 开录前先跑通一遍完整绕圈 | `hook-orbit-success` | 片头钩子画面（可后补录） |
| Agent 开始写代码 | `dev-gravity-start` | 中段快剪 |
| 第一次运行 | `fail-first-run` | 翻车点 1 |
| 角色翻转/打滑 | `fail-flip-scene` | 翻车点 2 |
| 改对 up 向量后运行 | `fix-it-works` | 高潮 |
| 日落光线下绕圈 | `beauty-sunset-orbit` | 结尾+封面候选 |

> 修订（2026-10-21）：打点标签已统一改为英文，便于英文 devlog 中命令行出镜。
> 原中文标签对照（旧标签字面已从文档移除，按「旧前缀 + 中文词」留档）：
> `hook-`+绕圈成功 → `hook-orbit-success`；`dev-`+开始写引力 → `dev-gravity-start`；
> `fail-`+第一次运行 → `fail-first-run`；`fail-`+翻车现场 → `fail-flip-scene`；
> `fix-`+跑通了 → `fix-it-works`；`beauty-`+黄昏绕圈 → `beauty-sunset-orbit`

## 四、旁白脚本（英文，约 3 分钟，voiceover.py 直接可用）

[HOOK]
This is a planet you can walk all the way around. And I didn't write most of the code — an AI agent did. I'm a web developer. I've never made a 3D game. So I gave an AI one job: build gravity that pulls you toward the center of a tiny planet, like The Little Prince. Here's what happened.

[PROBLEM]
In a normal game, down is just... down. But on a tiny planet, down is different everywhere you stand. Your "up" has to point away from the planet's center, and it changes with every single step. Get this wrong, and your character either falls off into space, or walks like he's had too much to drink.

[BUILD]
So here's the setup. Godot 4, one sphere, one character. I wrote a design doc — just a markdown file explaining exactly what I wanted — and handed it to a coding agent. Build me a planet. Make gravity point at its heart.

[FAIL]
First try: the agent gave me normal gravity... and a round planet. The character slid right off, like the whole planet was made of ice. Second try: he kept flipping upside down near the south pole. I'm not gonna lie — this part took the longest.

[FIX]
The fix turned out to be one idea. Every frame, your "up" is the direction from the planet's center to you. Rotate to match it. Gravity always points home. Once the agent got that one idea — everything clicked.

[RESULT]
And now... I can walk over the horizon. It sounds small. But this is the foundation of the entire game. Every tree, every house, every fox I add later — all of it stands on this math.

[OUTRO]
Next time, I'll ask the AI to scatter trees and rocks across the planet — without them floating in the air or sinking into the ground. Subscribe if you want to watch a web developer accidentally become a game developer.

> 配音：`python voiceover.py --script publish/ep01/narration.txt --out publish/ep01/voice`
> 如果口播改成自己录，这份稿就是提词稿，录完用 transcribe.py 出字幕。

## 五、剪辑单要点（Shanks 出正式版，这里是框架）

- 0:00–0:08 钩子：`beauty-sunset-orbit` 或 `hook-orbit-success` 先行，旁白 HOOK 同步
- 0:08–0:40 问题段：录屏+示意图（up 向量示意可用录屏标注）
- 0:40–1:40 干活段：`dev-gravity-start` 快剪（4–8 倍速），穿插代码特写
- 1:40–2:20 翻车段：两个 fail 标记原速播放，保留现场声效/静默笑点
- 2:20–2:50 修复+跑通：`fix-it-works` 原速，旁白 RESULT
- 2:50–3:10 结尾：黄昏绕圈 + 预告

**竖版切片（Shorts/TikTok 共用，各 15–30 秒）**：
1. 翻车合集：两个 fail 标记拼接，字幕「AI built my planet's gravity. Try #1:」
2. before/after 硬切：打滑 vs 绕圈成功，同一机位
3. 黄昏绕圈纯画面版（satisfying 向）

## 六、发布文案框架（发布前 Shanks 出终稿）

- YouTube：标题三选一（见上）；描述第一行放钩子句，第二段放"设计文档驱动 AI"的方法论，附下期预告
- X：一条主帖（黄昏绕圈 GIF/短视频 + 一句失败→成功对比）+ 一条 thread 讲 up 向量的一句话原理，结尾留开放问题引回复
- TikTok：用竖版切片 1，文案模板「POV: you let an AI build gravity」类钩子
