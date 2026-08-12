# 巨潮机构调研归档系统｜AI 接手文档

> 用途：把项目现状、接口、运行方式、已知错误和后续任务完整转交给下一位 AI。  
> 更新时间：2026-08-12 11:14（Asia/Shanghai）  
> 仓库：`zencolab/stock`  
> 当前正式分支：`main`  
> 当前 `main` HEAD：`e8ca2cac4844ec738177aac25000253c2abf3397`

---

## 1. 一句话说明

本项目从巨潮资讯网查询上市公司机构调研／投资者关系活动公告，只保留沪市、深市 A 股，排除北交所，下载 PDF、DOC、DOCX 原件，生成审计清单，并可将原件幂等归档到 Google Drive。GitHub Actions 提供手动执行、每日 7 日回看和历史按月回填。

## 2. 接手时必须先知道的结论

1. **以 `main` 为唯一事实来源。** 功能分支 `feat/juchao-institution-research` 仍保留，但已落后且与 `main` 分叉。
2. 项目曾因 GitHub 授权无法创建 PR，最终采用直接提交文件到 `main`，因此没有一条真正的 Git merge 记录。
3. 用户多次报告 GitHub Actions 手动运行报错，但没有提供运行 URL、红色步骤或错误日志。
4. 当前连接权限无法读取 Actions 运行日志、无法列 Issue、无法创建 PR；不要继续猜错误，必须先拿到最新运行的第一个失败步骤和日志。
5. 本地已完成语法、YAML、Shell 和离线端到端验证；**不能据此宣称 GitHub 托管运行或 Google Drive 真实上传已经通过。**
6. Google Drive 尚未完成真实凭据配置和真实上传验收。

## 3. 不得改变的业务范围

- 数据源：巨潮资讯网。
- 文档主题：机构调研、投资者关系活动记录。
- 市场：只保留沪市、深市 A 股。
- 明确排除：北交所代码，包括 `4xxxxx`、`8xxxxx`、`920xxx`。
- 原始格式：PDF、DOC、DOCX，必须保留原件，不强制转 PDF。
- 不执行 Word 宏、脚本和嵌入对象。
- 附件只能来自巨潮官方域名白名单。
- 当前项目不负责 OCR、摘要、投资建议或问答分析。

## 4. 三阶段目标与当前状态

| 阶段 | 目标 | 当前状态 |
| --- | --- | --- |
| 第一阶段 | 公告发现、筛选、分页、PDF/DOC/DOCX 下载、清单 | 代码完成；离线验证通过；线上运行仍需日志确认 |
| 第二阶段 | Google Drive 认证、目录、幂等上传、版本管理 | 代码完成；未做真实 Drive 集成验收 |
| 第三阶段 | 手动运行、每日回看、历史回填、Artifact、告警 | 代码完成；托管 Actions 最终状态未知 |

## 5. 当前仓库结构

```text
.github/workflows/
├── fetch.yml                    # 手动、每日任务、Push/PR 验证
├── backfill.yml                 # 历史按月回填
├── daily-report.yml             # 仓库原有股票日报，与本项目无关
└── report-by-date.yml           # 仓库原有按日期日报，与本项目无关

docs/juchao-institution-research/
├── README.md
├── PHASES.md
├── GOOGLE_DRIVE_SETUP.md
└── AI_HANDOFF.md                # 本文

scripts/
├── backfill_matrix.py           # 日期区间拆自然月
└── self_check.py                # 无网络端到端自检

src/
├── phase1.py                    # 巨潮查询、筛选、下载、格式检测、清单
├── pipeline.py                  # 完整流水线编排
├── drive_storage.py             # Google Drive API 与认证
├── main.ts                      # 仓库原有代码，与本项目无关
├── report-by-date.ts            # 仓库原有代码，与本项目无关
└── lib/                         # 仓库原有代码

tests/
├── test_phase1.py
├── test_drive_storage.py
└── test_pipeline.py

requirements-juchao.txt          # 本流程精简依赖；Actions 应使用此文件
requirements.txt                 # 较宽的历史依赖；不要用于本流程 CI
```

## 6. 总体数据流

```text
GitHub workflow / CLI
        ↓
解析日期、市场、数量、Drive 开关
        ↓
POST 巨潮公告查询接口，按月、关键词、完整分页
        ↓
标题过滤 + 股票代码市场过滤 + 公告 ID 去重
        ↓
下载官方附件
        ↓
文件头/容器结构检测 PDF、DOC、DOCX
        ↓
SHA-256 + 清单 + 失败记录 + 隔离目录
        ↓（可选）
Google Drive 目录创建与幂等 upsert
        ↓
GitHub Artifact + Drive 运行清单
```

## 7. 外部接口

### 7.1 巨潮公告查询接口

- 方法：`POST`
- URL：`http://www.cninfo.com.cn/new/hisAnnouncement/query`
- 请求超时：25 秒
- 重试：最多 5 次，指数退避
- 节流：默认最少约 0.8 秒并加随机抖动

核心表单字段：

```json
{
  "pageNum": 1,
  "pageSize": 30,
  "column": "",
  "tabName": "fulltext",
  "plate": "",
  "stock": "",
  "searchkey": "投资者关系活动记录表",
  "secid": "",
  "category": "",
  "trade": "",
  "seDate": "2025-03-01~2025-03-31",
  "sortName": "",
  "sortType": "",
  "isHLtitle": "true"
}
```

查询关键词：

- `投资者关系活动记录表`
- `机构调研`

分页终止条件：

- `announcements` 为空；或
- `hasMore` 为假；或
- 达到 `MAX_PAGES=400`，此时任务应失败，避免静默截断。

### 7.2 巨潮附件接口

相对附件路径会拼接：

```text
http://static.cninfo.com.cn/
```

允许的最终域名：

- `static.cninfo.com.cn`
- `dataclouds.cninfo.com.cn`
- `www.cninfo.com.cn`

重定向后的最终 URL 也必须再次通过白名单检查。

### 7.3 文件格式检测

| 格式 | 检测方式 |
| --- | --- |
| PDF | 文件头 `%PDF` |
| DOC | OLE magic `D0CF11E0A1B11AE1` |
| DOCX | ZIP 中同时存在 `[Content_Types].xml` 和 `word/document.xml` |

未知、HTML 错误页、空文件进入 `quarantine/`，任务记录失败。

### 7.4 Google Drive API

- API：Google Drive API v3
- 查询／文件元数据：`https://www.googleapis.com/drive/v3`
- Resumable upload：`https://www.googleapis.com/upload/drive/v3`
- 默认 scope：`https://www.googleapis.com/auth/drive`

支持认证：

1. 个人 My Drive OAuth refresh token；
2. Shared Drive + GitHub OIDC + Workload Identity Federation；
3. 静态 `GDRIVE_ACCESS_TOKEN` 仅供特殊运行，不建议长期使用。

Drive 文件 `appProperties`：

```text
cninfo_announcement_id
cninfo_sha256
cninfo_stock_code
cninfo_publish_date
cninfo_format
cninfo_version
```

幂等规则：

- 同公告 ID、同 SHA-256：`skipped`。
- 同公告 ID、不同 SHA-256：更新同一文件，版本加一。
- 找不到公告 ID：创建文件。

## 8. 命令行接口

### 8.1 第一阶段入口

```bash
python -m src.phase1 \
  --start 2025-03-07 \
  --end 2025-03-07 \
  --market all \
  --download-files \
  --max-files 3 \
  --output artifacts/juchao-phase1
```

参数：

| 参数 | 必填 | 含义 |
| --- | --- | --- |
| `--start` | 是 | 开始日期，`YYYY-MM-DD` |
| `--end` | 是 | 结束日期，`YYYY-MM-DD` |
| `--market` | 否 | `all`、`sh`、`sz` |
| `--download-files` | 否 | 下载原件，否则只生成公告清单 |
| `--max-files` | 否 | 0 不限制 |
| `--output` | 否 | 输出目录 |
| `--verbose` | 否 | DEBUG 日志 |

`src.phase1` 单次日期区间限制为 31 天。

### 8.2 完整流水线入口

```bash
python -m src.pipeline \
  --start 2025-03-07 \
  --end 2025-03-07 \
  --market all \
  --download-files \
  --max-files 3 \
  --output artifacts/juchao-run
```

可选 Drive：

```bash
python -m src.pipeline \
  --start 2025-03-07 \
  --end 2025-03-07 \
  --market all \
  --download-files \
  --upload-drive \
  --max-files 3
```

完整参数：

| 参数 | 含义 |
| --- | --- |
| `--start` / `--end` | 显式日期区间 |
| `--last-days N` | 最近 N 个自然日；与显式日期二选一 |
| `--market` | `all`、`sh`、`sz` |
| `--download-files` | 下载原件 |
| `--upload-drive` | 下载并上传 Drive |
| `--max-files` | 0 不限制 |
| `--output` | 输出根目录 |
| `--run-id` | 运行 ID，默认 UTC 时间戳 |
| `--fail-on-empty` | 无结果时返回失败 |
| `--skip-run-manifest-upload` | 不上传运行清单 |
| `--verbose` | DEBUG 日志 |

## 9. GitHub Actions 接口

### 9.1 手动／每日工作流

文件：`.github/workflows/fetch.yml`  
工作流名：`juchao-research-pipeline`

手动输入：

| 输入 | 默认值 | 说明 |
| --- | --- | --- |
| `start` | 无 | 必填日期 |
| `end` | 无 | 必填日期 |
| `market` | `all` | 市场 |
| `upload_drive` | `false` | 未配凭据必须关闭 |
| `max_files` | `3` | 安全小样本 |

验证 job：

1. 安装 `requirements-juchao.txt`；
2. `compileall`；
3. Ruff 只检查 `E9,F63,F7,F82`；
4. pytest；
5. `python -m scripts.self_check`；
6. Push/PR 时做巨潮在线探针，但 `continue-on-error: true`。

归档 job：

- 只在 `workflow_dispatch` 或 `schedule` 执行；
- 每日 cron：`37 13 * * *`，即北京时间 21:37；
- 定时任务回看最近 7 个自然日；
- 无 Drive 凭据时定时任务降级为 Artifact-only；
- 手动勾选 Drive 但凭据不全时，在 preflight 明确失败。

### 9.2 历史回填工作流

文件：`.github/workflows/backfill.yml`  
工作流名：`juchao-history-backfill`

- 手动输入开始、结束日期；
- `scripts/backfill_matrix.py` 按自然月生成 matrix；
- `max-parallel: 1`；
- `fail-fast: false`；
- 默认每月只处理 3 个文件，0 表示无限制；
- Drive 默认关闭。

## 10. 环境变量与 GitHub Secrets

### 10.1 个人 OAuth

```text
GDRIVE_OAUTH_CLIENT_ID
GDRIVE_OAUTH_CLIENT_SECRET
GDRIVE_OAUTH_REFRESH_TOKEN
GDRIVE_ROOT_FOLDER_ID          # 推荐
```

### 10.2 Shared Drive + WIF

```text
GCP_WORKLOAD_IDENTITY_PROVIDER
GCP_SERVICE_ACCOUNT
GDRIVE_SHARED_DRIVE_ID
GDRIVE_ROOT_FOLDER_ID          # 可选
```

### 10.3 通用变量

```text
GDRIVE_BASE_PATH               # 默认 CNINFO/机构调研
GDRIVE_SCOPE                   # 默认 full drive scope
JUCHAO_MIN_INTERVAL            # 默认 0.8 秒
```

任何 Secret 都不得写入代码、日志、Artifact、Issue 或聊天。

## 11. 输出目录与数据契约

```text
artifacts/juchao-run/
├── manifest.json
├── manifest.csv
├── failures.json
├── files/YYYY/YYYY-MM/*.pdf|*.doc|*.docx
├── quarantine/*.bin
└── .tmp/                       # 正常结束后应为空
```

公告核心字段：

```text
schema_version
announcement_id
stock_code
company_name
market
title
publish_date
attachment_url
source_filename
format_hint
format_detected
mime_detected
size_bytes
sha256
local_path
download_status
format_mismatch
source_keyword
fetched_at
```

流水线运行时还会在 JSON 记录中追加：

```text
drive_status
drive_file_id
drive_path
drive_version
drive_web_view_link
uploaded_at
```

状态值：

- `download_status`: `not_requested`、`not_selected`、`ok`、`quarantined`、`failed`
- `drive_status`: `not_requested`、`created`、`updated`、`skipped`、`failed`

## 12. 模块职责与可调用函数

### `src/phase1.py`

- `parse_date(value)`：解析日期。
- `month_slices(start, end)`：按自然月切片。
- `build_payload(start, end, keyword, page)`：生成巨潮表单。
- `query_page(...)`：单页查询与重试。
- `market_from_code(code)`：识别 `sh`、`sz`、`bj`、`other`。
- `resolve_attachment_url(value)`：补全 URL 并验证域名。
- `normalize_announcement(...)`：标题、市场和元数据过滤。
- `iter_query(...)`：完整分页。
- `fetch_range(...)`：多关键词、按月查询与公告 ID 去重。
- `sniff_format(path)`：真实文件格式检测。
- `download_attachment(...)`：下载、校验、哈希、隔离或归档。
- `write_outputs(...)`：写 JSON、CSV 和失败清单。

### `src/drive_storage.py`

- `DriveConfig.from_env()`：读取 Drive 配置。
- `GoogleTokenSource`：OAuth／ADC 令牌加载与刷新。
- `DriveClient.ensure_folder()`：查找或创建目录。
- `DriveClient.ensure_path()`：递归创建目录。
- `DriveClient.upsert_record()`：公告文件幂等上传。
- `DriveClient.upload_run_file()`：上传运行清单。

### `src/pipeline.py`

- `resolve_range(args)`：显式日期或最近 N 天。
- `main(argv)`：查询、下载、Drive、清单和退出码编排。

### `scripts/self_check.py`

无网络验证：

- 市场过滤；
- PDF/DOC/DOCX 检测；
- Drive create/update/skip；
- 完整流水线清单生成；
- 历史月份拆分；
- 日期范围。

## 13. 已执行验证

本地实际执行：

```bash
python3 -m compileall -q src scripts tests
python3 offline_validate.py
```

结果：

```text
Python syntax                 PASS
Workflow YAML                 PASS
Workflow Bash syntax          PASS
market_filter                 PASS
format_detection              PASS
drive_idempotency             PASS
pipeline_orchestration        PASS
backfill_ranges               PASS
pipeline_range                PASS
failures                      0
```

受限项：

- 当前沙箱无法连接 PyPI，因此不能在本地安装并真实运行 Ruff/pytest；
- 最新 GitHub 托管 Actions 状态无法通过当前连接读取；
- Google Drive 没有真实凭据，未做真实上传；
- 巨潮在线请求在本地沙箱没有做最终网络验收。

## 14. 已修复错误

| 错误 | 修复 |
| --- | --- |
| Ruff `E702`，测试中单行多语句 | 改为标准多行语句 |
| Ruff `E704`，Protocol 单行方法体 | 改为多行 `...` |
| 自检脚本按文件运行可能找不到 `src` | 改为 `python -m scripts.self_check` |
| 工作流安装无关重型依赖 | 新增 `requirements-juchao.txt` |
| 在线探针阻断所有 CI | 改为 best-effort、非阻断 |
| Drive 未配置却默认上传 | 手动／回填默认改为 `false` |
| 定时任务无 Drive 凭据时失败 | 改为 Artifact-only |
| Issue API 失败使正常任务变红 | 告警步骤设为 `continue-on-error` |

相关提交：

- `e7518a034f70621d1f3044607bb120a7621cc9ad`
- `14238fcb67df1206ef64b112530883ea19831ef9`
- `54aaac60a9f14710c5a6c460ea1568d8c3e5686a`
- `e8ca2cac4844ec738177aac25000253c2abf3397`

## 15. 当前已知错误与风险

### P0：必须先处理

1. **GitHub Actions 托管错误尚未取得日志。** 用户只报告“报错很多”。下一位 AI 必须先获取最新运行 URL、第一处红色步骤和末尾 30～50 行日志，禁止继续猜测。
2. **GitHub 托管 pytest/Ruff 是否为绿色未知。** 最新 Push 已触发工作流，但当前连接没有 Actions 日志／check-run 读取能力。
3. **巨潮接口可能在 GitHub Runner 上被限流、返回非 JSON、HTML 或 403/429。** 在线探针目前不阻断 CI，但手动归档仍会真实失败并返回非零退出码。
4. **Drive 真实上传未测试。** OAuth refresh token、权限、目标文件夹、Shared Drive 参数均需真实验证。

### P1：功能缺口

1. `manifest.csv` 使用 `src.phase1.MANIFEST_FIELDS`，当前没有 Drive 字段；Drive 字段只会完整出现在 `manifest.json`。
2. Drive 配置失败时，`failures.json` 有 `drive_config`，但各记录的 `drive_status` 可能仍是 `not_requested`，`drive_failed` 汇总可能为 0。
3. Drive 去重查询限制在目标日期目录；如果同一公告 ID 的发布日期元数据改变，可能在另一个日期目录重复创建。
4. Resumable upload 目前仍使用 `read_bytes()` 把整个文件读入内存，不适合超大附件。
5. 巨潮查询仍使用 HTTP；应确认 HTTPS 端点是否稳定可用，并避免无意改变接口行为。
6. 任何一个关键词／月份查询失败都会进入 failures，最终整次流水线返回 1；需确认是否接受“部分成功即失败”的策略。
7. `main` 与功能分支分叉。不要从旧功能分支覆盖 `main` 的最新修复。

### P2：工程改进

1. GitHub Actions 第三方 Action 使用版本标签，尚未固定到 commit SHA。
2. 依赖只设下限，尚未锁定可重现版本。
3. 缺少巨潮响应 fixture／录制契约测试。
4. 缺少真实 OAuth/Drive 沙箱集成测试。
5. 告警依赖 GitHub Issues；仓库当前 API 授权曾返回 403，虽然运行时 `GITHUB_TOKEN` 可能不同，但必须实测。
6. 缺少运行指标的长期保存和趋势统计。

## 16. 常见失败定位表

| 红色步骤 | 优先检查 |
| --- | --- |
| `Install pipeline dependencies` | PyPI 网络、Python 版本、包解析 |
| `Compile all Python files` | 首个 SyntaxError 文件和行号 |
| `Critical static checks` | Ruff 首个 `E9/F63/F7/F82` |
| `Unit tests` | 第一个 pytest failure，不要先修后续错误 |
| `Offline end-to-end self-check` | 模块导入、断言、当前 main 是否包含最新脚本 |
| `Resolve and validate run parameters` | Drive 开关与 Secrets 是否匹配 |
| `Authenticate with Workload Identity Federation` | Provider、服务账号、OIDC 权限 |
| `Run archive pipeline` | `failures.json` 的第一个 stage；巨潮 HTTP／下载／Drive |
| `Upload run artifact` | 上游是否在创建输出前失败 |

## 17. 下一位 AI 的执行顺序

### 第一步：固定现场

```bash
git checkout main
git pull
git rev-parse HEAD
```

必须得到：

```text
e8ca2cac4844ec738177aac25000253c2abf3397
```

如果 HEAD 更新，重新阅读差异并以更新后的 `main` 为准。

### 第二步：拿到真实错误

向用户索取：

1. 最新 Actions 运行 URL；
2. 红色 job 和 step 名称；
3. 第一条错误及末尾 30～50 行日志；
4. 本次输入参数，特别是 `upload_drive`。

不要索取或让用户粘贴 Secret 值。

### 第三步：在有网络的开发环境执行

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-juchao.txt
python -m compileall -q src scripts tests
ruff check --select E9,F63,F7,F82 \
  src/phase1.py src/drive_storage.py src/pipeline.py \
  scripts/backfill_matrix.py scripts/self_check.py \
  tests/test_phase1.py tests/test_drive_storage.py tests/test_pipeline.py
pytest -q tests/test_phase1.py tests/test_drive_storage.py tests/test_pipeline.py
python -m scripts.self_check
```

### 第四步：先验证无 Drive 小样本

```bash
python -m src.pipeline \
  --start 2025-03-07 \
  --end 2025-03-07 \
  --market all \
  --download-files \
  --max-files 3 \
  --output artifacts/manual-smoke
```

验收：

- 退出码 0；
- 三个清单文件存在；
- 至少一个原件或有可解释的零结果；
- `failures.json` 为空；
- 文件格式检测与后缀一致或正确记录 mismatch。

### 第五步：配置并验证 Drive

1. 只选择 OAuth 或 WIF 其中一种；
2. `max_files=3`；
3. 第一次运行应 `created=3`；
4. 相同参数第二次运行应 `skipped=3`；
5. 修改一个测试文件哈希时应 `updated=1`、版本加一；
6. 检查 `_runs` 中清单。

### 第六步：修复后走分支和 PR

不要继续直接推 `main`。建议：

```text
fix/juchao-actions-<issue>
```

创建 PR，附：

- 真实错误日志摘要；
- 根因；
- 修改；
- 本地命令结果；
- Actions 结果；
- 是否影响 Drive 和历史数据。

如果权限仍不允许创建 PR，先明确告知用户，再决定是否直接提交。

## 18. 完成验收标准

只有全部满足才可宣布项目完成：

- [ ] `main` 最新 Push 验证全部绿色；
- [ ] 手动 `upload_drive=false` 小样本成功并能下载 Artifact；
- [ ] 沪深 A 股保留，北交所样本被排除；
- [ ] PDF、DOC、DOCX 各至少一个真实样本通过；
- [ ] HTML／未知格式进入 quarantine；
- [ ] OAuth 或 WIF 真实认证成功；
- [ ] Drive 第一次 created，第二次 skipped；
- [ ] 同 ID 不同哈希触发 updated；
- [ ] 历史回填至少跨两个月成功；
- [ ] 定时任务连续至少 3 天成功；
- [ ] 无 Secret 出现在日志和 Artifact；
- [ ] README、Drive 配置文档与代码一致。

## 19. 给下一位 AI 的直接提示词

```text
你接手 zencolab/stock 的巨潮机构调研归档项目。

只以 main 为事实来源，当前记录的 HEAD 是
 e8ca2cac4844ec738177aac25000253c2abf3397。
先阅读 docs/juchao-institution-research/AI_HANDOFF.md，
再阅读 .github/workflows/fetch.yml、src/phase1.py、src/pipeline.py、
src/drive_storage.py 和 scripts/self_check.py。

不要重写项目，不要扩大业务范围，不要加入北交所，不要只支持 PDF。
第一任务不是猜错，而是拿到用户最新 GitHub Actions 运行 URL、
第一个红色步骤和错误日志。按“最早失败优先”修复。

修复后必须执行 compileall、关键 Ruff、pytest、离线 self_check、
无 Drive 的 3 文件小样本，再做真实 Drive create/skip/update 验收。
不要声称未实际执行的测试已经通过，不要让用户把 Secret 粘贴到聊天。
优先通过新分支和 PR 提交；若权限阻止，明确说明。
```

## 20. 重要链接

- 仓库：`https://github.com/zencolab/stock`
- 手动／每日工作流：`https://github.com/zencolab/stock/actions/workflows/fetch.yml`
- 历史回填：`https://github.com/zencolab/stock/actions/workflows/backfill.yml`
- 当前 main 提交：`https://github.com/zencolab/stock/commit/e8ca2cac4844ec738177aac25000253c2abf3397`
- 旧功能分支最新提交：`https://github.com/zencolab/stock/commit/faba7a1dd7e6ddf6b61b32bee22d8131aed3746c`

---

**交接原则：先取得真实线上错误，再修最早失败；先无 Drive 小样本，再做真实 Drive；未实际通过的步骤不得写成已完成。**
