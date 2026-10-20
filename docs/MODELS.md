# 模型清单与下载

`manifests/models.lock.json` 记录本次基线实际使用的文件名、字节数和 SHA-256，不包含本机绝对路径。`scripts/verify_models.py` 只读取文件校验，不加载 PyTorch pickle。

## 项目内 LoRA

```bash
git lfs install
git lfs pull
python scripts/verify_models.py --only ja-r64-e5 ja-r32-e45 ja-r32-e5
```

推理包仅含 LoRA 张量、可搬迁配置和来源说明。模型的分词器、AudioVAE 以及主干来自 VocalRender-Pro。推理加载器以 `render.local.json` 中的 `base_model` 为准，不会使用训练时旧机器的绝对路径。张量未合并到基础模型，也未改变训练得到的数值；LocDiT 0.5 是推理阶段的 LoRA 强度。

## 上游资产

| 资产目录 | 用途 | 获取方式 |
| --- | --- | --- |
| `models/VocalRender-Pro` | 合成主干与 AudioVAE | HF `pymaster/VocalRender` 的 `VocalRender-Pro/`，版本见 lock |
| `models/Qwen3-ASR-1.7B` | 初次转录、英文识别 | HF `Qwen/Qwen3-ASR-1.7B`，版本见 lock |
| `models/RomajiASR-v1.0.0` | 日语歌声识别 | [官方 v1.0.0](https://github.com/Xiantaidu/RomajiASR/releases/tag/v1.0.0) |
| `models/GAME-1.0.3-medium-onnx` | 音符提取 | [官方 v1.0.3](https://github.com/openvpi/GAME/releases/tag/v1.0.3) |
| `models/TIFA-1.0-ST` | 生成后对齐、导出来源 | [官方 v1.0.0 ST 包](https://github.com/openvpi/TIFA/releases/tag/v1.0.0) |
| `models/TIFA-1.0-ST-onnx` | 标注对齐 | 从上项导出，包含模型图、字典与资源 |
| `models/kashi-g2p-onnx` | 含汉字日语文本的 G2P | [Kashi G2P](https://github.com/Xiantaidu/kashi-g2p)；当前本地 v4 包的确切发布版本尚未核对，需手工提供匹配包 |
| 分离器 / 去混响器 | 分离人声 | 根据 [SingCrawl](https://github.com/pymaster17/SingCrawl) 配置并下载，模型不在本仓库 |
| SingMOS / wav2vec2 SSL | 音色候选评分 | 按 [SingMOS](https://github.com/South-Twilight/SingMOS) 官方说明准备 |

**尚未完全自动化的部分：** Kashi v4 本地包与源项目发布资产的版本对应关系尚未核对；分离、去混响及 SingMOS 资产仍需手动准备。本次本地归档保留了文件索引和校验值，不能据此声称全新机器上已完成无人工步骤的复现。

下载具有已记录官方地址的模型，按需执行（会下载数 GB）：

```bash
pip install huggingface-hub
python scripts/download_models.py --only vocalrender-pro qwen3-asr romajiasr game tifa
```

脚本不会覆盖已有模型目录。HF 固定到具体 revision；GitHub 发布包校验整个 ZIP 的 SHA-256 后解包。手动准备的资产可写入模板配置，不要求存放在仓库内部。

TiFA ONNX 导出使用已固定源码的原始 `Exporter`：

```bash
.venv-labeling/bin/python scripts/export_tifa.py \
  --repo vendor/TIFA --source models/TIFA-1.0-ST --out models/TIFA-1.0-ST-onnx
```

不同 PyTorch / ONNX 导出器版本可能导致导出字节不同；原始 ST 包的校验与导出产物的校验分开记录。当前 lock 是本次基线的字节记录，重新导出的可用模型不必然与它逐字节相同。

校验自行放到统一目录的模型：

```bash
python scripts/verify_models.py --root models --only vocalrender-pro qwen3-asr romajiasr game tifa
```

基础模型共约 18 GB；分离、SingMOS 及环境另占空间。模型、数据、缓存不进入普通 Git。模型许可证与字典条款见对应上游；代码许可证不能自动覆盖所有模型资产。
