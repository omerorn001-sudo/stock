/**
 * enrich-run.ts — 三期增强功能的编排与渲染层
 *
 * 职责：给定入选的 200 只股票，批量构建增强数据（双走势图、概念板块、龙虎榜），
 * 并提供卡片内 HTML 片段、CSS、meta 口径说明的渲染函数。
 * report-by-date.ts 只需 4 处最小改动接入（import / build / render / css+meta）。
 *
 * 口径要点（与报告 meta 区说明保持一致，改逻辑必须同步改说明）：
 * - 当日走势图：分时数据仅"今天"可得；历史日期降级为 ±30 个交易日收盘价窗口图并标记当日。
 * - 板块涨幅：今天取东财板块实时快照；历史日期为"沪深成分股当日涨跌幅算术平均"的估算值（标 *）。
 * - 概念成分股名单取自当前时点（东财不提供历史成分），历史日期存在时点错位，如实披露。
 * - 板块历史K线走 push2his 机会源（不稳定、带熔断），取不到则无图并注明。
 * - 龙虎榜为东财披露的历史数据，稳定可回溯。
 */

import {
  fetchMinute,
  marketIndexOf,
  fetchDailyCloses,
  fetchConceptTable,
  fetchStockBoards,
  fetchBoardMembers,
  fetchBoardTrends,
  fetchBoardKline,
  boardKlineBreakerOpen,
  fetchLhbList,
  fetchLhbSeats,
  minuteSVG,
  klineWindowSVG,
  type BoardMember,
  type LhbEntry,
  type LhbSeat,
  type MinuteData,
} from "./enrich";

// ---------- 类型 ----------

export interface SelStock { code: string; name: string }

export interface BuildOpts {
  date: string;               // YYYY-MM-DD
  isToday: boolean;           // 报告日是否为今天（决定分时/降级路径）
  selected: SelStock[];       // 入选的 200 只
  pctMap: Map<string, number>;// 全市场 code→当日涨跌幅（来自回算阶段，历史口径的唯一真相源）
  concurrency?: number;
}

interface ExpandedBoard {
  bk: string;
  name: string;
  chg: number | null;
  chgEst: boolean;            // 涨幅是否为成分股均值估算（历史口径）
  upN: number; flatN: number; downN: number; naN: number; totalN: number;
  topUp: BoardMember[];       // 领涨前10
  topDown: BoardMember[];     // 领跌前10
  chartSvg: string;           // 可能为空串（降级）
  chartLbl: string;
}

interface CardEnrich {
  dayChartSvg: string; dayChartLbl: string;
  idxChartSvg: string; idxChartLbl: string;
  conceptChips: { name: string; chg: number | null }[]; // 全部概念（过滤后）
  conceptErr: string;         // 概念获取失败原因（空=正常）
  expanded: ExpandedBoard[];  // 展开的前2个
  lhb: { entries: LhbEntry[]; buy: LhbSeat[]; sell: LhbSeat[] } | null;
  lhbErr: string;             // 龙虎榜明细获取失败原因（空=正常或未上榜）
}

export interface EnrichResult {
  map: Map<string, CardEnrich>;
  metaNote: string;           // 拼进报告 meta 区的口径说明
  stats: { lhbCount: number; boardChartMiss: number; conceptFail: number };
}

// ---------- 工具 ----------

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const compact = (s: string) => s.replace(/-/g, "");
const symTX = (code: string) => (code.startsWith("6") ? "sh" : "sz") + code;

/** 泛化/指数成份类板块：仍列名但不参与"展开"（它们不是真正的题材概念） */
const EXPAND_BLOCK =
  /重仓|融资融券|沪股通|深股通|富时|MSCI|标普|标准普尔|百元股|低价股|破净|^大盘|^中盘|^小盘|^微盘|^昨日|次新股|转债|^AH|茅指数|宁组合|金股|成份|综$|指数|证50|证100|证180|证380|证500|深成500|沪深300|中证|上证|深证|多板|连板|涨停|首板/;

function shiftDate(date: string, days: number): string {
  return new Date(new Date(date + "T00:00:00Z").getTime() + days * 86400e3).toISOString().slice(0, 10);
}

async function runPool<T, R>(items: T[], limit: number, fn: (x: T) => Promise<R>): Promise<R[]> {
  const out: R[] = new Array(items.length);
  let idx = 0;
  const worker = async () => {
    while (true) {
      const i = idx++;
      if (i >= items.length) break;
      out[i] = await fn(items[i]);
    }
  };
  await Promise.all(Array.from({ length: limit }, worker));
  return out;
}

// ---------- 构建 ----------

export async function buildEnrichment(opts: BuildOpts): Promise<EnrichResult> {
  const { date, isToday, selected, pctMap } = opts;
  const CC = opts.concurrency ?? 6;
  const todayBJ = new Date(Date.now() + 8 * 3600e3).toISOString().slice(0, 10);

  console.log(`增强阶段：概念板块 / 走势图 / 龙虎榜（${isToday ? "当日分时" : "历史日期降级"}路径）…`);

  // 0) 概念板块全表 + 当日龙虎榜（各1次，失败则该功能整体降级并披露）
  let conceptTable = new Map<string, string>();
  let conceptTableErr = "";
  try {
    conceptTable = await fetchConceptTable();
    if (conceptTable.size < 100) throw new Error(`概念全表仅 ${conceptTable.size} 条，疑似接口异常`);
  } catch (e) {
    conceptTableErr = `概念板块全表获取失败（${String(e).slice(0, 80)}）`;
    console.warn(conceptTableErr);
  }
  let lhbMap = new Map<string, LhbEntry[]>();
  let lhbListErr = "";
  try {
    lhbMap = await fetchLhbList(date);
    console.log(`龙虎榜：${date} 全市场上榜 ${lhbMap.size} 只`);
  } catch (e) {
    lhbListErr = `龙虎榜数据获取失败（${String(e).slice(0, 80)}）`;
    console.warn(lhbListErr);
  }

  // 1) 四大指数走势图（全局缓存）
  const idxCharts = new Map<string, { svg: string; lbl: string }>();
  for (const code of ["600000", "000001", "300750", "688111"]) {
    const mi = marketIndexOf(code);
    if (idxCharts.has(mi.sym)) continue;
    let svg = "", lbl = "";
    try {
      if (isToday) {
        const md = await fetchMinute(mi.sym);
        if (md && md.date === compact(date)) {
          svg = minuteSVG(md, mi.name);
          lbl = `${mi.name} 当日分时`;
        }
      }
      if (!svg) {
        const bars = await fetchDailyCloses(mi.sym, shiftDate(date, -75), todayBJ < shiftDate(date, 65) ? todayBJ : shiftDate(date, 65));
        svg = klineWindowSVG(bars, date, mi.name);
        lbl = `${mi.name} 前后30个交易日收盘走势（圆点=当日）`;
      }
    } catch { /* svg 保持空 */ }
    idxCharts.set(mi.sym, { svg, lbl });
  }

  // 2) 板块级数据（按 BK 全局去重缓存）
  //    成员涨跌口径：今天=东财实时快照；历史=全市场回算 pctMap（北交所等不在样本的成员计为无数据）
  const memberCache = new Map<string, Promise<BoardMember[]>>();
  const getMembers = (bk: string): Promise<BoardMember[]> => {
    let p = memberCache.get(bk);
    if (!p) {
      p = fetchBoardMembers(bk).catch(() => [] as BoardMember[]);
      memberCache.set(bk, p);
    }
    return p;
  };
  const chartCache = new Map<string, Promise<{ svg: string; lbl: string }>>();
  const getBoardChart = (bk: string, name: string): Promise<{ svg: string; lbl: string }> => {
    let p = chartCache.get(bk);
    if (!p) {
      p = (async () => {
        try {
          if (isToday) {
            const md = await fetchBoardTrends(bk);
            if (md && md.date === compact(date)) return { svg: minuteSVG(md, name), lbl: `${name} 当日分时` };
          } else {
            const bars = await fetchBoardKline(bk, compact(shiftDate(date, -75)), compact(todayBJ < shiftDate(date, 65) ? todayBJ : shiftDate(date, 65)));
            const svg = klineWindowSVG(bars, date, name);
            if (svg) return { svg, lbl: `${name} 前后30个交易日走势（圆点=当日）` };
          }
        } catch { /* fallthrough */ }
        return { svg: "", lbl: "" };
      })();
      chartCache.set(bk, p);
    }
    return p;
  };

  /** 计算某板块在报告日的涨跌统计与涨幅 */
  function boardStats(members: BoardMember[], snapshotChg: number | null) {
    const withChg = members.map((m) => ({
      ...m,
      chg: isToday ? m.chg : (pctMap.has(m.code) ? pctMap.get(m.code)! : null),
    }));
    const known = withChg.filter((m) => m.chg !== null) as (BoardMember & { chg: number })[];
    const upN = known.filter((m) => m.chg > 0).length;
    const downN = known.filter((m) => m.chg < 0).length;
    const flatN = known.filter((m) => m.chg === 0).length;
    const naN = withChg.length - known.length;
    const sorted = [...known].sort((a, b) => b.chg - a.chg);
    let chg: number | null; let chgEst: boolean;
    if (isToday && snapshotChg !== null) { chg = snapshotChg; chgEst = false; }
    else if (known.length) { chg = +(known.reduce((s, m) => s + m.chg, 0) / known.length).toFixed(2); chgEst = true; }
    else { chg = null; chgEst = false; }
    return { upN, flatN, downN, naN, totalN: withChg.length, topUp: sorted.slice(0, 10), topDown: sorted.slice(-10).reverse().filter((m) => m.chg < 0), chg, chgEst };
  }

  // 3) 龙虎榜席位明细（仅入选且上榜的股票；串行小池防限流）
  const lhbSelected = selected.filter((s) => lhbMap.has(s.code));
  console.log(`入选 200 只中上榜龙虎榜：${lhbSelected.length} 只`);
  const seatMap = new Map<string, { buy: LhbSeat[]; sell: LhbSeat[] } | null>();
  await runPool(lhbSelected, 3, async (s) => {
    try {
      seatMap.set(s.code, await fetchLhbSeats(s.code, date));
    } catch {
      seatMap.set(s.code, null);
    }
    await sleep(200);
  });

  // 4) 逐股构建
  let done = 0;
  const map = new Map<string, CardEnrich>();
  await runPool(selected, CC, async (s) => {
    const ce: CardEnrich = {
      dayChartSvg: "", dayChartLbl: "",
      idxChartSvg: "", idxChartLbl: "",
      conceptChips: [], conceptErr: conceptTableErr, expanded: [],
      lhb: null, lhbErr: "",
    };
    const sym = symTX(s.code);
    const mi = marketIndexOf(s.code);

    // 4.1 个股当日走势图
    try {
      let ok = false;
      if (isToday) {
        const md = await fetchMinute(sym);
        if (md && md.date === compact(date)) {
          ce.dayChartSvg = minuteSVG(md, `${s.name}分时`);
          ce.dayChartLbl = "当日分时走势";
          ok = true;
        }
      }
      if (!ok) {
        const bars = await fetchDailyCloses(sym, shiftDate(date, -75), todayBJ < shiftDate(date, 65) ? todayBJ : shiftDate(date, 65));
        ce.dayChartSvg = klineWindowSVG(bars, date, `${s.name}走势`);
        ce.dayChartLbl = ce.dayChartSvg ? "前后30个交易日收盘走势（圆点=当日；分时数据源仅保留当天）" : "";
      }
    } catch { /* 保持空 */ }

    // 4.2 市场板块指数图（缓存）
    const ic = idxCharts.get(mi.sym);
    if (ic?.svg) { ce.idxChartSvg = ic.svg; ce.idxChartLbl = `所属板块【${mi.board}】· ${ic.lbl}`; }

    // 4.3 概念板块
    if (!conceptTableErr) {
      try {
        const boards = await fetchStockBoards(s.code);
        const concepts = boards.filter((b) => conceptTable.has(b.bk));
        if (!concepts.length) ce.conceptErr = boards.length ? "该股未归入任何东财概念板块" : "板块归属接口未返回数据";
        // 候选（参与展开排序）：剔除泛化/成份类
        const candidates = concepts.filter((b) => !EXPAND_BLOCK.test(b.name));
        // 报告日口径的板块涨幅：今天直接用快照；历史需成分股均值 → 先取成员
        const scored = await runPool(candidates, 4, async (b) => {
          const members = await getMembers(b.bk);
          const st = boardStats(members, b.chg);
          return { b, members, st };
        });
        // 概念标签（全部概念，含泛化）：历史口径下泛化板块不算均值（未取成员），涨幅显示今天快照会误导 → 历史时标签不带涨幅，仅展开的带
        ce.conceptChips = concepts.map((b) => {
          if (isToday) return { name: b.name, chg: b.chg };
          const hit = scored.find((x) => x.b.bk === b.bk);
          return { name: b.name, chg: hit ? hit.st.chg : null };
        });
        const ranked = scored
          .filter((x) => x.st.chg !== null && x.st.totalN >= 3)
          .sort((a, b2) => (b2.st.chg as number) - (a.st.chg as number))
          .slice(0, 2);
        for (const { b, st } of ranked) {
          const chart = await getBoardChart(b.bk, b.name);
          ce.expanded.push({
            bk: b.bk, name: b.name, chg: st.chg, chgEst: st.chgEst,
            upN: st.upN, flatN: st.flatN, downN: st.downN, naN: st.naN, totalN: st.totalN,
            topUp: st.topUp, topDown: st.topDown,
            chartSvg: chart.svg, chartLbl: chart.lbl,
          });
        }
      } catch (e) {
        ce.conceptErr = `概念板块获取失败（${String(e).slice(0, 60)}）`;
      }
    }

    // 4.4 龙虎榜
    const entries = lhbMap.get(s.code);
    if (entries?.length) {
      const seats = seatMap.get(s.code);
      if (seats) ce.lhb = { entries, buy: seats.buy, sell: seats.sell };
      else { ce.lhb = { entries, buy: [], sell: [] }; ce.lhbErr = "席位明细接口未返回数据"; }
    } else if (lhbListErr) {
      ce.lhbErr = lhbListErr;
    }

    map.set(s.code, ce);
    done++;
    if (done % 25 === 0) console.log(`增强进度 ${done}/${selected.length}`);
  });

  // 5) 统计与 meta 口径说明
  const lhbCount = selected.filter((s) => lhbMap.has(s.code)).length;
  const boardChartMiss = [...map.values()].reduce(
    (n, ce) => n + ce.expanded.filter((e) => !e.chartSvg).length, 0);
  const conceptFail = [...map.values()].filter((ce) => ce.conceptErr && !ce.conceptChips.length).length;

  const notes: string[] = [];
  if (isToday) {
    notes.push(`个股/指数/概念板块走势图为<b>当日分时</b>（腾讯/东财公开接口，报告生成时刻之前的分时点）`);
    notes.push(`概念板块涨跌幅与成分股涨跌为东财<b>实时快照</b>口径`);
  } else {
    notes.push(`分时数据源仅保留当天，历史日期的"当日走势图"以<b>前后30个交易日收盘价窗口图</b>替代（圆点标记当日）`);
    notes.push(`历史日期的概念板块涨跌幅为该板块<b>沪深成分股当日涨跌幅算术平均</b>的估算值（标 *，与东财板块指数口径存在差异；北交所等不在本报告样本内的成分股不参与统计，计为"无数据"）`);
  }
  notes.push(`概念板块归属与成分股名单取自<b>当前时点</b>的东财板块体系（数据源不提供历史成分），${isToday ? "" : "历史日期报告存在成分变动的时点错位；"}"融资融券""沪股通"等交易属性类与指数成份类板块仅列名、不展开详情`);
  if (!isToday && boardChartMiss > 0) {
    notes.push(`概念板块历史K线依赖的接口对本运行环境间歇拒绝服务，本次有 ${boardChartMiss} 处板块走势图未能取得（已注明），属数据源限制而非板块无行情`);
  }
  if (conceptTableErr || conceptFail > 0) notes.push(`本次有 ${conceptFail || "部分"} 只个股概念板块获取失败，已在卡片内注明`);
  if (lhbListErr) notes.push(`<b>龙虎榜数据本次获取失败</b>：${lhbListErr}`);
  else notes.push(`龙虎榜为交易所披露、经东财数据中心整理的当日榜单，两市入选个股中共 ${lhbCount} 只上榜；席位明细为买入/卖出前5营业部`);

  return {
    map,
    metaNote: notes.join("。") + "。",
    stats: { lhbCount, boardChartMiss, conceptFail },
  };
}

// ---------- 渲染 ----------

const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const chgColor = (v: number | null) => (v === null ? "#888" : v > 0 ? "#c0392b" : v < 0 ? "#1e8449" : "#888");
const chgStr = (v: number | null, est = false) => (v === null ? "—" : (v > 0 ? "+" : "") + v.toFixed(2) + "%" + (est ? "*" : ""));
const fmtAmt = (v: number | null) => (v === null ? "—" : Math.abs(v) >= 1e8 ? (v / 1e8).toFixed(2) + " 亿" : (v / 1e4).toFixed(0) + " 万");

function membersTable(e: ExpandedBoard): string {
  const row = (m: BoardMember) =>
    `<tr><td>${esc(m.name)}<span class="mcode">${m.code}</span></td><td style="color:${chgColor(m.chg)}">${chgStr(m.chg)}</td></tr>`;
  const upRows = e.topUp.map(row).join("");
  const downRows = e.topDown.map(row).join("");
  return `<div class="bkmem">
    <div class="bkmemcol"><div class="bkmemttl">领涨成分股</div><table class="bkt">${upRows || `<tr><td colspan="2" class="dim">无上涨成分</td></tr>`}</table></div>
    <div class="bkmemcol"><div class="bkmemttl">领跌成分股</div><table class="bkt">${downRows || `<tr><td colspan="2" class="dim">无下跌成分</td></tr>`}</table></div>
  </div>`;
}

function expandedHtml(e: ExpandedBoard): string {
  const naNote = e.naN ? `，无数据 ${e.naN}` : "";
  const chart = e.chartSvg
    ? `<div class="bkchart">${e.chartSvg}<div class="chartlbl">${esc(e.chartLbl)}</div></div>`
    : `<div class="nochart">板块走势图未能取得（数据源对本运行环境间歇拒绝服务）</div>`;
  return `<div class="bkpanel">
    <div class="bkhead"><b>${esc(e.name)}</b> <span style="color:${chgColor(e.chg)};font-weight:bold">${chgStr(e.chg, e.chgEst)}</span>
      <span class="bkstat">沪深成分 ${e.totalN} 只：<span style="color:#c0392b">涨 ${e.upN}</span> · 平 ${e.flatN} · <span style="color:#1e8449">跌 ${e.downN}</span>${naNote}</span></div>
    ${chart}
    ${membersTable(e)}
  </div>`;
}

function lhbHtml(ce: CardEnrich): string {
  if (!ce.lhb) {
    return ce.lhbErr
      ? `<div class="lhbbox"><div class="httl">龙虎榜</div><div class="noholder">未能获取：${esc(ce.lhbErr)}</div></div>`
      : `<div class="lhbbox"><div class="httl">龙虎榜</div><div class="dim">当日未登上龙虎榜</div></div>`;
  }
  const reasons = [...new Set(ce.lhb.entries.map((x) => x.explanation).filter(Boolean))];
  const head = ce.lhb.entries[0];
  const seatRow = (s: LhbSeat) =>
    `<tr><td class="dept">${esc(s.dept)}</td><td>${fmtAmt(s.buy)}</td><td>${fmtAmt(s.sell)}</td><td style="color:${chgColor(s.net)}">${fmtAmt(s.net)}</td></tr>`;
  const seatTable = (title: string, seats: LhbSeat[]) =>
    seats.length
      ? `<div class="seatcol"><div class="bkmemttl">${title}</div><table class="seatt"><tr><th>营业部/席位</th><th>买入</th><th>卖出</th><th>净额</th></tr>${seats.map(seatRow).join("")}</table></div>`
      : "";
  const summary = head
    ? `<span class="bkstat">龙虎榜成交 ${fmtAmt(head.lhbAmount)}${head.amountRatio !== null ? `（占当日成交 ${head.amountRatio.toFixed(1)}%）` : ""} · 净买入 <span style="color:${chgColor(head.netBuy)}">${fmtAmt(head.netBuy)}</span></span>`
    : "";
  return `<div class="lhbbox">
    <div class="httl">龙虎榜 <span class="lhbtag">当日上榜</span></div>
    <div class="lhbreason">上榜原因：${reasons.map(esc).join("；") || "—"}</div>
    <div>${summary}</div>
    ${ce.lhbErr ? `<div class="noholder">席位明细：${esc(ce.lhbErr)}</div>` : `<div class="seats">${seatTable("买入金额前5席位", ce.lhb.buy)}${seatTable("卖出金额前5席位", ce.lhb.sell)}</div>`}
  </div>`;
}

/** 卡片内新增区块（插入在 .cbody 之后、卡片 div 结束之前） */
export function renderEnrichHtml(map: Map<string, CardEnrich>, code: string): string {
  const ce = map.get(code);
  if (!ce) return "";
  const charts =
    (ce.dayChartSvg ? `<div class="chartbox">${ce.dayChartSvg}<div class="chartlbl">${esc(ce.dayChartLbl)}</div></div>` : `<div class="chartbox"><div class="nochart">当日走势图未能取得</div></div>`) +
    (ce.idxChartSvg ? `<div class="chartbox">${ce.idxChartSvg}<div class="chartlbl">${esc(ce.idxChartLbl)}</div></div>` : "");
  const chips = ce.conceptChips.length
    ? `<div class="chips">${ce.conceptChips.map((c) => `<span class="chip">${esc(c.name)}${c.chg !== null ? `<i style="color:${chgColor(c.chg)}"> ${chgStr(c.chg)}</i>` : ""}</span>`).join("")}</div>`
    : `<div class="noholder">未能获取：${esc(ce.conceptErr || "无概念板块数据")}</div>`;
  const expanded = ce.expanded.length
    ? ce.expanded.map(expandedHtml).join("")
    : ce.conceptChips.length ? `<div class="dim">（无可展开的题材概念板块）</div>` : "";
  return `<div class="enrich">
  <div class="charts2">${charts}</div>
  <div class="cptbox"><div class="httl">所属概念板块（${ce.conceptChips.length} 个，展开当日最强 ${ce.expanded.length} 个）</div>${chips}${expanded}</div>
  ${lhbHtml(ce)}
</div>`;
}

/** 新增区块的样式（拼入 <style>） */
export function enrichCss(): string {
  return `
.enrich{border-top:1px dashed #e3e3e3;padding:10px 14px 12px;background:#fcfcfd}
.charts2{display:flex;flex-wrap:wrap;gap:18px;margin-bottom:8px}
.chartbox{flex:0 0 auto}
.cptbox{margin:6px 0 4px}
.chips{margin:4px 0 6px;line-height:2}
.chip{display:inline-block;background:#eef2f7;border-radius:10px;padding:1px 9px;font-size:12px;color:#345;margin-right:6px}
.chip i{font-style:normal;font-size:11.5px}
.bkpanel{border:1px solid #e8eaee;border-radius:6px;padding:8px 10px;margin:6px 0;background:#fff}
.bkhead{font-size:13.5px;margin-bottom:4px}
.bkstat{color:#666;font-size:12px;margin-left:10px}
.bkchart{margin:4px 0}
.bkmem{display:flex;flex-wrap:wrap;gap:24px;margin-top:4px}
.bkmemcol{flex:1;min-width:220px}
.bkmemttl{font-size:12px;font-weight:600;color:#556;margin-bottom:2px}
table.bkt{border-collapse:collapse;font-size:12px;width:100%}
table.bkt td{padding:2px 8px 2px 0;border-bottom:1px dashed #f0f0f0}
table.bkt td:last-child{text-align:right;font-weight:600;white-space:nowrap}
.mcode{color:#999;font-family:Consolas,monospace;font-size:11px;margin-left:5px}
.lhbbox{margin-top:8px}
.lhbtag{background:#c0392b;color:#fff;border-radius:4px;padding:1px 7px;font-size:11.5px;font-weight:normal;margin-left:6px}
.lhbreason{font-size:12.5px;color:#555;margin:3px 0}
.seats{display:flex;flex-wrap:wrap;gap:24px;margin-top:5px}
.seatcol{flex:1;min-width:300px}
table.seatt{border-collapse:collapse;font-size:12px;width:100%}
table.seatt th{text-align:left;color:#888;font-weight:normal;padding:2px 8px 2px 0;border-bottom:1px solid #eee}
table.seatt td{padding:3px 8px 3px 0;border-bottom:1px dashed #f0f0f0;white-space:nowrap}
table.seatt td.dept{white-space:normal;max-width:320px}
table.seatt td:not(.dept){text-align:right}
.dim{color:#999;font-size:12px}
`;
}
