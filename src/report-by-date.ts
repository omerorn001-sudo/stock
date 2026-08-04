/**
 * A股「任意交易日」涨跌幅 TOP100 报告生成器（历史日期版 v3）
 *
 * v3 变更（三期增强，实现见 src/lib/enrich.ts 数据层 + src/lib/enrich-run.ts 编排渲染层）：
 * - 每张卡片新增两张走势图：个股当日走势 + 所属市场板块（沪主板/深主板/创业板/科创板）指数走势。
 *   分时数据源仅保留当天 → 报告日=今天时画真分时；历史日期降级为"±30个交易日收盘窗口图并标记当日"。
 * - 概念板块：列出个股全部所属概念（东财概念板块体系，剔除交易属性/指数成份类后展开当日最强2个），
 *   展开内容含板块走势图、涨跌家数统计、领涨领跌前10成分股。历史日期的板块涨幅与成分股涨跌
 *   复用全市场回算数据（零额外请求、口径一致）。
 * - 龙虎榜：当日上榜标记 + 上榜原因 + 买入/卖出前5席位明细（东财数据中心，可回溯历史日期）。
 *
 * 与 src/main.ts 的区别：
 * - main.ts 依赖东方财富的"实时/延时榜单快照"，只能生成当日报告，无法回溯。
 * - 本脚本不依赖榜单快照：先取全市场A股名单，再逐只拉取日K，用指定日期当天的
 *   涨跌幅重新排序，因此可以生成"任意历史交易日"的报告。
 *
 * v2 变更（修复 GitHub Actions 上东财 K 线接口拒绝数据中心网络连接的问题）：
 * - 个股/指数 K 线主数据源改为腾讯财经（proxy.finance.qq.com，备用 web.ifzq.gtimg.cn），
 *   该源在数据中心网络可用，且不复权日K直接提供当日换手率与成交额，字段完备。
 * - 当日涨跌幅 = 前复权连续收盘价环比，与行情软件官方口径一致（正确处理除权除息日）；
 *   不再需要"推算涨跌幅"的近似逻辑。
 * - 东方财富 K 线接口降级为逐只兜底源（若可达）。
 * - 全市场名单与前十大流通股东仍用东方财富（push2delay / datacenter 域名实测可达），
 *   名单接口增加镜像域名轮换。
 *
 * 用法（任选其一）：
 *   bun run src/report-by-date.ts 2026-07-29
 *   REPORT_DATE=2026-07-29 bun run src/report-by-date.ts
 *
 * 输出：reports/YYYY-MM-DD.html（不改写 reports/latest.html，避免覆盖每日任务产物）
 *
 * 诚实性说明（同样写入报告页脚注）：
 * - 历史榜单为"按当日涨跌幅对全市场逐只重算"所得，样本取自"当前仍在市"的股票名单，
 *   之后已退市的个股不在样本内。
 * - 历史市值 = 当日收盘价 × 当前股本（股本若在此期间变动会有偏差），已在报告中标注。
 * - 前十大流通股东取"报告期不晚于指定日期"的最新一期披露。
 */

import { mkdirSync, writeFileSync } from "node:fs";
import { buildEnrichment, renderEnrichHtml, enrichCss } from "./lib/enrich-run";

// ---------- 参数 ----------
const RAW_DATE = (process.env.REPORT_DATE || process.env.DATE || process.argv[2] || "").trim();
if (!/^\d{4}-\d{2}-\d{2}$/.test(RAW_DATE)) {
  console.error("请指定日期，格式 YYYY-MM-DD。例如：bun run src/report-by-date.ts 2026-07-29");
  process.exit(1);
}
const DATE = RAW_DATE;
const dateMs = new Date(DATE + "T00:00:00Z").getTime();
if (!isFinite(dateMs)) {
  console.error(`非法日期：${DATE}`);
  process.exit(1);
}
const todayBJ = new Date(Date.now() + 8 * 3600e3).toISOString().slice(0, 10);
if (DATE > todayBJ) {
  console.error(`指定日期 ${DATE} 晚于今天（${todayBJ}），无法生成。`);
  process.exit(1);
}
const compact = (s: string) => s.replace(/-/g, "");
const shift = (days: number) => new Date(dateMs - days * 86400e3).toISOString().slice(0, 10);
const END = compact(DATE);
const BEG_SCREEN_D = shift(30);          // 初筛只需当日 + 近5个交易日换手率
const BEG_SCREEN = compact(BEG_SCREEN_D);
const BEG_CHART_D = shift(185);          // 明细图需近半年
const BEG_CHART = compact(BEG_CHART_D);
const WEEKDAYS = ["日", "一", "二", "三", "四", "五", "六"];
const weekday = WEEKDAYS[new Date(DATE + "T00:00:00Z").getUTCDay()];
const CONCURRENCY = Number(process.env.CONCURRENCY || 8);

// ---------- 通用请求 ----------
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36";

async function getJSON(url: string, referer: string, tries = 5): Promise<any> {
  for (let i = 0; i < tries; i++) {
    try {
      const r = await fetch(url, {
        headers: { Referer: referer, "User-Agent": UA },
        signal: AbortSignal.timeout(20000),
      });
      if (!r.ok) throw new Error("HTTP " + r.status);
      return await r.json();
    } catch (e) {
      if (i === tries - 1) return { __error: String(e) };
      await sleep(700 * (i + 1));
    }
  }
}

// 腾讯K线：主 proxy.finance.qq.com，备 web.ifzq.gtimg.cn（同一接口的两个域名）
const TX_HOSTS = ["https://proxy.finance.qq.com/ifzqgtimg", "https://web.ifzq.gtimg.cn"];
let txHostIdx = 0;
/**
 * 拉取腾讯日K。fq: "" 不复权（返回 day），"qfq" 前复权（返回 qfqday）。
 * 行格式：[日期, 开盘, 收盘, 最高, 最低, 成交量(手), {…}, 换手率%, 成交额(万元), …]
 * （指数与部分老数据行可能缺少第8/9列）
 */
async function txKline(sym: string, beg: string, end: string, count: number, fq: "" | "qfq"): Promise<any[][] | null> {
  for (let h = 0; h < TX_HOSTS.length; h++) {
    const host = TX_HOSTS[(txHostIdx + h) % TX_HOSTS.length];
    const j = await getJSON(
      `${host}/appstock/app/newfqkline/get?param=${sym},day,${beg},${end},${count},${fq}`,
      "https://gu.qq.com/",
      3
    );
    const d = j?.data?.[sym];
    const rows: any[][] | undefined = fq === "qfq" ? d?.qfqday ?? d?.day : d?.day;
    if (rows && Array.isArray(rows)) return rows;
    if (h === 0) txHostIdx = (txHostIdx + 1) % TX_HOSTS.length; // 首选域名异常则切换
  }
  return null;
}

async function runPool<T, R>(items: T[], limit: number, fn: (x: T) => Promise<R>, label = ""): Promise<R[]> {
  const out: R[] = new Array(items.length);
  let idx = 0, done = 0;
  const step = Math.max(1, Math.floor(items.length / 10));
  const worker = async () => {
    while (true) {
      const i = idx++;
      if (i >= items.length) break;
      out[i] = await fn(items[i]);
      done++;
      if (label && done % step === 0) console.log(`${label} ${done}/${items.length}`);
    }
  };
  await Promise.all(Array.from({ length: limit }, worker));
  return out;
}

const secidEM = (code: string) => (code.startsWith("6") ? "1." : "0.") + code;
const symTX = (code: string) => (code.startsWith("6") ? "sh" : "sz") + code;

// 东财K线兜底（在部分网络环境下 push2his 拒绝连接，因此只作兜底且轻量重试）
let emKlineDead = 0; // 连续失败计数，超过阈值后不再尝试，避免浪费时间
async function emKline(code: string, beg: string, end: string, fqt: 0 | 1): Promise<string[] | null> {
  if (emKlineDead > 20) return null;
  const j = await getJSON(
    `https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=${secidEM(code)}&klt=101&fqt=${fqt}&fields1=f1,f2,f3&fields2=f51,f52,f53,f56,f57,f59,f61&beg=${beg}&end=${end}`,
    "https://quote.eastmoney.com/",
    2
  );
  const rows: string[] | undefined = j?.data?.klines;
  if (rows && rows.length) { emKlineDead = 0; return rows; }
  emKlineDead++;
  return null;
}

// ---------- 0) 交易日校验：上证指数当日必须有K线 ----------
{
  let days: string[] = [];
  const rows = await txKline("sh000001", BEG_SCREEN_D, DATE, 40, "");
  if (rows && rows.length) {
    days = rows.map((r) => String(r[0]));
  } else {
    const em = await emKline("000001", BEG_SCREEN, END, 1); // 兜底（注意：东财指数 secid 前缀特殊，此处按沪市处理）
    if (em) days = em.map((s) => s.split(",")[0]);
  }
  if (!days.length) {
    console.error("无法获取上证指数K线，交易日校验失败（接口异常或被限流）。");
    process.exit(1);
  }
  if (!days.includes(DATE)) {
    console.error(`${DATE}（周${weekday}）不是交易日（该日无上证指数K线）。最近的交易日：${days.filter((d) => d <= DATE).slice(-3).join("、") || days.slice(-3).join("、")}`);
    process.exit(1);
  }
  console.log(`交易日校验通过：${DATE}（周${weekday}）`);
}

// ---------- 1) 全市场A股名单（沪深，不含北交所） ----------
const FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"; // 深主板、创业板、沪主板、科创板
const CLIST_HOSTS = ["https://push2delay.eastmoney.com", "https://push2.eastmoney.com", "https://92.push2.eastmoney.com"];
type Stock = { code: string; name: string; total: number; float: number };
const universe: Stock[] = [];
{
  let total = 0, hostOK = "";
  for (const host of CLIST_HOSTS) {
    universe.length = 0;
    let page = 1, failed = false;
    while (true) {
      const j = await getJSON(
        `${host}/api/qt/clist/get?pn=${page}&pz=100&po=1&np=1&fltt=2&invt=2&fid=f12&fs=${FS}&fields=f12,f14,f38,f39`,
        "https://quote.eastmoney.com/",
        3
      );
      const diff = j?.data?.diff;
      if (!diff?.length) {
        if (page === 1) failed = true;
        break;
      }
      total = j.data.total || total;
      for (const d of diff) {
        universe.push({ code: String(d.f12), name: d.f14, total: Number(d.f38), float: Number(d.f39) });
      }
      if (universe.length >= total) break;
      page++;
      await sleep(120);
    }
    if (!failed && universe.length) { hostOK = host; break; }
    console.warn(`名单接口 ${host} 失败，尝试下一个镜像…`);
  }
  if (!universe.length) {
    console.error("全市场名单获取失败：所有镜像域名均未返回数据");
    process.exit(1);
  }
  console.log(`全市场名单：${universe.length} 只（接口声明 ${total} 只，来源 ${hostOK}）`);
}

// ---------- 2) 逐只拉取日K，取指定日期当天数据用于排序 ----------
type DayRow = {
  code: string;
  name: string;
  total: number;
  float: number;
  close: number | null;
  open: number | null;
  amount: number | null;   // 成交额（元）
  pct: number | null;      // 当日涨跌幅 %
  pctSrc: "qfq" | "em" | "raw" | "";  // 涨跌幅口径来源
  turn: number | null;     // 当日换手率 %
  turnEst: boolean;        // 换手率是否为推算值（成交量÷流通股本）
  turn5: number[];         // 近5个交易日（含当日）单日换手率
  bars: number;            // 截至当日的K线根数（用于判断是否新股）
  err: string;
};

const fnum = (v: any): number | null => {
  const n = parseFloat(v);
  return isFinite(n) ? n : null;
};

async function screenOne(s: Stock): Promise<DayRow> {
  const base: DayRow = {
    code: s.code, name: s.name, total: s.total, float: s.float,
    close: null, open: null, amount: null, pct: null, pctSrc: "", turn: null, turnEst: false,
    turn5: [], bars: 0, err: "",
  };

  // 主源：腾讯不复权日K（当日开收/成交额/换手率，口径与行情软件一致）
  const raw = await txKline(symTX(s.code), BEG_SCREEN_D, DATE, 30, "");
  if (raw && raw.length) {
    const upto = raw.filter((r) => String(r[0]) <= DATE);
    base.bars = upto.length;
    base.turn5 = upto.slice(-5).map((r) => {
      const t = fnum(r[7]);
      if (t !== null) return t;
      const v = fnum(r[5]);
      return v !== null && s.float ? +((v * 100 / s.float) * 100).toFixed(4) : NaN;
    }).filter((v) => isFinite(v));
    const i = upto.findIndex((r) => String(r[0]) === DATE);
    if (i < 0) {
      base.err = "当日无K线（停牌、尚未上市或已退市）";
      return base;
    }
    const r = upto[i];
    base.open = fnum(r[1]);
    base.close = fnum(r[2]);
    const amtWan = fnum(r[8]);
    base.amount = amtWan !== null ? amtWan * 1e4 : null; // 万元 → 元
    base.turn = fnum(r[7]);
    if (base.turn === null) {
      const v = fnum(r[5]);
      if (v !== null && s.float) { base.turn = +((v * 100 / s.float) * 100).toFixed(2); base.turnEst = true; }
    }

    // 涨跌幅：用前复权连续收盘价计算（除权除息日也与官方口径一致）
    const qfq = await txKline(symTX(s.code), BEG_SCREEN_D, DATE, 30, "qfq");
    if (qfq && qfq.length) {
      const q = qfq.filter((r2) => String(r2[0]) <= DATE);
      const qi = q.findIndex((r2) => String(r2[0]) === DATE);
      if (qi > 0) {
        const c = fnum(q[qi][2]), p = fnum(q[qi - 1][2]);
        if (c !== null && p !== null && p !== 0) { base.pct = +(((c - p) / p) * 100).toFixed(2); base.pctSrc = "qfq"; }
      }
    }
    if (base.pct === null) {
      // 窗口内无前一交易日（如上市首日）→ 尝试东财兜底取官方涨跌幅
      const em = await emKline(s.code, BEG_SCREEN, END, 0);
      if (em) {
        const hit = em.map((x) => x.split(",")).find((p) => p[0] === DATE);
        const v = hit ? fnum(hit[5]) : null;
        if (v !== null) { base.pct = v; base.pctSrc = "em"; }
      }
    }
    if (base.pct === null) base.err = "无法确定当日涨跌幅（上市首日或前收盘缺失）";
    return base;
  }

  // 腾讯失败 → 东财兜底（若该网络环境可达）
  const em = await emKline(s.code, BEG_SCREEN, END, 0);
  if (em) {
    const upto = em.map((x) => x.split(",")).filter((p) => p[0] <= DATE);
    base.bars = upto.length;
    base.turn5 = upto.slice(-5).map((p) => parseFloat(p[6])).filter((v) => isFinite(v));
    const hit = upto.find((p) => p[0] === DATE);
    if (!hit) { base.err = "当日无K线（停牌、尚未上市或已退市）"; return base; }
    base.open = fnum(hit[1]);
    base.close = fnum(hit[2]);
    base.amount = fnum(hit[4]);
    base.pct = fnum(hit[5]);
    base.pctSrc = base.pct !== null ? "em" : "";
    base.turn = fnum(hit[6]);
    if (base.pct === null) base.err = "东财K线缺少涨跌幅字段";
    return base;
  }

  base.err = "腾讯与东方财富源均未返回K线";
  return base;
}

console.log(`开始逐只回算 ${DATE} 当日行情（并发 ${CONCURRENCY}，预计数分钟）…`);
const screened = await runPool(universe, CONCURRENCY, screenOne, "回算进度");
const valid = screened.filter((r) => r.pct !== null && isFinite(r.pct as number));
const failed = screened.filter((r) => r.err === "腾讯与东方财富源均未返回K线");
const noPct = screened.filter((r) => r.err === "无法确定当日涨跌幅（上市首日或前收盘缺失）");
console.log(`当日有效样本 ${valid.length} 只；接口失败 ${failed.length} 只；无法定涨跌幅 ${noPct.length} 只`);
if (valid.length < 100) {
  console.error("有效样本不足 100 只，判定为接口大面积失败，终止（不产出不完整报告）。");
  process.exit(1);
}

const sorted = [...valid].sort((a, b) => (b.pct as number) - (a.pct as number));
const up = sorted.slice(0, 100);
const down = sorted.slice(-100).reverse();
const estTurnCount = [...up, ...down].filter((r) => r.turnEst).length;

// ---------- 3) 入选个股明细：前复权K线（画图）+ 前十大流通股东 ----------
type Extra = { kline: [string, number][]; kerr: string; holders: any[]; holderDate: string; serr: string };
const extra: Record<string, Extra> = {};

async function fetchHolders(r: DayRow, e: Extra, tries: number) {
  const suffix = r.code.startsWith("6") ? "SH" : "SZ";
  let sj: any = null;
  let data: any[] = [];
  for (let attempt = 0; attempt < tries; attempt++) {
    sj = await getJSON(
      `https://datacenter.eastmoney.com/securities/api/data/v1/get?reportName=RPT_F10_EH_FREEHOLDERS&columns=SECUCODE,END_DATE,HOLDER_RANK,HOLDER_NAME,HOLDER_TYPE,HOLD_NUM,FREE_HOLDNUM_RATIO&filter=(SECUCODE%3D%22${r.code}.${suffix}%22)&pageNumber=1&pageSize=60&sortTypes=-1,1&sortColumns=END_DATE,HOLDER_RANK&source=HSF10&client=PC`,
      "https://emweb.securities.eastmoney.com/"
    );
    data = sj?.result?.data || [];
    if (data.length) break;           // 拿到数据即止；偶发空响应则稍候重试
    await sleep(1500 * (attempt + 1));
  }
  // 只取"报告期不晚于指定日期"的最新一期，避免用未来数据
  const past = data.filter((h) => String(h.END_DATE).slice(0, 10) <= DATE);
  if (past.length) {
    const latest = past.reduce((m, h) => (String(h.END_DATE) > String(m) ? String(h.END_DATE) : m), String(past[0].END_DATE));
    e.holderDate = String(latest).slice(0, 10);
    e.holders = past.filter((h) => String(h.END_DATE) === String(latest))
      .sort((a, b) => a.HOLDER_RANK - b.HOLDER_RANK)
      .map((h) => ({ name: h.HOLDER_NAME, type: h.HOLDER_TYPE || "-", ratio: h.FREE_HOLDNUM_RATIO }));
    e.serr = "";
  } else {
    e.serr = data.length ? `该股最早披露期晚于 ${DATE}，当时尚未披露` : (sj?.__error || (r.bars && r.bars <= 20 ? "上市初期新股，尚未披露定期报告（前十大流通股东无数据）" : "数据源无该股流通股东数据"));
  }
}

async function fetchExtra(r: DayRow) {
  const e: Extra = { kline: [], kerr: "", holders: [], holderDate: "", serr: "" };

  // 半年前复权收盘价：主腾讯，兜底东财
  const rows = await txKline(symTX(r.code), BEG_CHART_D, DATE, 200, "qfq");
  if (rows && rows.length) {
    e.kline = rows.filter((x) => String(x[0]) <= DATE).map((x) => [String(x[0]), parseFloat(x[2])] as [string, number]);
  } else {
    const em = await emKline(r.code, BEG_CHART, END, 1);
    if (em) e.kline = em.map((s) => s.split(",")).filter((p) => p[0] <= DATE).map((p) => [p[0], parseFloat(p[2])] as [string, number]);
    else e.kerr = "腾讯与东方财富源均未返回K线数据";
  }

  await fetchHolders(r, e, 2);
  extra[r.code] = e;
}

console.log("拉取入选个股的走势图与流通股东…");
await runPool([...up, ...down], 6, fetchExtra, "明细进度");

// 补漏：对偶发空响应的个股做串行二次重试（"尚未披露"属正常情况，不重试）
{
  const misses = [...up, ...down].filter((r) => extra[r.code]?.serr === "数据源无该股流通股东数据");
  if (misses.length) {
    console.log(`股东数据补漏重试 ${misses.length} 只…`);
    for (const r of misses) {
      await sleep(800);
      await fetchHolders(r, extra[r.code], 3);
      console.log(`  ${r.code} ${r.name}: ${extra[r.code].holders.length ? "已补齐" : "仍失败（" + extra[r.code].serr + "）"}`);
    }
  }
}

// ---------- 3.5) 三期增强：双走势图 / 概念板块 / 龙虎榜 ----------
const isToday = DATE === todayBJ;
const pctMap = new Map<string, number>(valid.map((r) => [r.code, r.pct as number]));
const enrichRes = await buildEnrichment({
  date: DATE,
  isToday,
  selected: [...up, ...down].map((r) => ({ code: r.code, name: r.name })),
  pctMap,
  concurrency: 6,
});

// ---------- 4) 生成 HTML ----------
const num = (v: any): number | null => (typeof v === "number" && isFinite(v) ? v : null);
const yi = (v: any, d = 2) => { const n = num(v); return n === null ? "—" : (n / 1e8).toFixed(d) + " 亿"; };
const pct = (v: any, d = 2) => { const n = num(v); return n === null ? "—" : n.toFixed(d) + "%"; };
const px = (v: any) => { const n = num(v); return n === null ? "—" : n.toFixed(2); };

function board(code: string): string {
  if (code.startsWith("688") || code.startsWith("689")) return "科创板";
  if (code.startsWith("60")) return "沪主板";
  if (code.startsWith("300") || code.startsWith("301") || code.startsWith("302")) return "创业板";
  if (code.startsWith("002") || code.startsWith("003")) return "深主板（原中小板·小盘）";
  if (code.startsWith("000") || code.startsWith("001")) return "深主板";
  return "—";
}

function sparkline(kline: [string, number][], color: string): string {
  const pts = kline.map((k) => k[1]).filter((v) => typeof v === "number" && isFinite(v));
  if (pts.length < 2) return `<div class="nochart">区间K线不足，无法绘图</div>`;
  const W = 300, H = 80, P = 4;
  const min = Math.min(...pts), max = Math.max(...pts);
  const rng = max - min || 1;
  const step = (W - 2 * P) / (pts.length - 1);
  const path = pts.map((v, i) => `${i ? "L" : "M"}${(P + i * step).toFixed(1)},${(H - P - ((v - min) / rng) * (H - 2 * P)).toFixed(1)}`).join("");
  const d0 = kline[0][0], d1 = kline[kline.length - 1][0];
  return `<svg width="${W}" height="${H + 18}" viewBox="0 0 ${W} ${H + 18}"><rect x="0" y="0" width="${W}" height="${H}" fill="#fafafa" stroke="#e5e5e5"/><path d="${path}" fill="none" stroke="${color}" stroke-width="1.5"/><text x="2" y="${H + 13}" class="axlbl">${d0}</text><text x="${W - 2}" y="${H + 13}" text-anchor="end" class="axlbl">${d1}</text><text x="${W - 4}" y="12" text-anchor="end" class="axlbl">高 ${max.toFixed(2)} / 低 ${min.toFixed(2)}</text></svg>`;
}

function turnover5(r: DayRow): string {
  if (!r.turn5.length) return "—（无换手数据）";
  const sum = r.turn5.reduce((a, b) => a + b, 0);
  const note = r.turn5.length < 5 ? `（上市不足5日，仅${r.turn5.length}日）` : "";
  return sum.toFixed(2) + "%" + (r.turnEst ? "*" : "") + note;
}

function holdersHtml(r: DayRow): string {
  const e = extra[r.code];
  if (!e || e.serr || !e.holders.length) {
    return `<div class="noholder">未能获取：${e?.serr || "数据源无该股流通股东数据"}</div>`;
  }
  const items = e.holders
    .map((h) => `<li>${h.name}<span class="htype">（${h.type}${typeof h.ratio === "number" ? "，占流通股 " + h.ratio.toFixed(2) + "%" : ""}）</span></li>`)
    .join("");
  return `<div class="hdate">截至 ${e.holderDate}（前十大流通股东，为不晚于 ${DATE} 的最新一期定期报告）</div><ol class="holders">${items}</ol>`;
}

function card(r: DayRow, idx: number, dir: "up" | "down"): string {
  const e = extra[r.code];
  const color = dir === "up" ? "#c0392b" : "#1e8449";
  const chg = num(r.pct);
  const chgStr = chg === null ? "—" : (chg > 0 ? "+" : "") + chg.toFixed(2) + "%";
  const ratio = r.float && r.total ? ((r.float / r.total) * 100).toFixed(2) + "%" : "—";
  const floatCap = r.close && r.float ? r.close * r.float : null;
  const totalCap = r.close && r.total ? r.close * r.total : null;
  return `<div class="card">
  <div class="chead"><span class="rank">#${idx}</span> <b>${r.name}</b> <span class="code">${r.code}</span> <span class="board">${board(r.code)}</span> <span class="chg" style="color:${color}">${chgStr}</span></div>
  <div class="cbody">
    <table class="kv">
      <tr><td>收盘价</td><td>${px(r.close)} 元</td><td>开盘价</td><td>${px(r.open)} 元</td></tr>
      <tr><td>成交额</td><td>${r.amount === null ? "—（数据源缺失）" : yi(r.amount)}</td><td>当日换手率</td><td>${pct(r.turn)}${r.turnEst ? "*" : ""}</td></tr>
      <tr><td>五日换手率</td><td>${turnover5(r)}</td><td>流通市值<sup>推算</sup></td><td>${yi(floatCap)}</td></tr>
      <tr><td>流通股/总股本</td><td>${ratio}</td><td>总市值<sup>推算</sup></td><td>${yi(totalCap)}</td></tr>
    </table>
    <div class="chart">${e?.kline?.length ? sparkline(e.kline, color) : `<div class="nochart">未能获取K线：${e?.kerr || "无数据"}</div>`}<div class="chartlbl">截至 ${DATE} 近半年收盘价走势（前复权）</div></div>
    <div class="hbox"><div class="httl">前十大流通股东</div>${holdersHtml(r)}</div>
  </div>
${renderEnrichHtml(enrichRes.map, r.code)}
</div>`;
}

const section = (title: string, arr: DayRow[], dir: "up" | "down") =>
  `<h2 style="color:${dir === "up" ? "#c0392b" : "#1e8449"}">${title}</h2>` + arr.map((r, i) => card(r, i + 1, dir)).join("\n");

const generatedAt = new Date(Date.now() + 8 * 3600e3).toISOString().slice(0, 16).replace("T", " ");
const estNote = estTurnCount ? `；其中 ${estTurnCount} 只的换手率因数据源缺失由"成交量÷流通股本"推算，以 <b>*</b> 标注` : "";
const noPctNote = noPct.length ? `另有 ${noPct.length} 只（多为当日上市的新股）因缺少前收盘价无法计算涨跌幅，未参与排序；` : "";

const html = `<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>A股涨跌幅榜 TOP100 · ${DATE}</title>
<style>
body{font-family:"Microsoft YaHei","PingFang SC",sans-serif;margin:0;background:#f0f2f5;color:#222}
.wrap{max-width:1000px;margin:0 auto;padding:20px}
h1{font-size:22px;margin:8px 0}
h2{font-size:18px;border-left:4px solid currentColor;padding-left:8px;margin:26px 0 12px}
.meta{background:#fff;border:1px solid #e0e0e0;border-radius:8px;padding:12px 16px;font-size:12.5px;line-height:1.7;color:#444}
.meta b{color:#222}
.card{background:#fff;border:1px solid #e0e0e0;border-radius:8px;margin-bottom:12px;overflow:hidden}
.chead{padding:8px 14px;background:#f7f8fa;border-bottom:1px solid #eee;font-size:15px}
.rank{color:#888;font-size:13px}
.code{color:#666;font-family:Consolas,monospace}
.board{background:#eef2f7;border-radius:4px;padding:1px 7px;font-size:12px;color:#345}
.chg{float:right;font-weight:bold;font-size:16px}
.cbody{display:flex;flex-wrap:wrap;gap:14px;padding:10px 14px;align-items:flex-start}
table.kv{border-collapse:collapse;font-size:13px;min-width:330px}
table.kv td{padding:4px 10px 4px 0;border-bottom:1px dashed #eee}
table.kv td:nth-child(odd){color:#777;white-space:nowrap}
table.kv td:nth-child(even){font-weight:600;min-width:90px}
table.kv sup{color:#b58;font-size:9px}
.chart{flex:0 0 auto}
.chartlbl{font-size:11px;color:#888;text-align:center}
.axlbl{font-size:10px;fill:#999}
.hbox{flex:1;min-width:260px;font-size:12.5px}
.httl{font-weight:600;margin-bottom:2px}
.hdate{color:#888;font-size:11.5px;margin-bottom:3px}
ol.holders{margin:2px 0 4px;padding-left:20px;line-height:1.55;column-count:1}
.htype{color:#888}
.noholder,.nochart{color:#a66;font-size:12px;background:#fdf6f6;padding:6px 8px;border-radius:4px}
footer{font-size:12px;color:#888;margin:20px 0;text-align:center}
@media print{.card{break-inside:avoid}}
${enrichCss()}
</style></head><body><div class="wrap">
<h1>A股 涨幅前100 与 跌幅前100 明细报告（指定交易日）</h1>
<div class="meta">
<b>交易日：</b>${DATE}（周${weekday}）｜<b>范围：</b>沪深两市A股（沪主板、深主板含原中小板、创业板、科创板），<b>不含北交所</b>；参与排序的当日有效样本 ${valid.length} 只（全市场名单 ${universe.length} 只，其余为当日停牌、尚未上市或接口失败 ${failed.length} 只）。${noPctNote}<br>
<b>生成方式：</b>本报告为<b>历史日期回算版</b>——不使用行情榜单快照，而是对全市场逐只拉取日K线，取 ${DATE} 当日涨跌幅重新排序后取前/后各100名。行情数据主源为腾讯财经公开接口（收盘/开盘/成交额/换手率取自不复权日K原始字段；涨跌幅由前复权连续收盘价环比计算，与行情软件官方口径一致，除权除息日亦准确）；东方财富公开接口用于全市场名单、前十大流通股东及个别兜底${estNote}。生成时间 ${generatedAt}（北京时间）。<br>
<b>口径与局限：</b>①当日收盘价、开盘价、成交额、换手率均取自<b>不复权</b>日K线，与行情软件当日排行口径一致；走势图为<b>前复权</b>收盘价。②<b>流通市值/总市值为推算值</b>＝当日收盘价 × <b>当前</b>流通股本/总股本，若此后发生过增发、回购、解禁等股本变动会有偏差。③样本取自当前仍在市的股票名单，<b>在 ${DATE} 之后已退市的个股不在样本内</b>。④前十大流通股东取报告期<b>不晚于 ${DATE}</b> 的最新一期披露，不使用未来数据。⑤无法取得的数据以"—"标示并注明原因，<b>不编造、不臆测</b>。<br>
<b>三期增强口径：</b>${enrichRes.metaNote}
</div>
${section("一、涨幅前 100", up, "up")}
${section("二、跌幅前 100", down, "down")}
<footer>指定交易日：${DATE} · 全市场逐只回算 · 来源：腾讯财经 / 东方财富公开行情接口 · 仅供参考，不构成投资建议</footer>
</div></body></html>`;

mkdirSync("reports", { recursive: true });
writeFileSync(`reports/${DATE}.html`, html, "utf-8");
console.log(`报告已生成：reports/${DATE}.html（${(html.length / 1024).toFixed(0)} KB）`);
