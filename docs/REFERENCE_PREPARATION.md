# 参考音色：两种入口

两种方式都产出可校验、可复用的冻结参考。输出目录的 `latest.json` 指向实际任务；`render.reference_run` 和 `convert --reference-run` 可填写输出目录或具体任务目录。Seed-VC 使用选中片段的完整音频，不静默截成另一段。

## 直接提供片段

```bash
utaweave prepare-reference --clip inputs/voice-clip.wav --out runs/voice-reference
```

适用于已经剪好的单人清唱。接受可解码的 1–25 秒音频，检查时长、有限数值及转单声道后非静音，再按原始字节保存。不分离、不裁切、不重新评分，也不把未评测的片段标成高质量。超过 25 秒会报错，避免 Seed-VC 上游截断参考；完整歌曲请走自动入口。

记录状态为 `provided_clip`，`score` 为 `null`。这是手动提供片段，不需要再从候选列表选一次。

## 从音源自动分割和选取

```bash
utaweave prepare-reference --audio inputs/reference-song.flac \
  --runtime-config configs/reference_runtime.local.json --out runs/voice-reference
```

已分离的音源可以复用：

```bash
utaweave prepare-reference --from-run runs/voice-stems --out runs/voice-reference
```

`--from-run` 是自动模式的缓存入口，读取原分离任务中的运行环境和人声；不是第三种选取策略。自动模式默认使用分离原人声，可通过 `--policy configs/auto_reference.example.json` 指定去混响人声等参数。`--audio` 默认不额外执行去混响，只有策略要求时才运行。

搜索与排序：

1. 对全曲做连续短窗扫描，同时在 3–24 秒的多个时长上取候选。短窗默认 4 秒、步长 1 秒，不以活动度预筛的名额替代全曲覆盖。
2. 用 SingMOS Pro 对实际导出的每个完整片段评分；围绕分散的高分区域，再向前、向后和居中扩展时长并评分。无需已有人工选段。
3. 过滤人声活动占比低于 75%、连续静音超过 1 秒或过于安静的片段。活动度来自 10 ms RMS 启发式，不是说话人识别或和声检测。
4. 音质必须至少 3.5 分，且不能比当前最佳有效候选低超过 0.35 分。通过后按下式排序：

```text
综合分 = SingMOS + 0.30 × log2(有效人声秒数 / 7) − 边界惩罚
```

有效人声时长每翻倍奖励 0.30；非静音边界扣 0.03。不会靠拼接、补静音或牺牲大量音质凑满 24 秒。搜索仍是有限候选的启发式优化；无合格候选就报错，不强行使用差片段。

候选统一为连续单声道 48 kHz PCM24，首尾仅加 3 ms 淡入淡出；SingMOS 读取此实际文件后按其 16 kHz 协议评分。SingMOS 衡量预测音质，不代表目标音色相似度，也不能可靠排除合唱或伴奏泄漏。

## 记录和复用

- `reference.selected.json`：所选片段、源区间、哈希、评分和自动决策依据。
- `selected_reference/`：独立冻结音频；无需依赖候选文件继续存在才能使用。
- `reference_candidates.json`：候选的分数、有效时长、综合分和拒绝原因。
- `search/`、`refinement/`：两轮候选、评分及日志。
- `reference_recipe.json`：来源、分离记录、策略、模型和实现摘要。

相同输入与实现复用评分；修改输入、策略或实现产生新任务。使用前执行音频与记录哈希校验，损坏不会被静默接受。旧的已冻结人工/自动参考仍可读取；交互候选选择、等待选择和固定长度对照命令已从主流程移除。

```bash
utaweave reference-status --run runs/voice-reference
```
