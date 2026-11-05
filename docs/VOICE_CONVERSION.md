# VR 导唱与 Seed-VC 音色渲染

当前固定使用 Seed-VC 的 F0-conditioned 44.1 kHz 歌声模型。VR 负责歌词、旋律和演唱细节；Seed-VC 用目标参考处理音色。固定不自动移调、半音偏移 0、长度倍率 1；默认 30 步、CFG 0.7、FP16。这样减少目标参考改变演唱的因素，但不保证换声完全保留每个细节，结果仍需试听。

## 环境

```bash
python3.12 -m venv .venv-svc
# 先安装适合本机 CUDA 的 torch / torchaudio；本机实测 2.8.0+cu128。
.venv-svc/bin/pip install -r configs/svc_requirements.txt
.venv-svc/bin/pip install --no-deps -e .
python scripts/bootstrap_svc.py
cp configs/seed_vc.example.json configs/seed_vc.local.json
```

按本机修改 `repo`、`python`、`assets`；相对路径以配置文件目录为基准。安装脚本仅拉取锁定 Seed-VC 源码和 9 个模型资产，检查 Git blob 和 SHA-256。环境仍需要 `descript-audio-codec`：Seed-VC 自身导入其量化器。主流程不依赖 HQ-SVC、SoulX-Singer 或融合训练权重。

完整依赖以当前运行环境为依据，尚未重新验证全新机器安装。源码与模型许可见 [第三方说明](../THIRD_PARTY_NOTICES.md)。

试听报告使用支持 HTTP 字节范围的本地服务，浏览器可以直接读取跳转位置附近的音频：

```bash
python -m song2voice.report_server --directory runs/converted/RUN_ID --port 8768
```

将 `--directory` 换成 `latest.json` 指向的实际报告目录。整曲 `render` 的试听页位于其 `voiced/` 下，进度条支持拖动、键盘定位和前后跳转；加载期间保留最新跳转目标，切换音轨保持位置及播放/暂停状态。具体命令见 [运行指南](QUICKSTART.md#7-打开试听页)。

## 复用已有导唱

先用 [参考准备](REFERENCE_PREPARATION.md) 的任一方式准备目标音色，然后运行：

```bash
utaweave convert --config configs/seed_vc.local.json \
  --guide inputs/guide-01.wav inputs/guide-02.wav \
  --reference-run runs/voice-reference --out runs/converted
```

无需重新运行 VR、标注或训练。每批在独立进程中加载一次 Seed-VC，各句使用固定种子；输入按原字节冻结。输出目录按输入、目标参考、配置、运行环境、源码及模型哈希生成。已完成音频校验后复用，失败或损坏的输出按句重做。

`latest.json` 指向完整成功结果，其中包含本地 `index.html`：目标参考、VR 导唱、Seed-VC 输出。播放器只做固定增益匹配；原生下载保留模型的 44.1 kHz float WAV，没有混响、降噪或时间拉伸。

## 接入乐谱渲染

`configs/render.example.json` 新增两个配置：

- `guide_reference_run`：VR 生成所需的固定导唱提示，准备一次后可持续复用；不随目标歌手切换。
- `seed_vc_config`：Seed-VC 运行配置。

`reference_run` 现在只指定最终目标音色。目标改变时，只要同一输出根目录下乐谱、导唱提示、VR 参数和依赖不变，`guides/` 缓存可复用，不重新生成演唱。

顺序为 VR 分句 → TiFA 对齐 → 有限重试与选择 → Seed-VC → 再次 TiFA 对齐 → 有限变速、衔接与混音。换声后的音频不能直接沿用 VR 的音素边界。Seed 原生输出保留；拼接前重采样到 48 kHz，最终输出原时间轴人声、混音和不变速串联版。

## 已收敛的实验分支

HQ-SVC、SoulX-Singer-SVC、三后端比较和固定 3/7/12/18 秒参考对照已退出可执行主流程，统一使用 `convert`。此前实验说明见 [历史记录](history/VOICE_COMPARISON.md)，历史音频、页面和临时源码快照不随仓库发布，也不作为运行依赖。
