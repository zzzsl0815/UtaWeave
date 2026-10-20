> 历史实验记录：描述当时的实现；当前运行入口以根目录 README 为准。

# 现有资产与迁移边界

本机路径及代码哈希记录在被 Git 忽略的 `workspace.local.json`。以下为已有实现的复用建议；标准乐谱导入／校验和音色参考准备已在新包实现，其余完整转谱／渲染仍待迁入。

| 已有实现 | 可复用内容 | 必须移除或调整的耦合 |
|---|---|---|
| `pipeline_stream_v4_romaji/stream_frontend.py`、`separate_with_provenance.py` | big_beta6x分离、去混响、切片来源记录、双流经验 | 采集目录结构、固定GPU和模型路径；推理不能直接丢低分句 |
| `pipeline_stream_v4_romaji/romaji_labels.py` | 同源同句的罗马音匹配、严格假名转换、来源审计 | 平台歌词字段；允许独立本地LRC／YRC／读音文件 |
| `reading_candidates.py`、`japanese_text.py` | Kashi候选、日语G2P、促音等映射 | 模型绝对路径，混合英文处理要有显式策略 |
| `align_tifa.py` | TiFA原生对齐、候选选择、零宽记录 | 固定目录、对齐失败自动移动／过滤逻辑；整曲任务需保留问题句 |
| `infer_notes_japanese.py`、`label_integrity.py` | ROSVOT音符、原歌词单位映射、数量与边界校验 | SingCrawl路径、训练批次环境；不能依赖旧文件状态 |
| SingCrawl `compute_notes.py`、`export_vr.py` | 原模型时值token转换、VR字段兼容检查 | 量化与原始时值分开；补齐MIDI与标准乐谱导出 |
| `training_control_v3/full_song_teru.py` | 全曲stems、独立参考、分阶段执行、整曲混音经验 | 固定歌曲ID、旧batch、18句候选、指定歌手和训练集索引；不能直接改名当通用入口 |
| `silence_chunk_plan.py` | 单位保护、真实低能量区、短句规划 | 固定输入目录、只按RMS且没有显式呼吸、硬时长无安全切点即失败 |
| `render_silence_chunks.py` | 已验证的VR原生EOS短句、固定参考、原始输出 | 固定14.5秒、checkpoint哈希、模块数量、seed／batch和路径；合成与装配要分模块 |
| `lora_scaling.py` | Base／Residual LM与LocDiT分别控制强度 | 当前模块数量断言需改为按实际模型结构审计 |
| `refine_silence_tails.py` | 生成后的末mora定位和尾部诊断 | TiFA强制对齐不能单独判定完整；不能只按音量把呼吸切掉 |
| `breath_protected_joins.py` | 完整原生余量、实际间隙衔接、音素保护与边缘平滑 | 当前假定片段有空隙，遇重叠会拒绝；不是通用呼吸检测或重叠消解器 |
| `verify_silence_chunks.py` | 时长、峰值、哈希、歌词覆盖、HTTP Range校验 | 依赖旧manifest、服务端口与目录；拆成与服务无关的音频检查 |
| `full_song_listening.html`、`media_http.py` | 被动刷新、版本对照、接缝跳转、Range播放 | 固定歌曲、输出路径和两种模型名称，改由manifest驱动 |

本次已经落地：

1. `src/song2voice/score.py`：以源音频为时钟的中间格式、旧实验导入与结构校验。
2. `src/song2voice/cli.py`：实际可执行的 `import-legacy`、`validate-score`，不提供假实现的一键渲染命令。
3. `work/teru.score.json`：本机已有18句的导入；源码／原音频未修改，18条时钟差异保留在 warnings。
4. 设计配置、分阶段接口、自然度要求、迁移顺序与验收清单。
5. `references.py`、`reference_audio.py`、`reference_worker.py`：可独立运行的参考歌曲分离、连续窗口、SingMOS与终端候选表；后端路径均由本地配置提供。
6. `selection.py`、`terminal_review.py`：终端输入编号、脚本指定编号、待选状态、独立参考快照与选择历史，以及可选候选WAV打包。不使用网页选择服务。

以下能力仍是待实现项：完整无采集库依赖的歌词对齐／转谱、通用VR渲染、跨片段重叠处理、自动呼吸检测、MIDI导出、整曲通用试听报告和干净环境安装。参考选择采用自动排序＋人工确认，不自动把最高分提交成最终音色。现有单曲试听不作为完整新歌端到端能力已经完成的证据。

训练、数据采集和整库伪标注可后续独立成扩展，首版主命令不自动启动这些任务。当前工作区及试听服务保留原状，不为整理而移动模型、训练数据或旧结果。
