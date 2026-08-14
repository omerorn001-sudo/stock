# 龙虎榜真实联网与 Google Drive 验收结果

验收时间：2026-08-14（Asia/Shanghai）

## 最终结论

龙虎榜真实数据采集、沪深 A 股过滤、文件生成、Apps Script 网关和 Google Drive 写入均已通过验收。

- 真实 Drive 验收：<https://github.com/zencolab/stock/actions/runs/31777414827>
- Apps Script 独立语法验收：<https://github.com/zencolab/stock/actions/runs/31637485300>

## 真实数据源

测试交易日：`2026-08-11`

```text
东方财富原始记录       73
保留沪深 A 股          68
排除记录                5
上榜证券               56
数据源失败              0
```

成功生成：

- `龙虎榜_2026-08-11.csv`
- `龙虎榜_2026-08-11.json`
- `龙虎榜摘要_2026-08-11.md`

## Apps Script 网关

```text
URL Secret             存在
Token Secret           存在
网关连接                成功
数据集能力              dragon_tiger
Drive 根目录            CNINFO/龙虎榜
```

报告没有记录 URL、上传令牌或 Secret 值。

## 第一次真实上传

```text
Drive 新建              3
Drive 更新              0
Drive 跳过              0
Drive 失败              0
流水线失败              0
```

文件目录：

```text
CNINFO/龙虎榜/2026/2026-08/2026-08-11/
```

## 第二次重复运行

```text
Drive 新建              0
Drive 更新              1
Drive 跳过              2
Drive 失败              0
流水线失败              0
```

CSV 和 Markdown 内容未变化，已跳过；JSON 中包含本次生成时间，因此生成新版本。第二次运行没有新建重复路径文件，旧 JSON 版本按现有版本策略进入回收站。

## 代码验证说明

- Python 编译通过；
- Ruff 关键静态检查通过；
- 单元测试 `5 passed`；
- 真实东方财富接口探针通过；
- `Code.gs` 使用标准输入方式执行 Node 语法检查并通过。

综合结论：**真实 Drive 验收通过**。
