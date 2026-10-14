# 数学讲座字幕整理验收报告

日期：2026-10-13。状态：**目录已规范化，当前输出已从新路径重新读取验收。**

## 目录规范化

- 视频及同名最终字幕：`videos/abstract-algebra-NN.mp4` 与 `.zh-en.srt`。
- 原始 ASR：`subtitles/raw/abstract-algebra-NN.raw.en.srt`。
- 可编辑字幕源：`subtitles/blocks/abstract-algebra-NN.blocks.tsv`。
- 课件笔记、习题、材料分别位于 `notes/`、`problems/`、`materials/`。
- 工具、审读记录、QA 备份和历史归档分别位于 `tools/`、`archive/reviews/`、`archive/qa/current/`、`archive/qa/history/`。

## 当前规则

- 完整句义、定义、条件与结论、证明步骤优先。
- 中文单条最多 **100 个可见字符**：去空白后计数，标点和数学公式也计入。
- 只按自然分句点/分句内标点打包切分；不按秒数、英文词数或板书停顿切分。

## 验收结果

- 33 讲，规范文件 66 个（33 个 blocks + 33 个视频同目录最终 SRT）。
- 当前 QA 记录：`/home/deadalux/Documents/Abstract Algebra/archive/qa/current/manifest.json`。
- 所有中文条目 ≤ 100 个可见字符；EN/ZH 去空白后逐字符与输入一致。
- 时间轴 `overlaps=0`、`inversions=0`；无 <1.2 秒闪条。
- 可从 `archive/qa/current/before/` 重新构建，验证结果与 `after/` 逐字节一致。
- 历史原始基线保存在 `archive/qa/history/semantic-verified-2026-10-13.tar.gz`。

## 文件统计

| 类别 | 数量 |
|---|---:|
| 视频 MP4 | 33 |
| 视频同目录最终双语 SRT | 33 |
| 原始 ASR 英文 SRT | 33 |
| 可编辑 blocks.tsv | 33 |
| 讲课笔记 PDF | 36 |
| 习题 PDF | 36 |
| 课程材料 | 2 |

## 说明

自动 QA 只证明文字完整性、长度上限和时间轴一致性，不等于所有译文都经过逐句人工语义审校；源字幕自身缺句或截断的内容不会被脚本补写。
