# Vocal2Midi 默认标注路径

当前默认为 `utaweave annotate` → `song2voice.vocal2midi_native_worker`，只接受音频，不接受外部歌词、参考罗马音或 LRC。旧参考优先实验记录在 `docs/history/`，不参与当前默认路径。

1. 保留上游默认静音切片的区间，再用上游低能量滑窗拆分超过 10 秒的区间。适配层核对逐样本连续覆盖、原始偏移与 10 秒上限；固定长度 ASR 输入在调用前校验容量，禁止截断。`slicing_audit.json` 记录拆分前后样本数。
2. Qwen3-ASR 先按配置的主语言（默认 Japanese）转录；按文本内假名/汉字与 ASCII 字母判断日语、英语或混合。这是本项目的自动分流适配，并非独立的声学语言检测器。
3. 混合段用上游双语 G2P 与 TiFA 确定词时间，在语言切换处划分。日语重新用 RomajiASR，英语重新指定 English 的 Qwen3-ASR。
4. 上游 TiFA 负责音素和词对齐；GAME 按上游各语言逻辑提取音符。英文保留原词，不转片假名或罗马音近似。
5. 用原生秒时间构造 VR 输入，保留未知音高和短音节，检查词、音素、音符的所有权。失败片段不静默删除。

空识别不自动等同于无人声。若片段 RMS 高于全曲切片 RMS 第 75 百分位的 20%，立即报告失败；其余空识别片段保留原音频、时间与 `unrecognized_segments.json`，不编造歌词，不送入音符生成。导出清单明确记录未识别区间数量；这仍可能包括弱声演唱，需复核，不能将“已导出”解释为“全部真实歌词已覆盖”。

Linux 下 Qwen 使用官方 Transformers/CUDA 后端；上游 Windows 主路径使用 ONNX/llama.cpp。RomajiASR、TiFA 和 GAME 使用上游 ONNX 推理。在此上游提交中 Linux 传 `cuda` 不等于 ONNX 启用 NVIDIA GPU，因此明确使用 CPU provider，并限制线程池。

适配层还修正了一个文件接口：日语 ASR 的 `.txt` 是分离音素，`.lab` 是合并 mora，而 TiFA 优先读 `.txt`；这里将两者统一为选中的 mora。上游源码未修改。

这些措施保证调用、时间覆盖和格式一致，不能证明识别正确。英语被识别成片假名、日语被写成拉丁字母、跨切片单词以及歌曲语言之外的其他语种仍是已知问题。
