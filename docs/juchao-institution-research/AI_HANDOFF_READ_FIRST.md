# AI 接手请先读

本文件是巨潮机构调研归档项目的稳定入口。旧版 `AI_HANDOFF.md` 保留为历史设计与问题记录；当前状态以本文件、`AI_HANDOFF_CURRENT.md`、`DRIVE_VALIDATION_RESULT.md` 和 `main` 实际代码为准。

## 当前基线

- 正式分支：`main`
- Apps Script 后端代码提交：`9c915f67fa996868e59d9e7b1ad8dce47df5a1a7`
- Apps Script 工作流与配置文档提交：`cf48f5ad67dd3b8bdd1311839934b6ea82a50062`
- GitHub 托管代码验证：<https://github.com/zencolab/stock/actions/runs/31599658796>
- Apps Script Drive 真实验收：<https://github.com/zencolab/stock/actions/runs/31603784249>

实际 HEAD 会随文档及后续修复变化，始终以 `main` 最新提交为准。

## 当前结论

1. 巨潮查询、沪深 A 股筛选、排除北交所、PDF/DOC/DOCX 下载和清单已实现。
2. GitHub Actions 手动运行、每日 7 日回看及历史按月回填已实现。
3. Google Drive 支持 Apps Script、OAuth、WIF 三种后端。
4. 个人 My Drive 的 Apps Script Web App 已完成真实授权与连通验证。
5. 第一次真实运行成功创建 3 个 Drive 文件；相同参数第二次运行成功跳过 3 个，Drive 失败 0，证明幂等去重有效。
6. 两个 Apps Script GitHub Secrets 已配置并正常工作；任何文档和报告均未记录 Secret 值。

## 阅读顺序

1. `docs/juchao-institution-research/AI_HANDOFF_CURRENT.md`
2. `docs/juchao-institution-research/DRIVE_VALIDATION_RESULT.md`
3. `docs/juchao-institution-research/APPS_SCRIPT_SETUP.md`
4. `.github/workflows/fetch.yml`
5. `.github/workflows/backfill.yml`
6. `apps-script/Code.gs`
7. `src/phase1.py`
8. `src/pipeline.py`
9. `src/apps_script_storage.py`
10. `src/drive_storage.py`
11. `scripts/self_check.py`

## 不得改变的范围

- 只处理沪市、深市 A 股；
- 排除北交所；
- 必须支持 PDF、DOC、DOCX 原件；
- 不执行 Word 宏或嵌入对象；
- `main` 是正式事实来源；
- 未实际执行的测试不得宣称通过；
- 不得把 Web App URL、上传令牌或任何 Secret 写入代码、日志、Issue、Artifact 或聊天。
