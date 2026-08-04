# A股涨跌幅报告项目 — 完整交接文档（v3，2026-08-04）

> **阅读者**：接手本项目的下一个 AI（或开发者）。
> **状态**：一、二、三期全部功能已在真实数据下验证通过（本地完整实跑 + GitHub Actions 实跑）。本文档是唯一权威交接文档，包含项目结构、数据源接口文档、代码模块接口、口径说明、坑与勘误、四期路线建议。
> **沟通约定**：用户全程使用中文，回复请用中文。用户对「声称完成但实际没验证」非常敏感——**没有真实运行证据前，绝不说功能已就绪**。用户不熟悉 GitHub 界面，需要用户手动操作时给出精确页面路径与直达链接。
>
> **三条铁律**：①`src/main.ts` 绝不覆盖；②任何改动必须真实数据完整实跑验证后才算完成；③报告口径说明必须与实现一致（诚实性原则，见 §6）。

---

## 1. 项目概述与需求全貌

每个交易日对沪深 A 股全市场排序，生成「涨幅榜前100 + 跌幅榜前100」的自包含 HTML 报告，每股一张卡片。

| 期数 | 需求 | 实现 | 状态 |
| --- | --- | --- | --- |
| 一期 | 每日收盘后自动生成当日报告，Actions 定时运行并提交回仓库 | `src/main.ts` + `daily-report.yml` | ✅ 上线（有隐患，见 §8 路线图 P1） |
| 二期 | 输入**任意历史交易日**生成同样报告（全市场逐只回算，不依赖榜单快照） | `src/report-by-date.ts` | ✅ 已验证 |
| 三期 | 每卡片新增：①当日走势图 + 所属市场板块指数走势图；②所属概念板块（板块走势图 + 成分股涨跌）；③龙虎榜标记与席位买卖明细 | `src/lib/enrich.ts` + `src/lib/enrich-run.ts`，接入 report-by-date.ts（升级 v3） | ✅ 已验证（2026-08-04） |

卡片内容全景：行情（开收/成交额/换手/五日换手）、市值（推算）、前十大流通股东、近半年走势 SVG、**当日走势图、市场指数走势图、概念板块标签 + 展开最强 2 个概念（板块走势图 + 涨跌家数 + 领涨领跌前10）、龙虎榜（上榜原因 + 买卖前5席位）**。

---

## 2. 项目结构

仓库：`zencolab/stock`（**Private**，默认分支 `main`）

```
stock/
├── src/
│   ├── main.ts                      # 一期：每日报告（东财实时榜单快照，只能当日）。★绝不修改★
│   ├── report-by-date.ts            # 二/三期核心：指定日期报告 v3（全市场回算 + 三期增强接入）
│   └── lib/
│       ├── enrich.ts                # 三期数据层：分时/日K/板块/龙虎榜取数 + SVG 渲染（纯函数，零依赖）
│       └── enrich-run.ts            # 三期编排渲染层：200只批量增强、板块全局缓存、HTML片段/CSS/meta口径
├── .github/workflows/
│   ├── daily-report.yml             # 定时：cron '30 8 * * 1-5'（UTC）= 北京 16:30 周一至五，跑 main.ts
│   └── report-by-date.yml           # 手动：workflow_dispatch 带 date 输入，跑 report-by-date.ts
├── reports/
│   ├── YYYY-MM-DD.html              # 产出报告（v2 约 800KB；v3 历史日期约 2.1MB、当日约 4.2MB）
│   └── latest.html                  # 仅由每日任务(main.ts)写入。report-by-date 绝不写此文件
├── docs/DEVELOPMENT.md              # 本文档
├── README.md / README-by-date.md    # 使用说明
├── .gitignore                       # 含 *.log —— 坑，见 §7
└── stocksucess                      # 历史遗留标记文件，无作用
```

历史遗留：`test/report-by-date` 分支及其上的 `bunfig.toml`、`test/net-hardening.ts` 是一代调试遗物，已无用处，可随时删除（勿合并该分支）。

**运行时**：Bun（Actions 用 `oven-sh/setup-bun@v2`，实测 1.3.14）。直接执行 `.ts`，无构建步骤，**零第三方依赖**（图表全部为手写内联 SVG）。

**本地运行**：
```bash
bun src/report-by-date.ts 2026-07-31          # 或 REPORT_DATE=2026-07-31 bun src/report-by-date.ts
# 环境变量：CONCURRENCY 回算并发（默认8）
```

---

## 3. 数据源接口文档（★ 最重要的工程知识）

**核心事实：东方财富 K 线域名 `push2his.eastmoney.com`（含全部数字镜像）对数据中心网络（GitHub Actions、云沙箱）不可靠。** 表现为瞬间拒连或 502，偶有放行窗口——2026-08-04 实测：首轮探测全 200，数分钟后（十余个快速请求触发）全镜像 502，冷却后仍 502。**任何「今天试了能通」都不构成可用证据**。因此：个股/指数 K 线主源必须是腾讯；板块历史 K 线只能把 push2his 当机会源并熔断降级。

所有请求带浏览器 `User-Agent` 和对应 `Referer`（腾讯 `https://gu.qq.com/`；东财行情 `https://quote.eastmoney.com/`；东财数据中心 `https://data.eastmoney.com/`；股东 `https://emweb.securities.eastmoney.com/`）。均为公开接口，无需鉴权。

### 3.1 腾讯日K（个股/指数主源）✅ 稳定

```
GET https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?param={sym},day,{beg},{end},{count},{fq}
备用域名（同参数，代码自动轮换）：https://web.ifzq.gtimg.cn/appstock/app/newfqkline/get
```
- `sym`：`sh600000` / `sz000001` / 指数 `sh000001`、`sz399001`、`sz399006`、`sh000688`
- `beg`/`end`：`YYYY-MM-DD`；`fq`：空=不复权（返回 `data[sym].day`）、`qfq`=前复权（返回 `qfqday`）
- 行格式：`[0]日期 [1]开盘 [2]收盘 [3]最高 [4]最低 [5]成交量(手) [6]信息对象 [7]换手率% [8]成交额(万元) …`
- **注意**：成交额/换手率只在**不复权**行；指数行与部分老数据缺第 7/8 列（指数只读日期/收盘）。

### 3.2 腾讯当日分时 ✅ 稳定（**只有今天，无历史**）

```
GET https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={sym}
```
- 返回 `data[sym].data.date`（YYYYMMDD，**必须校验等于目标日**）与 `data.data`：`["0930 9.56 2354 2250424.00", …]`（时间 价格 累计量 累计额）
- 昨收在 `data[sym].qt[sym][4]`（腾讯 qt 标准列位）
- `day/query?date=` 的 date 参数被忽略，**没有历史分时**——别再浪费时间找。

### 3.3 东财 push2delay 系列 ✅ 稳定

| 用途 | 接口 | 说明 |
| --- | --- | --- |
| 全市场名单 | `push2delay.eastmoney.com/api/qt/clist/get?fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23&fields=f12,f14,f38,f39&pn={页}&pz=100` | 代码/名称/总股本(f38)/流通股本(f39)。镜像 `push2`、`92.push2` 可轮换 |
| 概念板块全表 | 同上 `fs=m:90+t:3&fields=f12,f14`（约503个） | f12=BK代码 f14=名称 |
| 个股所属板块 | `…/api/qt/slist/get?spt=3&secid={1.|0.}{code}&fields=f12,f14,f3&pz=50` | 沪`1.`深`0.`前缀。**混含概念/地域/行业/风格**，须与概念全表取交集过滤；f3=板块当日实时涨幅 |
| 板块成分股 | `…/api/qt/clist/get?fs=b:{BK代码}&fields=f12,f14,f3&pn={页}&pz=100` | 今日快照口径；**含北交所成员** |
| 板块当日分时 | `…/api/qt/stock/trends2/get?secid=90.{BK代码}&ndays=1&fields1=f1,f2,f3,f4,f5,f8&fields2=f51,f53` | 只有当天；`data.trends`=["2026-08-04 09:30,7174.28",…]，`data.preClose`=昨收 |
| ⚠️ 东财延时K线 | `push2delay` 的 `stock/kline/get` | 可达但 `klines` **恒为空数组**，不可用 |

### 3.4 东财数据中心 datacenter ✅ 稳定，可回溯历史

| 用途 | 接口 | 说明 |
| --- | --- | --- |
| 前十大流通股东 | `datacenter.eastmoney.com/securities/api/data/v1/get?reportName=RPT_F10_EH_FREEHOLDERS&filter=(SECUCODE="600000.SH")…` | **偶发返回空 result**，重试+串行补漏即可（实现见 report-by-date.ts `fetchHolders`） |
| 龙虎榜当日全表 | `datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_DAILYBILLBOARD_DETAILSNEW&columns=ALL&filter=(TRADE_DATE='2026-07-31')&pageSize=200&pageNumber={页}&source=WEB&client=WEB` | 关键字段：SECURITY_CODE/SECURITY_NAME_ABBR/EXPLANATION(上榜原因)/BILLBOARD_NET_AMT(净买,元)/BILLBOARD_DEAL_AMT(榜单成交,元)/DEAL_AMOUNT_RATIO(占总成交%)。**一股可因多榜单原因多行**；含北交所 |
| 龙虎榜席位明细 | 同上，reportName=`RPT_BILLBOARD_DAILYDETAILSBUY` / `RPT_BILLBOARD_DAILYDETAILSSELL`，filter 加 `(SECURITY_CODE="000009")` | 字段：OPERATEDEPT_NAME(营业部)/BUY/SELL/NET(元)/RANK/EXPLANATION。多榜单原因会重复席位，**按（营业部+买+卖）去重**后取前5 |

### 3.5 板块历史日K ❌ 机会源（唯一取历史板块K线的通道）

```
GET https://92.push2his.eastmoney.com/api/qt/stock/kline/get?secid=90.{BK代码}&klt=101&fqt=0&beg={YYYYMMDD}&end={YYYYMMDD}&fields1=f1,f2,f3&fields2=f51,f53
```
放行窗口假象见本节开头。代码中单次尝试、快速失败、连败 5 次全局熔断（`fetchBoardKline`）。**报告绝不能依赖它**——取不到就无图+注明。备选思路（未实现）：同花顺 `d.10jqka.com.cn/v6/line/48_{881xxx}/01/last.js` 实测可达，但板块体系与东财 BK 不互通，需自建映射，见 §8。

### 3.6 已证实不可用

新浪 `hq.sinajs.cn`（403）；东财 `push2his` 主域名个股K线（拒连，代码中仅作兜底、连败>20熔断）。

---

## 4. 代码模块接口文档

### 4.1 `src/report-by-date.ts`（v3 主脚本，管线 6 阶段）

```
0) 参数校验（argv[2] 或 REPORT_DATE/DATE；拒绝未来日期，按北京时间）
1) 交易日校验：上证指数当日必须有K线（区分"非交易日"与"接口失败"两种退出）
2) 全市场名单：clist 分页取全（约5545只，沪深四板块，不含北交所）
3) 全市场回算（并发 CONCURRENCY=8）：每只腾讯不复权K（开收/成交额/换手/近5日换手/bars数）
   + 前复权K（当日涨跌幅=前复权收盘环比，官方口径，除权除息日准确——勿改回不复权环比）
   + 上市首日兜底东财官方涨跌幅；换手缺失用 成交量÷流通股本 推算并标 *
   → 有效样本<100 即中止不产出。排序取涨/跌各前100
3.5) 三期增强（见 4.3 buildEnrichment）：isToday = DATE===今天(北京)；pctMap=全市场code→当日涨跌幅
4) 入选200只明细（并发6）：半年前复权收盘（画图）+ 前十大流通股东（报告期≤指定日期的最新一期，
   不用未来数据；偶发空响应有补漏机制：首轮重试2次，跑完后串行补漏间隔800ms再试3次）
5) 生成 HTML：只写 reports/${DATE}.html（绝不碰 latest.html）
```

关键类型 `DayRow`：`{code,name,total,float,close,open,amount,pct,pctSrc,turn,turnEst,turn5[],bars,err}`。
`bars ≤ 20` 判定为新股（无股东数据是事实不是 bug，有专门文案）。

v3 对 v2 的全部改动仅 6 处（git diff 可见）：头注释、import enrich-run、阶段3.5 调用、card() 内插入 `renderEnrichHtml`、`<style>` 内插入 `enrichCss()`、meta 区插入 `enrichRes.metaNote`。

### 4.2 `src/lib/enrich.ts`（数据层，纯函数，全部可独立测试）

| 导出 | 签名 | 说明 |
| --- | --- | --- |
| `getJSON` | `(url, tries=3, timeoutMs=20000) → Promise<any>` | 带重试；按域名自动配 Referer；**每次重试新建 AbortSignal**（复用是历史隐藏bug，勿回退）；失败 throw |
| `fetchMinute` | `(sym) → Promise<MinuteData\|null>` | 当日分时（个股/指数通用）。`MinuteData={date,points[{t,price}],prevClose}`。**调用方必须校验 date===目标日** |
| `marketIndexOf` | `(code) → {sym,name,board}` | 688/689→科创50 sh000688；300/301/302→创业板指 sz399006；6xx→上证指数 sh000001；其余→深证成指 sz399001 |
| `fetchDailyCloses` | `(sym, beg, end, count=320) → Promise<KBar[]>` | 腾讯不复权日K，只取日期+收盘；双域名轮换；失败返回 [] |
| `fetchConceptTable` | `() → Promise<Map<BK,名称>>` | 概念板块全表（约503个），分页取全 |
| `fetchStockBoards` | `(code) → Promise<BoardInfo[]>` | 个股所属板块（slist），`{bk,name,chg}`，chg 为**今日实时**板块涨幅 |
| `fetchBoardMembers` | `(bk) → Promise<BoardMember[]>` | 板块成分股（今日快照），`{code,name,chg}` |
| `fetchBoardTrends` | `(bk) → Promise<MinuteData\|null>` | 板块当日分时（trends2） |
| `fetchBoardKline` | `(bk, beg, end) → Promise<KBar[]>` | ❌机会源：92.push2his→push2his 各单次尝试(8s超时)；连败5次全局熔断后恒返 []。`boardKlineBreakerOpen()` 查熔断态 |
| `fetchLhbList` | `(date) → Promise<Map<code, LhbEntry[]>>` | 当日龙虎榜全表。`LhbEntry={code,name,explanation,netBuy,lhbAmount,amountRatio}` |
| `fetchLhbSeats` | `(code, date) → Promise<{buy,sell: LhbSeat[]}>` | 席位明细，按RANK排序、（营业部+买+卖）去重、各取前5。`LhbSeat={dept,buy,sell,net}`（元） |
| `minuteSVG` | `(md, title) → string` | 300×80 分时折线 + 昨收虚线基准；颜色按收盘对昨收（涨 #c0392b 跌 #1e8449）；x轴按241分钟满刻度 |
| `klineWindowSVG` | `(bars, targetDate, title) → string` | ±30交易日收盘折线，目标日竖虚线+圆点标记；**targetDate 必须精确存在于 bars**，否则返回空串 |

### 4.3 `src/lib/enrich-run.ts`（编排渲染层）

| 导出 | 签名 | 说明 |
| --- | --- | --- |
| `buildEnrichment` | `(opts: BuildOpts) → Promise<EnrichResult>` | 三期全部取数编排。`BuildOpts={date,isToday,selected[{code,name}],pctMap,concurrency=6}`；`EnrichResult={map: Map<code,CardEnrich>, metaNote, stats}` |
| `renderEnrichHtml` | `(map, code) → string` | 单卡片增强区 HTML（`<div class="enrich">`），插在 `.cbody` 之后 |
| `enrichCss` | `() → string` | 增强区样式，拼入 `<style>` |

**内部机制（改造前必读）**：
- **双路径**：`isToday=true` → 个股/指数/板块全用真分时（稳定源）；`false` → 个股/指数用 ±30 交易日窗口图（个股需**额外一次**日K请求取 date-75d~date+65d），板块走机会源。分时取回后仍校验 `date` 字段，不符自动落到窗口图。
- **历史口径零额外请求**：板块涨幅（成分股算术平均，标 \*）与成分股涨跌全部查 `pctMap`（全市场回算已算出）；北交所等不在样本的成员计"无数据"。今天口径：板块涨幅=slist 实时值、成员涨跌=clist 实时值。
- **全局缓存**：成分股 `Map<bk,Promise>`、板块走势图 `Map<bk,Promise>`、指数图 4 个——200 只股票概念高度重叠，板块级请求只发一次。
- **展开选择**：个股全部概念列名（标签）；剔除 `EXPAND_BLOCK` 正则命中的泛化/成份/打板类（融资融券、沪股通、MSCI、百元股、大盘成长、昨日\*、最近多板、深成500 等）后，按报告日板块涨幅取最强 2 个展开（要求成分≥3且涨幅非空）。
- **龙虎榜**：全表 1 次 → 入选∩上榜逐只取席位（并发3+200ms间隔）。多上榜原因去重展示。
- **失败全部降级不中断**：每个子功能独立 try/catch，卡片内/meta 区注明原因。`metaNote` 由实际运行状态动态拼装——**渲染与口径说明天然同步**。

**卡片增强区 HTML 结构**（改样式/布局时参照）：
```
<div class="enrich">
  <div class="charts2"> <div class="chartbox">个股图+label</div> <div class="chartbox">指数图+label</div> </div>
  <div class="cptbox"> 标题(N个,展开M个) → .chips 概念标签 → .bkpanel×M
      （.bkhead 板块名+涨幅+涨跌家数 → 板块图/.nochart → .bkmem 领涨/领跌各前10 .bkt 表）</div>
  <div class="lhbbox"> 龙虎榜：.lhbtag 上榜标记 / .lhbreason 原因 / 成交概要 / .seats 买卖前5 .seatt 表；
      未上榜显示"当日未登上龙虎榜"</div>
</div>
```

### 4.4 `src/main.ts`（一期，★不可修改★）

依赖东财实时榜单快照 + push2his K线（有拒连风险，等于每天赌放行窗口），且存在 AbortSignal 复用缺陷。**已知隐患但用户未授权修改**——修复方案见 §8 P1。当前每日 16:30 报告若失败，可用 report-by-date 手动补当天。

### 4.5 GitHub Actions 工作流

| 工作流 | 触发 | 行为 |
| --- | --- | --- |
| `daily-report.yml` | cron UTC 08:30 周一~五（北京16:30）+ 手动 | `bun run src/main.ts` → 提交 reports/（含 latest.html）。timeout 30 分钟 |
| `report-by-date.yml` | 手动，date 输入（YYYY-MM-DD） | `bun run src/report-by-date.ts $date` → 提交 reports/。timeout 60 分钟；按 date 做并发组 |

触发路径（告知用户用）：仓库 → Actions → 选工作流 → Run workflow。直达链接：`https://github.com/zencolab/stock/actions/workflows/report-by-date.yml`

---

## 5. 性能与请求量预算

| 项 | 量级 |
| --- | --- |
| v2 基线（全市场 5545 只，并发8） | 回算每只 2 请求（raw+qfq），完整一跑 13–15 分钟 |
| 三期新增（历史路径） | 200×slist + 200×窗口日K + 候选概念板块（去重后约300-450个）×成分股1-2页 + 龙虎榜 1+2×上榜数，约 +3–6 分钟 |
| 三期新增（当日路径） | 200×(slist+分时) + 展开板块（去重后约150-250）×(成分股+trends2) + 指数4 + 龙虎榜，量级相近 |
| 产物体积 | v2≈800KB；v3 历史≈2.1MB、当日≈4.2MB（分时点多） |

**调并发或重试前先算请求总量**：`5545 × 2 × 重试数` 的乘法效应会失控（一代曾跑 39 分钟）。

---

## 6. 口径与诚实性原则（用户明确重视，必须延续）

- 取不到的数据显示 `—` 或明确文案并注明原因，**不编造、不臆测**；推算值必须标注（市值、换手率\*、历史板块涨幅\*）。
- 报告 meta 区口径说明必须与实现一致；三期的 `metaNote` 是动态拼装的，改逻辑时同步改 `buildEnrichment` 里的 notes 文案。
- 三期已披露的口径边界：①分时仅当天，历史日期为±30窗口图；②概念板块归属/成分是**当前时点**体系（东财不提供历史成分），历史日期存在时点错位；③历史板块涨幅为成分股均值估算，与东财板块指数（自由流通市值加权）有差异；④历史口径下北交所成员计"无数据"；⑤板块历史K线大概率缺图（数据源限制）。

---

## 7. 环境限制与坑（务必先读再动手）

### GitHub 操作能力（经由 AI 平台的 GitHub MCP 连接）

- ✅ 读文件/目录（可只取 name/sha 字段）、`push_files` 多文件一次提交、分支/PR/Issue、代码搜索；
- ❌ **任何 Actions 操作**（触发/读日志/下载 artifact）→ 必须请用户手动 Run workflow；观测结果的唯一通道是让工作流把产物 commit 回仓库再轮询读文件；
- ❌ **单次提交请求体上限 4MB**（实测：4.2MB 的当日版报告推不上去，2.5MB 可以）。大产物靠 Actions 现场生成提交，别从本地推；
- 提交后用 blob SHA 校验：本地 `git hash-object 文件` 应等于远端 get_file_contents 返回的 SHA。

### 坑列表

| 坑 | 对策 |
| --- | --- |
| `.gitignore` 含 `*.log`，日志被静默忽略 | 产物一律 `.txt`/`.html` 后缀，必要时 `git add -f` |
| 工作流内 git 提交失败 exit 128 丢日志 | 提交步骤自带重试且 exit 0 |
| push2his「今天能通」 | 放行窗口假象（§3 开头），别据此改回东财主源或调高板块K线权重 |
| 对 push2his 镜像连发请求 | 十余个快速请求即触发全镜像 502 封禁。保持"单次尝试+熔断5次"，勿加重试 |
| 分时没有历史 | 腾讯/东财都只保留当天，别再找历史分时源 |
| 腾讯前复权行缺换手率/成交额列 | 只能从不复权行取 |
| 指数行列数与个股不同 | 指数只读日期/收盘，别读第 7/8 列 |
| AbortSignal 复用于多次重试 | 触发一次后后续全败；每次重试必须新建（enrich.ts/report-by-date.ts 均已正确实现，勿回退） |
| slist 板块混含多类型 | 必须与概念全表（m:90+t:3）交集过滤；展开另有 `EXPAND_BLOCK` 黑名单，发现新泄漏（如当年的"最近多板""深成500"）就补正则 |
| 板块成分含北交所 | 历史口径计"无数据"并已披露；若四期加北交所（§8）需同步改 |
| 龙虎榜含北交所/一股多行/席位重复 | code 匹配自然过滤北交所；原因去重展示；席位按（营业部+买+卖）去重 |
| 新股无股东数据 | 事实不是 bug，`bars ≤ 20` 有专门文案 |
| 股东接口偶发空 result | 重试 + 串行补漏机制已内置 |
| 沙箱 /tmp 易失 | 持久产物推 GitHub；本地开发目录随平台而定 |

### 本地验证流程（改动后必跑）

```bash
bun src/report-by-date.ts <历史交易日> > run.log 2>&1     # 约18-20分钟，建议 nohup 后台
# 质检（注意 grep -c 按行计数，SVG同行时要用 -o | wc -l 按次数计）：
grep -o 'class="card"' reports/$D.html | wc -l        # 期望 200
grep -o 'class="enrich"' reports/$D.html | wc -l      # 期望 200
grep -oE 'NaN|undefined' reports/$D.html | wc -l      # 期望 0
grep -o '未能获取K线' reports/$D.html | wc -l          # 期望 0
grep -o '<circle' reports/$D.html | wc -l             # 历史路径期望 400（个股200+指数200）
grep -o 'class="lhbtag"' reports/$D.html | wc -l      # 应等于日志"入选200只中上榜"数
# 若当天是交易日，收盘后再跑一次当日日期，验证分时路径（板块图应零缺失）
```

**验收标准**：报告存在；200 卡片/200 增强区；无 NaN/undefined；排序正确；有效样本 5000+；双图齐全；龙虎榜数与日志一致；展开黑名单零泄漏（`grep -oE '<div class="bkhead"><b>[^<]+</b>'` 人工过目）。

---

## 8. 历史勘误与结论（避免走回头路）

1. **一代假设全部被推翻**：「push2his 是 IPv6/Bun fetch 问题」→ 强制 IPv4 直连各真实 IP 同样瞬间被拒、curl 与 Bun 表现一致、`dns.setDefaultResultOrder` 无效。真相是东财对数据中心网段封锁且有放行窗口。**主源必须是腾讯。**
2. **92.push2his 镜像同理**（三期实测）：首轮全通是窗口，不是通道。
3. 一代 `test/net-hardening.ts` 已废弃，唯一有价值发现（AbortSignal 复用缺陷）已合入 v2/v3。
4. 涨跌幅必须用**前复权收盘环比**（除权除息日才正确），勿改回不复权环比。

---

## 9. 四期路线图建议（动手前先与用户确认范围）

### P1
1. **main.ts 隐患修复（需用户点头）**：每日任务改跑 `report-by-date.ts $(今天)` 即可——每日报告只是"指定日期=今天"的特例，且能直接获得三期增强 + 当日真分时。改 `daily-report.yml` 一行 + latest.html 写入逻辑（可在 report-by-date.ts 加 `--latest` 开关，仅当日期==今天时同步写 latest.html）。**main.ts 本体仍不动。**
   注意：当日版报告约 4.2MB，Actions 内 git 提交无 4MB 限制（那是 MCP 接口的限制），可行。
2. **报告索引页** `reports/index.html`：报告多了不好找。生成时顺手重写索引（列出全部 YYYY-MM-DD.html 链接）。
3. **运行失败告警**：工作流 failure 时用 `GITHUB_TOKEN` 调 REST API 自动开 Issue。

### P2
4. **北交所支持**：FS 加 `m:0+t:81+s:2048`；`symTX`/`secidEM`/`marketIndexOf` 加 `bj`/`0.`/北证50 分支；腾讯对 `bj` 代码的日K/分时支持需先抽样实测；历史口径板块统计的"无数据"文案同步调整。
5. **涨跌停/连板统计**：报告头部加当日涨停家数、连板梯队（按板块区分 10%/20%/30% 与 ST 5%）。
6. **板块历史K线备源**：同花顺 `d.10jqka.com.cn` 板块线实测可达，但板块体系（881xxx）与东财 BK 不互通——需要建名称映射表并处理口径差异，工作量不小，仅当用户强烈需要历史板块图时再做。
7. **数据缓存**：全市场日K按日落盘（`data/YYYY-MM-DD.jsonl`），区间回算免重复拉取。
8. **日期区间批量回算**：`report-by-date.ts 2026-07-01 2026-07-31` 逐日生成。

### 动手前自检清单

- [ ] 读完本文档 §3、§7、§8
- [ ] 确认 GitHub 连接可用且能访问 `zencolab/stock`
- [ ] 任何数据源改动先做数据中心网络可达性探测（且**间隔复测**排除放行窗口），别信「本机能通」
- [ ] 改完本地完整实跑 + §7 质检命令 → 推 main → 请用户手动跑一次 Actions 收尾
- [ ] 新功能的口径/局限同步写进报告 meta 区与本文档

---

## 10. 新 AI 快速上手（第一小时）

1. 读本文档（就是你正在做的事），重点 §3、§7。
2. 用 GitHub 连接确认仓库可达；拉 `src/report-by-date.ts`（~560行）、`src/lib/enrich.ts`（~430行）、`src/lib/enrich-run.ts`（~380行）通读，注释完整。
3. 跑一次 `bun src/report-by-date.ts <最近交易日>` 建立手感（18-20 分钟），对照 §7 质检。
4. 与用户确认本次任务范围，对照 §9 路线图。
5. 记住：**腾讯是主源；不覆盖 main.ts；没跑通不算完成；口径说明与实现同步。**

---

## 附：三期验证记录（2026-08-04，数据中心网络，最终代码）

| 验证 | 结果 |
| --- | --- |
| 历史日期实跑 2026-07-31 | 有效样本 5197（与二期基线一致）；200 卡片/200 增强区/400 张双图；23 只上榜股席位表全齐；395 个展开板块面板；无 NaN；板块历史K线全部降级注明；报告 2131KB |
| 当日实跑 2026-08-04（收盘后） | 有效样本 5196；798 张分时图（个股200+指数200+板块398，含昨收基准线）零缺失；19 只上榜股席位全齐；展开黑名单零泄漏；报告 4176KB |
| 数据正确性抽样 | 席位金额、上榜原因、板块涨跌家数与东财页面人工抽样核对一致 |
| **Actions 终极验证（历史路径）** | 用户手动触发 report-by-date.yml，date=2026-07-30：产物 2.6MB 已提交回仓库，200 卡片/400 双图/44 只上榜股席位全齐/无 NaN/黑名单零泄漏。板块历史K线在 GitHub 网络同样全部降级（意料之中，已注明） |
| **Actions 终极验证（当日路径）** | 用户 2026-08-04 23:59（北京）触发 date=2026-08-04：**腾讯分时与东财 trends2 在 GitHub Actions 网络实测可达**——200 张个股分时+798 条昨收基准线+板块分时零缺失，19 只上榜股席位全齐。产物 4.5MB 由 Actions 内 git 提交（不受 MCP 4MB 上限约束，印证 §9 P1 方案可行） |
| 二期存量验证 | 见 git 历史中 v2 文档（Actions 实跑成功、腾讯数据 40 只抽样核对一致等），结论仍有效 |
