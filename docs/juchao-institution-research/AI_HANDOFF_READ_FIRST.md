# AI 接手请先读

本文件是 `AI_HANDOFF.md` 的稳定入口，用于避免文档提交本身改变 `main` HEAD 后产生误解。

## 版本说明

- 正式分支：`main`
- 最后完成离线验证的代码基线：`e8ca2cac4844ec738177aac25000253c2abf3397`
- 完整交接文档首次提交：`aa40c62202269ab7f3ab501eda78cac9acf97ad5`
- 实际 HEAD 会随着文档和后续修复变化，以 `git rev-parse HEAD` 为准。

确认当前 HEAD 包含完整交接文档：

```bash
git checkout main
git pull
git merge-base --is-ancestor aa40c62202269ab7f3ab501eda78cac9acf97ad5 HEAD
```

最后一条命令退出码应为 0。

## 阅读顺序

1. `docs/juchao-institution-research/AI_HANDOFF_READ_FIRST.md`
2. `docs/juchao-institution-research/AI_HANDOFF.md`
3. `.github/workflows/fetch.yml`
4. `src/phase1.py`
5. `src/pipeline.py`
6. `src/drive_storage.py`
7. `scripts/self_check.py`

## 接手后的第一任务

不要先改代码。先取得用户最新 GitHub Actions 运行 URL、第一个红色步骤和末尾 30～50 行错误日志，再按最早失败项修复。不要向用户索取 Secret 值。

## 不得改变的范围

- 只处理沪市、深市 A 股；
- 排除北交所；
- 必须支持 PDF、DOC、DOCX；
- `main` 是唯一事实来源；
- 未实际执行的测试不得宣称通过。
