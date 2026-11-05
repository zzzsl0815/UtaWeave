# 第三方来源

本项目是流水线及适配层，不把依赖的研究模型或上游功能声明为本项目原创。源码由 `scripts/bootstrap_upstreams.py` 按锁定提交另行取得，保留原仓库许可证、NOTICE、字典及内嵌组件说明。

| 项目 | 使用范围 | 已查阅的代码许可 |
| --- | --- | --- |
| VocalRender | score prompt、模型与 LoRA、AudioVAE | Apache-2.0 |
| Vocal2Midi | 切片、识别预处理、TiFA/GAME 接口 | Apache-2.0；内嵌组件各有条款 |
| RomajiASR | 日语歌声 ONNX 识别 | Apache-2.0 |
| TiFA | 对齐、导出与词典 | MIT；词典/资产条款保留 |
| GAME | 音符估计 | MIT |
| Qwen3-ASR | 日英初次转录、英语识别 | Apache-2.0 |
| Kashi G2P | 日语汉字读音转换 | 源项目代码/权重为 Apache-2.0，编译词典为 CC BY-SA 4.0；本地包的确切发布版本尚未核对 |
| SingMOS | 音色候选质量评分 | MIT |
| SingCrawl / MSST-WebUI | 外部配置的分离器 | MSST-WebUI 为 AGPL-3.0；按上游逐组件条款 |

部分许可证副本在 `third_party/licenses/`，具体版本见 `manifests/upstreams.lock.json`。外部模型下载不会因 UtaWeave 自写代码采用 Apache-2.0 而改变原许可。[Kashi G2P](https://github.com/Xiantaidu/kashi-g2p) v4 本地 ONNX 包与源项目发布资产的版本对应关系尚未核对，本仓库记录运行文件校验值和源项目说明，未重新分发该包。

模型预训练、第三方歌曲、训练录音、下载歌词、字典与生成作品的权利不由本仓库代码许可证替代。

## Seed-VC 音色渲染

通过独立进程加载第三方代码和权重，下载内容位于 Git 忽略的 `vendor/` 与 `models/svc/`。具体版本、9 个资产与 SHA-256 见 `manifests/svc.lock.json`。

[Seed-VC](https://github.com/Plachtaa/seed-vc) 上游代码 GPL-3.0；模型及其 Whisper、BigVGAN、CAMPPlus、RMVPE、DAC 依赖遵循各自发布条款。UtaWeave 的 Apache-2.0 不改变这些条款。

历史实验曾使用 HQ-SVC 和 SoulX-Singer-SVC，其模型缓存及结果仍遵循各自条款；适配器已移出当前流水线，原说明见 [历史实验](docs/history/VOICE_COMPARISON.md)。

工作台的日语读音转换使用 pyopenjtalk，MIDI 解析和导出使用 mido。网易云入口可调用用户已安装的 NeteaseCloudMusicApi 模块；本仓库仅包含本地适配器，不重新分发 API 模块、账户凭据或歌曲。具体依赖许可证以相应安装包为准。
