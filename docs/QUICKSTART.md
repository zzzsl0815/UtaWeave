# 运行指南

图形界面的两种输入与操作说明见 [可视化工作台](WEB_UI.md)。以下为命令行流程。

这是已在本机运行的研究基线，不是已验证所有平台的安装器。推荐 Linux、Python 3.12、NVIDIA CUDA GPU；实测 GPU 显存为 32 GB。先安装支持 `rubberband` 滤镜和 `libmp3lame` 编码的 FFmpeg，再准备模型。模型路径和 Python 环境路径必须按自己的机器修改。

## 1. 准备环境

核心工具：

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[audio,dev]'
source .venv/bin/activate
python scripts/bootstrap_upstreams.py
```

CUDA 环境先分别安装匹配的 `torch` / `torchaudio`；本机验证版本为 `2.8.0+cu128`，不要求所有 GPU 都使用这个 CUDA 版本。

```bash
python3.12 -m venv .venv-render
.venv-render/bin/pip install -r requirements-render.txt
.venv-render/bin/pip install --no-deps -e vendor/VocalRender
.venv-render/bin/pip install --no-deps -e .

python3.12 -m venv .venv-labeling
.venv-labeling/bin/pip install -r requirements-alignment.txt
.venv-labeling/bin/pip install --no-deps -e .
```

上述两个环境都需要先安装 PyTorch；完整依赖不只包括 requirements 中的包，CUDA/音频系统库也需可用。标注和生成后对齐在不同进程运行；`Vocal2Midi/inference` 与 `TiFA/inference` 不能在同一 Python 进程混用。

分离/评分另用一个环境，按 `vendor/SingCrawl/MSST-WebUI` 的安装说明配置其 `MSSeparator`、`s3prl` 和 SingMOS 依赖。UtaWeave 调用这个环境；不打包整个 SingCrawl，也不要求它的采集数据库。具体版本参考 [tested-environments.json](../manifests/tested-environments.json)，它是运行时记录，不是完整的依赖锁。

FFmpeg 自检：

```bash
ffmpeg -hide_banner -filters
ffmpeg -hide_banner -encoders
```

确认分别能看到 `rubberband` 和 `libmp3lame`。可以通过配置的 `ffmpeg` 字段指定绝对路径。

## 2. 模型

参见 [模型清单与下载](MODELS.md)。LoRA 需要先 `git lfs pull`。原版模型需要从官方获取；TiFA 的 ST 原始包为 PyTorch 模型，标注阶段需另行导出 ONNX。

配置模板中 `/path/to/...` 必须替换为自己的路径。当前 `reference_runtime` 和 `vocal2midi` 建议全部填写绝对路径；`render` 中的相对路径相对于配置文件所在目录解析。

```bash
cp configs/reference_runtime.example.json configs/reference_runtime.local.json
cp configs/vocal2midi.example.json configs/vocal2midi.local.json
cp configs/render.example.json configs/render.local.json
```

## 3. 目标歌曲分离

```bash
utaweave separate --audio inputs/target.flac \
  --runtime-config configs/reference_runtime.local.json --out runs/target
```

输出 `stems/vocal_native.wav`、`stems/instrumental.wav` 和 `stems/vocal_for_reference.wav`。最后一项是用于识别的单声道人声；默认经过除混响。源录音的时间轴保留。无需处理混响时可使用 `--no-dereverb`。

## 4. 音色参考与 Seed-VC

按 [Seed-VC 环境说明](VOICE_CONVERSION.md) 准备 `.venv-svc`、9 个资产和 `configs/seed_vc.local.json`。

```bash
# 自动从完整音源分离、分割，平衡音质和有效人声时长
utaweave prepare-reference --audio inputs/reference-song.flac \
  --runtime-config configs/reference_runtime.local.json --out runs/voice-reference
# 或直接提供片段，两条命令选一条
utaweave prepare-reference --clip inputs/voice-clip.wav --out runs/voice-reference
```

VR 仍需一个固定提示音频作为导唱上下文，使用同一个片段准备 `runs/guide-reference` 即可。它与最终目标音色分开设置；切换目标时不必更换导唱提示。参考详情见 [参考准备](REFERENCE_PREPARATION.md)。

```bash
utaweave prepare-reference --clip inputs/guide-prompt.wav --out runs/guide-reference
utaweave reference-status --run runs/guide-reference
utaweave reference-status --run runs/voice-reference
```

`guide-prompt.wav` 是固定的干净人声片段，可与手动目标参考使用同一文件；自动模式已选出的片段也可复用。目标参考的自动筛选使用 SingMOS 与有效人声时长的平衡规则，不依赖失败融合实验的训练参考选择器。

## 5. 纯音频自动标注

修改 `configs/vocal2midi.local.json`：

- `audio` 指向 `runs/target/stems/vocal_for_reference.wav`。
- `out` 指向 `runs/target-score`；`bpm` 必须由用户提供，本基线**尚不自动估算全曲 BPM**。
- 模型路径和 `upstream_repo` 对应准备好的资源。

```bash
utaweave annotate --config configs/vocal2midi.local.json \
  --python .venv-labeling/bin/python
utaweave validate-conditioning runs/target-score/score.vr.json
```

输出 `score.vr.json`、`notes.mid` / `notes.csv`、识别、语言分流、对齐和覆盖记录。此模式拒绝外部歌词、罗马音和 LRC 字段。英文支持仍是实验性的。

已有合格的 `score.vr.json` 可以跳过自动标注。普通 MIDI 还缺歌词、音素及对应关系，不能直接替代此文件；字段说明见 [Vocal2Midi 集成](VOCAL2MIDI_INTEGRATION.md)。命令行的原曲模式仍需要同一时间轴的人声和伴奏 stems，用于边界规划、时长及混音增益参考。可视化工作台另提供 MIDI＋歌词入口：生成明确标记的时间轴缓冲，使用独立的响度策略，不要求原始人声。

`validate-score` 对应旧统一乐谱对象；`validate-conditioning` 对应当前自动标注生成的 VR 条目数组。两个格式尚未合并，不能互换命令。

## 6. 渲染、自动重试与整曲输出

修改 `configs/render.local.json`，指定乐谱、目标音色 `reference_run`、固定导唱提示 `guide_reference_run`、`seed_vc_config`、目标 `stems`、模型路径以及运行环境。

```bash
utaweave render --config configs/render.local.json --out runs/render \
  --python .venv-render/bin/python
```

执行短唱句规划 → 原生停止生成 → TiFA 对齐 → 对异常段额外采样两个候选 → 固定规则选取 → Seed-VC 换声 → 再次 TiFA 对齐 → 有限变速、衔接和混音。最多 14.5 秒的歌词内容；无法完整覆盖且安全切开时会报错，不强制超长整曲生成。

最终位置由 `runs/render/latest.json` 的 `final_manifest` 指定，对应目录内有：

- `audio/mix.wav` / `.mp3`：原曲时间轴混音。
- `audio/vocal.wav` / `.mp3`：原曲时间轴纯人声。
- `audio/natural.wav` / `.mp3`：不做变速的原始生成串联版，不对应原曲完整时钟。
- 配置、源码摘要、参考与权重哈希、分段规划、对齐警告和衔接审计。
- `index.html` 和 `listening_player.js`：可拖动进度条、按唱句跳转的试听页。混音与纯人声切换保留位置，原速串联版单独播放。

导唱缓存与目标音色分离；相同输入和代码可复用已缓存的生成；修改模型、设置或代码会产生新的运行签名。数值检查成功不等于演唱准确或音质合格，最终仍需试听。

## 7. 打开试听页

`runs/render/latest.json` 中的 `report` 给出实际页面位置。用其父目录作为服务根目录：

```bash
python -m song2voice.report_server --directory runs/render/RUN_ID/voiced --port 8768
```

本机打开 `http://127.0.0.1:8768/`。远程机器需要 SSH 转发，例如在自己电脑运行 `ssh -L 8768:127.0.0.1:8768 USER@SERVER`；已有端口转发则使用它提供的地址。服务支持 HTTP Range，音频默认使用 MP3 预览，WAV 可单独下载。

旧结果只有音频时可重建页面，不重新推理：

```bash
utaweave report --manifest runs/render/RUN_ID/voiced/manifest.json
```

## 8. 复用、更换声音与排错

- 换目标音色：只改 `reference_run`，保留 `guide_reference_run`、乐谱、VR 设置和 `--out` 根目录，复用导唱缓存。
- 只有已有导唱：运行 [convert](VOICE_CONVERSION.md#复用已有导唱)，无需再标注或生成 VR。
- 任务失败：检查该 run 的 `pipeline_status.json` 与对应阶段日志。修复依赖后重跑相同命令；输入、配置或实现变更会产生新的运行签名。
- 无法安全切句、时长超限或对齐比例异常：先检查源乐谱、分离结果及标注，修正后重新运行。不要把自动检查通过理解为歌词已经人工校对。
- 参考缺失或校验失败：运行 `reference-status`。不手改哈希、不将长录音冒充已选片段；重新执行 `prepare-reference`。
- 音频显示 0:00 或不能跳转：确认网页和音频来自同一 HTTP 服务，使用本项目 `report_server`，检查音频请求是否返回 200/206。不要直接双击 HTML 或把整个数据目录作为公开服务根目录。

仓库只提供推理主流程与已发布 VR LoRA 的训练参数记录，不包含融合训练命令。新的训练不会随推理自动启动。
