# 从歌曲准备音色参考

本地歌曲 → 分离人声 → 可选去混响 → 连续6–8秒窗口 → 对最终WAV评分 → 终端列出候选 → 输入编号 → 固定参考文件。参考选择不需要浏览器、网页服务或端口转发。

运行环境使用SingCrawl／MSST和本地SingMOS权重，各路径通过 `reference_runtime.local.json` 提供，模板见 `configs/reference_runtime.example.json`。协调器与选择工具使用Python标准库，神经后端在配置指定的独立Python中执行。当前还没有全新机器自动安装这些模型的脚本。

## 交互终端

```bash
PYTHONPATH=src python -m song2voice prepare-reference \
  --audio /path/to/song.flac \
  --runtime-config reference_runtime.local.json \
  --out runs/reference_example
```

准备完成后打印编号、原曲起止时间、时长、SingMOS分数及处理方式。输入编号确认，输入 `q` 保留待选状态；最高分不会自动成为最终参考。以后可以重新列出或选择：

```bash
PYTHONPATH=src python -m song2voice list-references --run runs/reference_example
PYTHONPATH=src python -m song2voice review-reference --run runs/reference_example
PYTHONPATH=src python -m song2voice select-reference --run runs/reference_example --candidate 1
```

## 非交互运行

`prepare-reference --no-prompt` 只打印候选并保存待选状态；没有交互终端时也不会卡在读取输入。准备完成但未选择返回状态码3，已确认返回0，错误返回2。脚本可以明确传入 `--candidate 1`，或准备后单独调用 `select-reference`。`--json` 输出机器可读结果且不询问输入。

```bash
PYTHONPATH=src python -m song2voice prepare-reference \
  --audio /path/to/song.flac --runtime-config reference_runtime.local.json \
  --out runs/reference_example --candidate 1 --json
PYTHONPATH=src python -m song2voice reference-status --run runs/reference_example
PYTHONPATH=src python -m song2voice wait-reference --run runs/reference_example --timeout 600
```

下游后端可通过 `selection.require_reference(run)` 获取已校验的参考文件；未选择或文件损坏时禁止消费。通用VR渲染命令仍待迁移，以上命令完成的是参考准备与选择阶段。

选择结果保存在 `reference.selected.json`，实际WAV独立复制到内容哈希命名的 `selected_reference/`，记录使用任务内相对路径。更改选择保留历史，再次确认同一候选不重复写入。再次运行已完成的同一任务会复用选择，不重新分离、评分或加载模型；修改预处理配方需使用新任务目录。

远程终端如需下载试听，可导出候选包再用 `scp` 取回。包内只有候选WAV、时间和评分表及编号说明，不含整曲、模型或凭据：

```bash
PYTHONPATH=src python -m song2voice export-references \
  --run runs/reference_example --out shortlist.zip
```

## 音频与评分

可选 `--metadata` 传入包含标题、歌手和源时长的JSON，`--top-count` 默认8；`--no-dereverb` 只提取人声。可选 `fetch-netease` 通过用户配置的本地API bridge导入明确请求的歌曲，不自动启动训练库采集。账号凭据由bridge读取，不写入产物或日志；无法获得完整音源时停止导入。

切片保留原曲连续时间，不把多句剪接成一个样例。优先使用低能量边界；6–8秒长句没有合适边界时保留候选并标记。静音比例和有效演唱比例为包络启发式，不能证明只有一个人演唱。

有去混响模型时，比较同一区间的“仅分离”与“分离＋去混响”版本，两者使用一致的候选增益策略。评分对象为最终48kHz单声道PCM24 WAV，经SingMOS前端转为16kHz，以FP32和掩码帧均值评价，不截断输入。

按分数降序选候选，排除与已选区间重叠比例超过10%的窗口。SingMOS对应音质，不代表音色身份、克隆相似度或适配度。没有4.5以上片段时保留真实分数，供用户选择。

主要产物：

- `separation.json`、`stems/`：源人声、伴奏、去混响人声及哈希。
- `candidate_windows.json`、`quality_scores.json`：连续窗口、原曲时间、实际WAV分数及哈希。
- `reference_candidates.json`、`reference_candidates.txt`、`reference_candidates.csv`：去重后的候选记录、终端表与CSV。
- `reference_recipe.json`：输入、模型资产、配置及实现哈希。
- `reference.selected.json`、`selected_reference/`：明确选择的参考与独立WAV。

新版不生成候选网页；旧实验目录中的历史网页不再作为流水线入口。

2026-10-19本机案例：歌曲1892575565《りそうのせかい (feat. higma)》，扫描96个源区间、比较192条音频变体。用户已确认候选1：原曲00:30.035–00:37.000，SingMOS约4.389，仅分离人声。
