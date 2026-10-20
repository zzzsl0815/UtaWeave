> 历史实验记录：描述当时的实现；当前运行入口以根目录 README 为准。

# Vocal2Midi 标注后端

## 当前默认：仅音频标注

2026-10-20 起默认入口为 `song2voice annotate`，运行
`song2voice.vocal2midi_native_worker`。此版本不读取下载歌词、参考罗马音或 LRC 时间轴，
也不复用旧的 25 句切片。旧的参考优先实验保留作历史记录，不再是默认流程。

```bash
PYTHONPATH=src python -m song2voice annotate \
  --config /path/to/vocal2midi.local.json \
  --python /path/to/labeling-env/bin/python
```

配置使用 `configs/vocal2midi.example.json`。误传 `reference_moras`、`reference_audit`、
`source_intervals`、`original_lyrics`、LRC 等旧字段会报错，防止悄悄采用旧标注。

流程为：上游音频自动切分 → 日英分流 → 上游日语／英文 ASR → TiFA → GAME。
主语言使用上游同样的显式语言选项，默认 `language=ja`，不使用自由语言猜测或额外转录提示。
日语采用 RomajiASR；英文采用 Qwen3-ASR 的 English 提示，保留英文单词，由 TiFA
对齐英文音素，再使用上游 GAME 的元音音节边界处理。英文不再走日语读音转换。
下载罗马音不参与此路径，不进行歌曲专用的手填纠错。

上游主要提供按语言运行的路径。为处理一首歌内的日英切换，入口增加一个薄分流层：
根据音频识别文本选择分支；同一片段有两种语言时，仅用上游 TiFA 定位语言边界，
再分别进入上游的日语和英文流程。这个分流层是本项目的适配，不是上游原有的一键混合语言模式。
Qwen3-ASR 在本机使用官方 Transformers／CUDA 后端，代替上游面向 Windows 的
ONNX／llama.cpp 后端；日语和英文的 TiFA／GAME 核心代码不改。

原生音符保留在 `notes.csv` 和 `notes.mid` 中，包括英文后续音节的 `+` 和延音的 `-`。
VR 导出另存 `score.vr.json`：英文保留原词及其音符，英文音素只用于对齐检查，
不把 ARPABET 当作要唱出的歌词。识别和合成音质仍需实际验证。

此默认路径不接受失败片段被静默删除。无法识别或对齐时保存失败信息并停止完整出谱。
59 项接入层测试已通过；模型实际运行结果另存于本地 run 目录。

当前歌曲的纯音频标注实跑得到 22 个原生切片，日英分流后 24 段（18 日语、6 英文），
全部完成 TiFA／GAME 和 VR 条件结构检查。原始录音、原生切片与分流边界均保留，
没有采用参考罗马音、下载歌词或 LRC 时间轴。英文 ASR 仍存在把 birthday 误听为
but say 等错误；此版本按要求保留原生识别结果，不用外部歌词改写。

下面内容记录此前参考优先版本，供结果追溯。

完整上游源码保存在 `vendor/Vocal2Midi`，固定提交
`f589c183ec9a4b25ea234459b8a4b72ba993af50`；RomajiASR 源码固定提交
`0df075c85ddc9b9df9c249935c6bd1702de6c420`。两份源码与其许可证、NOTICE 保持原样。

## 历史版本的实际复用范围

独立 worker 调用上游的 RomajiASR ONNX runtime、音素归一化、音素到 mora
合并、整 mora 到假名映射、TiFA ONNX G2P／强制对齐、GAME 音符提取和字音对齐。
无歌词参考时直接使用歌声识别结果；有已经匹配验证的下载罗马音时，以参考读音为准，
同时保留识别结果。英文在日语识别路径中输出日语音素，不再把英文 ARPABET 直接交给 VR。

识别成功不意味着歌词正确：无参考的 ASR 仍可能漏字、误听，尤其是混杂英文。
片段覆盖、音符与 mora 映射校验不能代替识别准确率或人工试听。

## 接入层的边界

- 当前上游未匹配歌词的直接音素 ASR 路径，`.txt` 保留原始分离音素，`.lab` 是合并 mora。
  TiFA 优先读取 `.txt`。接入层把两者统一为最终选中的 mora，避免把 `h a` 当作两个字。
- 整 mora 转假名保持 `tu → とぅ`、`tsu → つ`、`ti → てぃ`；不先串接成字符串再猜读音。
- 固定长度 ONNX 输入若容纳不下音频，显式报错，禁止底层静默截断。
- 上游在部分失败时允许跳过片段／退回纯音高；完整演唱的导出不允许用这一回退冒充成功。
  全部原片段都保留在识别记录中，有未解决项时不导出完整乐谱。
- 直接从 TiFA 字音时间及 GAME 秒时间构造 VR 条件，不经 MIDI 网格往返，不拉伸真实短 mora。
  未知音高记零并标记；所有权映射与浮点边界检查沿用 Song2Voice 的时间完整性检查。
- CPU 运行明确指定 CPU provider。该提交把 `cuda` 解释成 DirectML；Linux 下传 `cuda`
  并不代表启用了 NVIDIA GPU。worker 只在同步加载模型时设置线程池上限，随后还原工厂。

## 模型与运行

官方发布地址及 SHA-256 记录在本地 `work/upstream_models_verified.json`。
发布页：[RomajiASR v1.0.0](https://github.com/Xiantaidu/RomajiASR/releases/tag/v1.0.0)、
[GAME v1.0.3](https://github.com/openvpi/GAME/releases/tag/v1.0.3)、
[TiFA v1.0.0](https://github.com/openvpi/TIFA/releases/tag/v1.0.0)。
RomajiASR v1.0.0、GAME v1.0.3 medium 已校验发布包；TiFA 使用官方 ST 权重，
通过 TiFA 的原有 `deployment.exporter.Exporter` 导出五个 ONNX 图。
ST 发布包本身是 PyTorch 权重，不能直接作为 ONNX 模型目录。

在具备 `requirements-labeling.txt` 依赖和 ffmpeg 的独立 Python 环境中：

```bash
PYTHONPATH=src python -m song2voice.vocal2midi_worker \
  --config /path/to/vocal2midi.local.json --stage all
```

配置样例在 `configs/vocal2midi.example.json`。可传 `source_intervals` 重用已切分片段，
或传 `audio` 让上游按静音自动切分。`reference_moras` 是此前歌词匹配阶段的机器输出，
不是逐曲手填读音的入口。识别、对齐和导出记录分别写入 `recognition.json`、
`alignment.json`、`manifest.json`；只有全部步骤完成才出现 `score.vr.json`。

此入口负责已经分离的人声到乐谱，不包含下载、分离、音色选择或 VR 推理。
它与既有渲染器通过 `score.vr.json` 对接，尚未整合成面向公众的一键 CLI。

## 运行检查

接入层测试覆盖固定输入长度保护、未识别片段保留、参考优先、外来 mora、重复元音、
鼻音／促音保留，以及时间映射。上游测试初次选跑为 105 通过、21 跳过、3 失败：
两项为粤语繁简转换，一项为拼音 ASR 的测试回退路径；未修改上游来规避失败。
缺少上游额外模型的测试跳过不能算真实模型验证。

《君が生まれた日》25 个既有片段已完成真实 RomajiASR 无参考识别，耗时约 8.25 秒。
25/25 是调用覆盖数，不是音节识别准确率；原始输出已显示部分英文漏音。

组合版另有 19 句采用已匹配的下载罗马音，6 句采用原声 ASR（其中 4 句英文）。
25 个片段均经 TiFA／GAME 导出并通过条件结构检查，未生成新的演唱试听。
上游自带的短字修复和边界调整保留在日志中；此处没有把对齐分数作为识别准确率。
