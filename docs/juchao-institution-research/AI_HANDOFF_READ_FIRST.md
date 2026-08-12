# AI 接手请先读

本文件是巨潮机构调研归档项目的稳定入口。旧版 `AI_HANDOFF.md` 保留为历史设计与问题记录；当前状态以本文件、`AI_HANDOFF_CURRENT.md` 和 `main` 实际代码为准。

## 当前基线

- 正式分支：`main`
- Apps Script 后端代码提交：`9c915f67fa996868e59d9e7b1ad8dce47df5a1a7`
- Apps Script 工作流与文档提交：`cf48f5ad67dd3b8bdd1311839934b6ea82a50062`
- GitHub 托管验证运行：`https://github.com/zencolab/stock/actions/runs/31599658796`
- 托管验证结果：install、compile、workflow YAML、Ruff、pytest、self-check、巨潮真实下载探针全部成功；`28 passed`；真实下载 3 个 PDF，失败 0。

实际 HEAD 会随文档及后续修复变化，始终以 `main` 最新提交为准。

## 当前结论

1. 巨潮查询、沪深 A 股筛选、排除北交所、PDF/DOC/DOCX 下载和清单已实现。
2. GitHub Actions 手动运行、每日 7 日回看及历史按月回填已实现。
3. Google Drive 支持三种后端，优先级为 Apps Script、OAuth、WIF。
4. 个人 My Drive 推荐 Apps Script，只需两个 GitHub Secrets，无需 Google Cloud OAuth 配置。
5. Apps Script 客户端、服务端脚本语法和模拟上传已验证；真实 Drive 上传仍必须由 Google 账号所有者先部署 Web App 并授权 Drive。

## 阅读顺序

1. `docs/juchao-institution-research/AI_HANDOFF_CURRENT.md`
2. `docs/juchao-institution-research/APPS_SCRIPT_SETUP.md`
3. `.github/workflows/fetch.yml`
4. `.github/workflows/backfill.yml`
5. `apps-script/Code.gs`
6. `src/phase1.py`
7. `src/pipeline.py`
8. `src/apps_script_storage.py`
9. `src/drive_storage.py`
10. `scripts/self_check.py`

## 不得改变的范围

- 只处理沪市、深市 A 股；
- 排除北交所；
- 必须支持 PDF、DOC、DOCX 原件；
- 不执行 Word 宏或嵌入对象；
- `main` 是正式事实来源；
- 未实际执行的测试不得宣称通过；
- 不得把 Web App URL、上传令牌或任何 Secret 写入代码、日志、Issue、Artifact 或聊天。
