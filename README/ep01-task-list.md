# 第一期任务单（⚠️ 已废弃 · 2026-10-21）

> **本文档已废弃，仅作历史存档，不要照此执行。** 它是开工前的草稿，多处与实际仓库不一致（星球半径、工程名、路径、标签体系、OBS 说法等均已过时）。
> 当前唯一事实来源：`phase1-kit/EP01-DESIGN.md`（设计与旁白）+ 仓库 `content/kit/README.md`（流水线契约）。

---

# 第一期任务单（草稿 · 环境验收通过后交付）

> 状态：草稿。主题沿用已向用户建议的「小星球引力」（用户未反对；若用户另选题，整单替换主题即可，结构不变）。
> 交付时机：用户在电脑上跑通 phase1-kit 五项验收后，由 Shanks 把这份任务单交给本地 agent 开工。
> 配套：`phase1-kit/PLAN.md`（流水线）、`phase1-kit/edits.example.json`（剪辑单格式）。

## 本期目标（一句话）

本地 agent 在 Godot 4.7 里做出**可在小星球表面行走跳跃的角色控制器**，全程录屏打点，产出第一期发布包（长视频 1 支＋竖版 2–3 条＋文案）。

范围铁律（一期一个功能点）：只做"球面引力＋行走"，不做星球美术、不做剧情、不做 UI。

---

## 给本地 agent 的开发任务单

### 0. 工程准备
- 新建 Godot 4.7 3D 项目 `tiny-planet`（git 初始化）。
- 场景结构：`Planet`（球体，半径 10，StaticBody3D + CollisionShape3D 球形）＋ `Player`（CharacterBody3D，胶囊体 + CollisionShape3D）。
- 相机：第三人称跟随（SpringArm3D 或代码跟随，保证角色始终在画面中心）。

### 1. 核心功能：球面引力角色控制器
按以下 5 步实现（逻辑已在《小星球引力》研究信中验证过思路，Godot 4.4 社区代码改编，4.7 API 未变，实测确认）：

```gdscript
extends CharacterBody3D

@export var planet: Node3D   # 编辑器里把星球节点拖进来
const GRAVITY := 9.8
const SPEED := 5.0
const JUMP := 4.5

func _physics_process(delta: float) -> void:
    # 1) 每帧重算"下"：从角色指向星球中心
    var gravity_direction: Vector3 = (planet.global_position - global_position).normalized()
    # 2) 上 = 引力的反方向（否则 is_on_floor() 按旧上方向判定）
    up_direction = -gravity_direction
    # 3) 自己施加引力（星球引力是常量，不用项目默认重力）
    if not is_on_floor():
        velocity += gravity_direction * GRAVITY * delta
    # 4) 移动：WASD 相对相机方向在切平面上走；跳跃沿 -gravity_direction
    var input_dir := Input.get_vector("move_left", "move_right", "move_forward", "move_back")
    # （把 input_dir 投影到与 gravity_direction 垂直的平面，略）
    if Input.is_action_just_pressed("jump") and is_on_floor():
        velocity -= gravity_direction * JUMP
    move_and_slide()
    # 5) 身体"立正"：四元数 slerp 把角色 Y 轴平滑转向上（防万向节锁）
```

要点：`velocity.y` 写法全部禁用；跳跃/移动一律相对 `gravity_direction`。

### 2. 本期验收（agent 自测，录屏为证）
- [ ] 角色出生站在球顶，不掉下去、不抖动
- [ ] WASD 能绕球走一整圈（走到球底时头朝下、画面不翻转穿帮）
- [ ] 空格跳得起来、落得回去
- [ ] 相机全程跟随，无剧烈抖动

### 3. 录制配合（agent 在开发时执行，在 kit/ 目录下运行）
```powershell
python record.py start --dir sessions/ep01          # 开工前
python record.py mark --dir sessions/ep01 --label "开场-今日目标"        # 对着镜头说 30 秒今天做什么
python record.py mark --dir sessions/ep01 --label "agent写引力代码"      # 写代码过程
python record.py mark --dir sessions/ep01 --label "fail-first-run"      # 【2026-10-21 已取消】翻车段砍掉，不再打点、不再复现
python record.py mark --dir sessions/ep01 --label "修复后-球面行走跑通"  # 验收 4 项逐项演示
python record.py stop --dir sessions/ep01           # 收工，把 OBS 输出改名为 raw.mkv 放进 sessions/ep01/
```
口播用英文（字幕链路是英文；实在说不顺就中文口播＋后期配英文字幕，二期再加自动翻译）。

---

## 剪辑单（Shanks 出，`edits/ep01.json`）

按下表填实际秒数（以 `record start` 为 0 点，对照 markers.json）：

| # | 内容 | 备注 |
|---|---|---|
| 1 | 开场 15 秒：目标陈述 "Today I'm making AI build gravity for a tiny planet" | 标题卡可后期压 |
| 2 | 代码段：agent 写 5 步核心逻辑（加速剪辑，保留关键行特写） | 2–3 分钟 |
| 3 | 原理段：up 向量一句话讲清（示意图/录屏标注） | 衔接干活段与跑通段，30 秒左右 |
| 4 | 跑通段：绕球走一圈＋跳跃演示 | 主菜，1–2 分钟 |
| 5 | 结尾 15 秒：下期预告＋订阅引导 | 固定模板 |

JSON 格式见 `phase1-kit/edits.example.json`，示例标题：`"title": "I let AI build my first Godot 3D planet"`。

## 竖版切片（2–3 条，`clip.py` 从粗剪里切）

1. **黄昏绕圈**（15–25 秒）：日落光下绕星球一周的纯画面，配文 "Walking around my own tiny planet at sunset" —— TikTok/Shorts 氛围款。（原"翻车集锦"已随翻车段取消，2026-10-21）
2. **跑通高光**（20–30 秒）：角色倒吊在球底行走，配文 "It actually works. Walking UNDER a planet." —— 展示款。
3. **代码讲解**（25–35 秒）：5 步逻辑中最反直觉的一行（`up_direction = -gravity_direction`），配文 "The one line Godot tutorials don't teach you" —— 技术款，发 X 也可以。

## 文案（英文）

- **YouTube 长视频标题**：`I Let AI Code Gravity for My Tiny Planet (Godot 4 Devlog #1)`
- **简介首行**：`An AI agent wrote the gravity for my tiny planet. I just wrote the design doc.`
- **X 帖文**：`Devlog #1: I had an AI agent build spherical gravity in Godot 4 — and now I can walk all the way around my own tiny planet. 🌍 Full video: [link]`
- **TikTok/Shorts 文案**：`POV: you let AI handle physics #gamedev #godot #indiedev #ai`

---

## 已知风险（本期）

- 翻车段已取消（2026-10-21 用户拍板，先跑通第一条视频）：录制只走"目标→干活→原理→跑通→黄昏结尾"直线流程，不复现翻车、不改 player.gd。万一录制中真出了自然 bug，顺手打点留着即可，不专门等它。
- 口播英文不顺是最大变数：宁可短句＋后期字幕兜底，不要长难句。
- 若 5 步逻辑在 4.7 实测中有 API 出入，以实测为准调，任务单不硬锁代码细节。
