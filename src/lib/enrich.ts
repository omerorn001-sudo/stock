/**
 * enrich.ts — 报告增强功能数据层（三期）
 *
 * 功能：
 *  A. 个股/指数当日分时（腾讯，仅当天有数据）
 *  B. 市场板块（上证/深证/创业板/科创板）指数映射与K线
 *  C. 概念板块：全表、个股归属、成分股、板块分时(当日)、板块日K(机会性)
 *  D. 龙虎榜：当日榜单 + 席位买卖明细
 *
 * 数据源约定（务必读 docs/DEVELOPMENT.md 第二、五节）：
 *  - 腾讯 proxy.finance.qq.com / web.ifzq.gtimg.cn：主源，数据中心网络稳定
 *  - push2delay.eastmoney.com：稳定（slist/clist/trends2 均可用；kline 不可用恒空）
 *  - datacenter-web.eastmoney.com：稳定（龙虎榜）
 *  - 92.push2his.eastmoney.com：★不稳定★，间歇 502/拒连（放行窗口），只作机会性
 *    数据源，带熔断，失败即诚实降级，绝不能让报告依赖它
 */

const UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";

function headersFor(url: string): Record<string, string> {
  let referer = "https://quote.eastmoney.com/";
  if (url.includes("qq.com") || url.includes("gtimg.cn")) referer = "https://gu.qq.com/";
  else if (url.includes("datacenter")) referer = "https://data.eastmoney.com/";
  return { "User-Agent": UA, Referer: referer, Accept: "*/*" };
}

/** 带重试的 GET JSON。每次重试新建 AbortSignal（复用是隐藏bug，勿改）。 */
export async function getJSON(url: string, tries = 3, timeoutMs = 20000): Promise<any> {
  let lastErr: any;
  for (let i = 0; i < tries; i++) {
    try {
      const res = await fetch(url, {
        headers: headersFor(url),
        signal: AbortSignal.timeout(timeoutMs),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const text = await res.text();
      return JSON.parse(text);
    } catch (e) {
      lastErr = e;
      if (i < tries - 1) await new Promise((r) => setTimeout(r, 700 * (i + 1)));
    }
  }
  throw lastErr;
}

// ---------------------------------------------------------------------------
// A. 当日分时（腾讯）
// ---------------------------------------------------------------------------

export interface MinuteData {
  date: string; // YYYYMMDD（接口返回的数据所属日期）
  points: { t: string; price: number }[];
  prevClose: number | null;
}

/**
 * 取当日分时。sym 形如 sh600000 / sz000001 / sh000001（指数亦可）。
 * ★ 该接口只有"今天"的数据。调用方必须校验返回的 date 与目标日一致，
 *   不一致（历史日期）时不得使用。
 */
export async function fetchMinute(sym: string): Promise<MinuteData | null> {
  const url = `https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=${sym}`;
  const j = await getJSON(url);
  const node = j?.data?.[sym];
  const rows: string[] = node?.data?.data;
  const date: string = node?.data?.date;
  if (!Array.isArray(rows) || rows.length === 0 || !date) return null;
  const points = rows
    .map((line) => {
      const p = line.split(" ");
      return { t: p[0], price: Number(p[1]) };
    })
    .filter((x) => Number.isFinite(x.price));
  // qt 数组：[3]当前价 [4]昨收（腾讯 qt 标准列位）
  const qt = node?.qt?.[sym];
  const prevClose = Array.isArray(qt) && Number.isFinite(Number(qt[4])) ? Number(qt[4]) : null;
  if (points.length === 0) return null;
  return { date, points, prevClose };
}

// ---------------------------------------------------------------------------
// B. 市场板块指数映射
// ---------------------------------------------------------------------------

export interface MarketIndex {
  sym: string; // 腾讯代码
  name: string;
  board: string; // 板块名
}

/** 按股票代码判定所属市场板块及代表指数 */
export function marketIndexOf(code: string): MarketIndex {
  if (code.startsWith("688") || code.startsWith("689"))
    return { sym: "sh000688", name: "科创50", board: "科创板" };
  if (code.startsWith("300") || code.startsWith("301") || code.startsWith("302"))
    return { sym: "sz399006", name: "创业板指", board: "创业板" };
  if (code.startsWith("6")) return { sym: "sh000001", name: "上证指数", board: "上海主板" };
  return { sym: "sz399001", name: "深证成指", board: "深圳主板" };
}

// ---------------------------------------------------------------------------
// B2. 腾讯日K（供 ±30 交易日走势图；指数与个股通用）
// ---------------------------------------------------------------------------

export interface KBar {
  date: string; // YYYY-MM-DD
  close: number;
}

const TX_HOSTS = ["https://proxy.finance.qq.com", "https://web.ifzq.gtimg.cn"];
let txHostIdx = 0;

/** 腾讯不复权日K（只取日期与收盘，指数行列数与个股不同，勿读第7/8列） */
export async function fetchDailyCloses(
  sym: string,
  beg: string,
  end: string,
  count = 320
): Promise<KBar[]> {
  for (let h = 0; h < TX_HOSTS.length; h++) {
    const host = TX_HOSTS[(txHostIdx + h) % TX_HOSTS.length];
    try {
      const url = `${host}/ifzqgtimg/appstock/app/newfqkline/get?param=${sym},day,${beg},${end},${count},`;
      const j = await getJSON(url);
      const node = j?.data?.[sym];
      const rows: any[] = node?.day || node?.qfqday;
      if (!Array.isArray(rows)) throw new Error("no day rows");
      txHostIdx = (txHostIdx + h) % TX_HOSTS.length;
      return rows
        .map((r) => ({ date: String(r[0]), close: Number(r[2]) }))
        .filter((b) => Number.isFinite(b.close));
    } catch {
      /* 轮换下一个域名 */
    }
  }
  return [];
}

// ---------------------------------------------------------------------------
// C. 概念板块
// ---------------------------------------------------------------------------

export interface BoardInfo {
  bk: string; // BK 代码
  name: string;
  chg: number | null; // 当日涨跌幅（今日快照口径；历史日期时由调用方回填）
}

/** 概念板块全表（东财 m:90+t:3，约 500 个）。返回 Map<BK, name>。 */
export async function fetchConceptTable(): Promise<Map<string, string>> {
  const out = new Map<string, string>();
  for (let pn = 1; pn <= 8; pn++) {
    const url = `https://push2delay.eastmoney.com/api/qt/clist/get?pn=${pn}&pz=100&po=1&np=1&fltt=2&invt=2&fid=f12&fs=m:90+t:3&fields=f12,f14`;
    const j = await getJSON(url);
    const diff = j?.data?.diff;
    if (!Array.isArray(diff) || diff.length === 0) break;
    for (const d of diff) if (d.f12 && d.f14) out.set(String(d.f12), String(d.f14));
    if (out.size >= (j?.data?.total ?? 0)) break;
  }
  return out;
}

/** 个股所属板块（东财 slist spt=3，含概念/地域/风格，调用方与概念全表取交集过滤） */
export async function fetchStockBoards(code: string): Promise<BoardInfo[]> {
  const secid = (code.startsWith("6") ? "1." : "0.") + code;
  const url = `https://push2delay.eastmoney.com/api/qt/slist/get?spt=3&fltt=2&invt=2&np=1&pn=1&pz=50&fid=f3&po=1&secid=${secid}&fields=f12,f14,f3`;
  const j = await getJSON(url);
  const diff = j?.data?.diff;
  if (!Array.isArray(diff)) return [];
  return diff
    .filter((d: any) => d.f12 && d.f14)
    .map((d: any) => ({
      bk: String(d.f12),
      name: String(d.f14),
      chg: Number.isFinite(Number(d.f3)) ? Number(d.f3) : null,
    }));
}

export interface BoardMember {
  code: string;
  name: string;
  chg: number | null; // 今日快照涨跌幅；历史日期时由调用方用全市场回算数据覆盖
}

/** 板块成分股（今日快照，含实时涨跌幅），分页取全 */
export async function fetchBoardMembers(bk: string): Promise<BoardMember[]> {
  const out: BoardMember[] = [];
  for (let pn = 1; pn <= 12; pn++) {
    const url = `https://push2delay.eastmoney.com/api/qt/clist/get?pn=${pn}&pz=100&po=1&np=1&fltt=2&invt=2&fid=f3&fs=b:${bk}&fields=f12,f14,f3`;
    const j = await getJSON(url);
    const diff = j?.data?.diff;
    if (!Array.isArray(diff) || diff.length === 0) break;
    for (const d of diff)
      out.push({
        code: String(d.f12),
        name: String(d.f14),
        chg: Number.isFinite(Number(d.f3)) ? Number(d.f3) : null,
      });
    if (out.length >= (j?.data?.total ?? 0)) break;
  }
  return out;
}

/** 板块当日分时（push2delay trends2，仅当天）。返回 {date, points, prevClose} */
export async function fetchBoardTrends(bk: string): Promise<MinuteData | null> {
  const url = `https://push2delay.eastmoney.com/api/qt/stock/trends2/get?secid=90.${bk}&ndays=1&iscr=0&fields1=f1,f2,f3,f4,f5,f8&fields2=f51,f53`;
  const j = await getJSON(url);
  const trends: string[] = j?.data?.trends;
  if (!Array.isArray(trends) || trends.length === 0) return null;
  const points = trends
    .map((line) => {
      const [dt, v] = line.split(",");
      return { t: dt.slice(11), price: Number(v), d: dt.slice(0, 10) };
    })
    .filter((x) => Number.isFinite(x.price));
  if (points.length === 0) return null;
  const prevClose = Number.isFinite(Number(j?.data?.preClose)) ? Number(j.data.preClose) : null;
  return { date: (points[0] as any).d.replace(/-/g, ""), points, prevClose };
}

/**
 * 板块历史日K —— ★机会性数据源，带全局熔断★
 * push2his 系域名对数据中心网络间歇拒绝（502/拒连），连续失败达到阈值后
 * 本进程内不再尝试，调用方必须优雅降级（无图+注明原因）。
 */
let bkKlineFails = 0;
const BK_KLINE_BREAKER = 5;

export function boardKlineBreakerOpen(): boolean {
  return bkKlineFails >= BK_KLINE_BREAKER;
}

export async function fetchBoardKline(bk: string, beg: string, end: string): Promise<KBar[]> {
  if (boardKlineBreakerOpen()) return [];
  const hosts = ["92.push2his.eastmoney.com", "push2his.eastmoney.com"];
  for (const host of hosts) {
    try {
      const url = `https://${host}/api/qt/stock/kline/get?secid=90.${bk}&klt=101&fqt=0&beg=${beg}&end=${end}&fields1=f1,f2,f3&fields2=f51,f53`;
      const j = await getJSON(url, 1, 8000); // 单次尝试，快速失败
      const klines: string[] = j?.data?.klines;
      if (Array.isArray(klines) && klines.length > 0) {
        bkKlineFails = 0;
        return klines
          .map((l) => {
            const p = l.split(",");
            return { date: p[0], close: Number(p[1]) };
          })
          .filter((b) => Number.isFinite(b.close));
      }
    } catch {
      /* 下一个域名 */
    }
  }
  bkKlineFails++;
  return [];
}

// ---------------------------------------------------------------------------
// D. 龙虎榜（东财 datacenter，历史日期均有数据，稳定）
// ---------------------------------------------------------------------------

export interface LhbEntry {
  code: string;
  name: string;
  explanation: string; // 上榜原因
  netBuy: number | null; // 龙虎榜净买入（元）
  lhbAmount: number | null; // 龙虎榜成交额（元）
  amountRatio: number | null; // 占总成交比例 %
}

export interface LhbSeat {
  dept: string; // 营业部名称
  buy: number | null; // 买入金额（元）
  sell: number | null;
  net: number | null;
}

async function dcGet(reportName: string, filter: string, pageSize: number, pageNumber: number) {
  const url =
    `https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=${reportName}` +
    `&columns=ALL&filter=${encodeURIComponent(filter)}&pageSize=${pageSize}&pageNumber=${pageNumber}` +
    `&source=WEB&client=WEB`;
  return getJSON(url);
}

/** 当日龙虎榜全表。返回 Map<code, LhbEntry[]>（一只股票可因多个原因多次上榜） */
export async function fetchLhbList(date: string): Promise<Map<string, LhbEntry[]>> {
  const out = new Map<string, LhbEntry[]>();
  let pages = 1;
  for (let pn = 1; pn <= pages && pn <= 20; pn++) {
    const j = await dcGet(
      "RPT_DAILYBILLBOARD_DETAILSNEW",
      `(TRADE_DATE='${date}')`,
      200,
      pn
    );
    const result = j?.result;
    if (!result?.data) break;
    pages = Number(result.pages) || 1;
    for (const d of result.data) {
      const e: LhbEntry = {
        code: String(d.SECURITY_CODE),
        name: String(d.SECURITY_NAME_ABBR ?? ""),
        explanation: String(d.EXPLANATION ?? ""),
        netBuy: numOrNull(d.BILLBOARD_NET_AMT),
        lhbAmount: numOrNull(d.BILLBOARD_DEAL_AMT),
        amountRatio: numOrNull(d.DEAL_AMOUNT_RATIO),
      };
      const arr = out.get(e.code) ?? [];
      arr.push(e);
      out.set(e.code, arr);
    }
  }
  return out;
}

/** 某股当日龙虎榜席位明细（买入榜前5 + 卖出榜前5） */
export async function fetchLhbSeats(
  code: string,
  date: string
): Promise<{ buy: LhbSeat[]; sell: LhbSeat[] }> {
  const filter = `(TRADE_DATE='${date}')(SECURITY_CODE="${code}")`;
  const [jb, js] = await Promise.all([
    dcGet("RPT_BILLBOARD_DAILYDETAILSBUY", filter, 50, 1),
    dcGet("RPT_BILLBOARD_DAILYDETAILSSELL", filter, 50, 1),
  ]);
  const toSeats = (rows: any[]): LhbSeat[] =>
    (rows ?? [])
      .map((d) => ({
        dept: String(d.OPERATEDEPT_NAME ?? ""),
        buy: numOrNull(d.BUY),
        sell: numOrNull(d.SELL),
        net: numOrNull(d.NET),
        _rank: Number(d.RANK ?? 99),
        _expl: String(d.EXPLANATION ?? ""),
      }))
      .sort((a: any, b: any) => a._rank - b._rank);
  // 同一股票多榜单原因会重复席位：按 (营业部+买+卖) 去重
  const dedupe = (seats: LhbSeat[]) => {
    const seen = new Set<string>();
    return seats.filter((s) => {
      const k = `${s.dept}|${s.buy}|${s.sell}`;
      if (seen.has(k)) return false;
      seen.add(k);
      return true;
    });
  };
  return {
    buy: dedupe(toSeats(jb?.result?.data)).slice(0, 5),
    sell: dedupe(toSeats(js?.result?.data)).slice(0, 5),
  };
}

function numOrNull(v: any): number | null {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

// ---------------------------------------------------------------------------
// E. SVG 渲染（零依赖手写，延续 v2 风格：300×80，涨 #c0392b 跌 #1e8449）
// ---------------------------------------------------------------------------

const UP = "#c0392b";
const DOWN = "#1e8449";
const FLAT = "#888";

function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function polyline(vals: number[], w: number, h: number, pad: number, lo: number, hi: number): string {
  const span = hi - lo || 1;
  const n = vals.length;
  return vals
    .map((v, i) => {
      const x = pad + ((w - 2 * pad) * i) / Math.max(1, n - 1);
      const y = h - pad - ((h - 2 * pad) * (v - lo)) / span;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}

/**
 * 分时图：价格折线 + 昨收虚线基准。颜色按收盘相对昨收。
 * 分时点不足全天（如盘中生成）也能画，x 轴按 241 分钟满刻度铺开。
 */
export function minuteSVG(md: MinuteData, title: string): string {
  const w = 300, h = 80, pad = 6;
  const vals = md.points.map((p) => p.price);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (md.prevClose != null) {
    lo = Math.min(lo, md.prevClose);
    hi = Math.max(hi, md.prevClose);
  }
  const span = hi - lo || 1;
  const total = 241; // 9:30-15:00 共241个分钟点
  const n = vals.length;
  const pts = vals
    .map((v, i) => {
      const x = pad + ((w - 2 * pad) * i) / Math.max(total - 1, n - 1);
      const y = h - pad - ((h - 2 * pad) * (v - lo)) / span;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const last = vals[n - 1];
  const color = md.prevClose == null ? FLAT : last > md.prevClose ? UP : last < md.prevClose ? DOWN : FLAT;
  let base = "";
  if (md.prevClose != null) {
    const by = h - pad - ((h - 2 * pad) * (md.prevClose - lo)) / span;
    base = `<line x1="${pad}" y1="${by.toFixed(1)}" x2="${w - pad}" y2="${by.toFixed(1)}" stroke="#bbb" stroke-width="0.8" stroke-dasharray="3,3"/>`;
  }
  return `<svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" role="img" aria-label="${esc(title)}">${base}<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.4"/></svg>`;
}

/**
 * ±30 交易日日K收盘折线图，标记目标日（竖线+圆点）。
 * bars 必须已按日期升序；targetDate 形如 YYYY-MM-DD。
 */
export function klineWindowSVG(bars: KBar[], targetDate: string, title: string): string {
  const w = 300, h = 80, pad = 6;
  const idx = bars.findIndex((b) => b.date === targetDate);
  if (idx < 0 || bars.length < 2) return "";
  const from = Math.max(0, idx - 30);
  const to = Math.min(bars.length - 1, idx + 30);
  const win = bars.slice(from, to + 1);
  const vals = win.map((b) => b.close);
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const span = hi - lo || 1;
  const n = vals.length;
  const X = (i: number) => pad + ((w - 2 * pad) * i) / Math.max(1, n - 1);
  const Y = (v: number) => h - pad - ((h - 2 * pad) * (v - lo)) / span;
  const pts = vals.map((v, i) => `${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(" ");
  const ti = idx - from;
  const prev = ti > 0 ? vals[ti - 1] : null;
  const color = prev == null ? FLAT : vals[ti] > prev ? UP : vals[ti] < prev ? DOWN : FLAT;
  const tx = X(ti).toFixed(1), ty = Y(vals[ti]).toFixed(1);
  return (
    `<svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" role="img" aria-label="${esc(title)}">` +
    `<polyline points="${pts}" fill="none" stroke="#7f8c8d" stroke-width="1.2"/>` +
    `<line x1="${tx}" y1="${pad}" x2="${tx}" y2="${h - pad}" stroke="${color}" stroke-width="0.8" stroke-dasharray="2,2"/>` +
    `<circle cx="${tx}" cy="${ty}" r="2.6" fill="${color}"/>` +
    `</svg>`
  );
}
