# 数学讲座双语字幕批处理管线

项目入口是 `tools/run_global_rebalance.py`。历史 `docs/legacy-session-handoff.md` 只记录旧现象，其中 22 秒、25 词、4–15 秒以及强制 50 ms 黑屏规则已废弃。

## 目录约定

- `subtitles/blocks/abstract-algebra-NN.blocks.tsv`：可编辑主数据源，格式 `EN<TAB>ZH`。
- `subtitles/raw/abstract-algebra-NN.raw.en.srt`：原始 ASR 英文。
- `videos/abstract-algebra-NN.zh-en.srt`：最终双语字幕，与同目录 MP4 同名。
- `archive/reviews/`：可选人工/局部语义审读记录。
- `archive/qa/current/`：当前验收记录，`before/` 和 `after/` 都保持项目目录镜像。
- `archive/qa/cache/`、`archive/qa/stage-*`：可重建 cache 和 dry-run 暂存，已被 `.gitignore` 忽略。

## 当前切分规则

1. 完整句义、定义、条件结论、证明步骤和问答对应优先。
2. 中文单条最多 **100 个可见字符**，去空白后计数，标点和公式也计入。只在中英自然分句点切分，不按秒数、英文词数或板书停顿切分。
3. 英文按中文语义断点同步；不用 100 字硬切英文，避免制造英文残句。
4. 不重译、不补句、不改标点或公式，不改变讲授顺序。两列分别去空白后必须与输入逐字符相同。
5. 时间轴保留旧端点，新增边界回查原始 ASR 窗口；重叠和倒序必须为零。
6. 不要求每讲全文 agent 审读。脚本批量处理，只有 manifest 的回退、时间冲突或语义配对警告需要局部查看。

## 常用命令

```sh
# 全量 dry-run
PYTHONDONTWRITEBYTECODE=1 uv run --no-project python tools/run_global_rebalance.py \
  --max-zh-chars 100 --stage-dir archive/qa/stage-check

# 只处理第 36 讲
PYTHONDONTWRITEBYTECODE=1 uv run --no-project python tools/run_global_rebalance.py \
  --lectures 36 --max-zh-chars 100 --stage-dir archive/qa/stage-lec36

# 检查后部署
PYTHONDONTWRITEBYTECODE=1 uv run --no-project python tools/run_global_rebalance.py \
  --apply-staged archive/qa/stage-check

# 回归测试
PYTHONDONTWRITEBYTECODE=1 uv run --no-project python -m unittest \
  discover -s tools/subtitle_pipeline -p 'test_*.py'
```

当前有效记录：`archive/qa/current/manifest.json`。当前布局下规范文件为 33 个 blocks、33 个 raw SRT、33 个最终 SRT，共 66 个双语管线的规范文件；视频 MP4 不计入 QA 数量。
