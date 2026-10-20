# 运行指南

这是已在本机运行的研究基线，不是已验证所有平台的安装器。推荐 Linux、Python 3.12、NVIDIA CUDA GPU；实测 GPU 显存为 32 GB。先安装支持 `rubberband` 滤镜和 `libmp3lame` 编码的 FFmpeg，再准备模型。模型路径和 Python 环境路径必须按自己的机器修改。

## 1. 准备环境

核心工具：

```bash
pip install -e '.[audio]'
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

## 4. 音色参考

```bash
utaweave prepare-reference --audio inputs/reference-song.flac \
  --runtime-config configs/reference_runtime.local.json --out runs/voice-reference
```

程序在终端打印候选原曲起止时间、时长、处理方式和 SingMOS 分数，再输入编号选择。默认候选为连续 6–8 秒，同时比较分离原人声与去混响版本。评分帮助挑选音质，不代表音色模仿准确率。

远程无交互终端：

```bash
utaweave prepare-reference --audio inputs/reference-song.flac \
  --runtime-config configs/reference_runtime.local.json \
  --out runs/voice-reference --no-prompt
utaweave list-references --run runs/voice-reference
utaweave select-reference --run runs/voice-reference --candidate 1
```

等待选择时退出码为 3。选定片段被保存为独立 WAV 和校验记录；文件发生变化会拒绝复用。用户也可以导出候选本地试听，详见 [参考选择](REFERENCE_PREPARATION.md)。

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

`validate-score` 对应旧统一乐谱对象；`validate-conditioning` 对应当前自动标注生成的 VR 条目数组。两个格式尚未合并，不能互换命令。

## 6. 渲染、自动重试与整曲输出

修改 `configs/render.local.json`，指定乐谱、`reference_run`、目标 `stems`、模型路径以及运行环境。

```bash
utaweave render --config configs/render.local.json --out runs/render \
  --python .venv-render/bin/python
```

执行短唱句规划 → 原生停止生成 → TiFA 对齐 → 对异常段额外采样两个候选 → 固定规则选取 → 有限变速、衔接和混音。最多 14.5 秒的歌词内容；无法完整覆盖且安全切开时会报错，不强制超长整曲生成。

最终位置由 `runs/render/latest.json` 的 `final_manifest` 指定，对应目录内有：

- `audio/mix.wav` / `.mp3`：原曲时间轴混音。
- `audio/vocal.wav` / `.mp3`：原曲时间轴纯人声。
- `audio/natural.wav` / `.mp3`：不做变速的原始生成串联版，不对应原曲完整时钟。
- 配置、源码摘要、参考与权重哈希、分段规划、对齐警告和衔接审计。

相同输入和代码可复用已缓存的生成；修改模型、设置或代码会产生新的运行签名。数值检查成功不等于演唱准确或音质合格，最终仍需试听。
