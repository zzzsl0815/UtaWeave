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
