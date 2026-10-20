# UtaWeave Japanese LoRA

VocalRender-Pro 的日语 attention LoRA，供本项目的单主唱日语演唱实验使用。它是适配器，不能独立生成音频。

## 基础模型与版本

- 基础模型：`pymaster/VocalRender` / `VocalRender-Pro`。
- HF revision：`c2ba9d06f39fa0731b96f0258d3df33cc1bda35a`。
- 推理代码：VocalRender `be68f0c664c4840ddd37aef8a241ccbc794b27bf`。
- 张量、配置和来源的 SHA-256：`manifests/models.lock.json` 及每个适配器目录的 `provenance.json`。

## 已归档适配器

| 名称 | rank / alpha | checkpoint step | epoch | 张量大小 |
| --- | --- | --- | --- | --- |
| ja-r64-e5 | 64 / 128 | 47934 | 5 | 144,749,240 bytes |
| ja-r32-e45 | 32 / 64 | 19318 | 4.5 | 72,397,184 bytes |
| ja-r32-e5 | 32 / 64 | 21464 | 5 | 72,397,184 bytes |

训练目标层为 Base LM、Residual LM 和 LocDiT 的 `q_proj/k_proj/v_proj/o_proj`。Projection、Local Encoder 和 AudioVAE 冻结。r64 含 384 个 LoRA 张量，36,175,872 个可训练参数。

r64 默认推理相对强度为 LM=1、Residual LM=1、LocDiT=0.5。该比例来自项目内试听选择，张量文件仍保留训练原值。r32 可作为对照，设置同样的比例不代表已经找到它的最优设置。

## 训练记录

r64 使用 76,692 个自动标签/参考读音校验后的日语训练片段，约 102.14 小时；验证 9,297 个片段。它们不是人工逐句标注，参考校验也不是独立的声学发音核验。此批训练标签采用当时的参考罗马音核验流程，**不等同于当前纯音频 Vocal2Midi 推理标注流程**。

r64 训练 5 个 epoch，共 47,935 次更新（checkpoint 目录采用从零开始的 step 编号）；LM 学习率峰值 5e-5，LocDiT 为其十分之一，约 5% warmup、余弦下降；diff 系数 1，stop 系数 0.1。prompt_audio_prob=1.0，prompt 上限 50 frames，参考候选目标 6–8 秒。更多参数见 `training/r64.recipe.json`。

## 观察与限制

项目内试听认为 r64 的日语演唱细节改善，但部分歌曲音色比 r32 更粗糙；将 LocDiT 强度降到 0.5 后听感更平衡。没有独立主观测试、统计显著性结论或覆盖英语的可靠基准。模型可能唱错字、漏音、偏离节奏、失去音色或产生噪声。参考音频、乐谱、分段、采样种子和推理强度都会影响结果。

只归档推理权重，不提供原始训练歌曲、歌词、录音或优化器状态。基础模型声明 Apache-2.0；适配器的发布不会授予第三方训练录音、歌词或声音身份的权利。本项目自写代码的 Apache-2.0 与数据/资产条款分别适用。
