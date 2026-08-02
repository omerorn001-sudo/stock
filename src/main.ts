/**
 * A股每日涨跌幅 TOP100 报告生成器
 * - 沪深两市A股（不含北交所）涨幅前100 + 跌幅前100
 * - 数据源：东方财富公开行情接口（主）、腾讯财经公开行情接口（K线兜底）
 * - 输出：reports/{YYYY-MM-DD}.html 与 reports/latest.html
 * - 非交易日自动跳过（exit 0，不产出文件）
 *
 * 运行：bun run src/main.ts
 * 可选环境变量：REPORT_DATE=YYYY-MM-DD（指定日期，仅用于测试/补跑，须为交易日）
 */

// ---------- 日期（北京时间） ----------
const bjNow = new Date(Date.now() + 8 * 3600e3);
const TODAY = process.env.REPORT_DATE || bjNow.toISOString().slice(0, 10);
const begDateObj = new Date(new Date(TODAY + "T00:00:00Z").getTime() - 185 * 86400e3);
const BEG = begDateObj.toISOString().slice(0, 10).replace(/-/g, ""); // K线起点 YYYYMMDD
const END = TODAY.replace(/-/g, "");
const WEEKDAYS = ["日", "一", "二", "三", "四", "五", "六"];
const weekday = WEEKDAYS[new Date(TODAY + "T00:00:00Z").getUTCDay()];

// ---------- 通用请求 ----------
async function getJSON(url: string, tries = 5): Promise<any> {
  for (let i = 0; i < tries; i++) {
    try {
      const r = await fetch(url, {
        headers: { Referer: "https://quote.eastmoney.com/", "User-Agent": "Mozilla/5.0" },
        signal: AbortSignal.timeout(20000),
      });
      if (!r.ok) throw new Error("HTTP " + r.status);
      return await r.json();
    } catch (e) {
      if (i === tries - 1) return { __error: String(e) };
      await Bun.sleep(800 * (i + 1));
    }
  }
}

// ---------- 0) 交易日检查：上证指数最新K线日期 == TODAY ----------
{
  const j = await getJSON(
    `https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=1.000001&klt=101&fqt=1&fields1=f1,f2,f3&fields2=f51,f53&beg=${BEG}&end=${END}`,
    3
  );
  const klines: string[] = j?.data?.klines || [];
  let lastDate = klines.length ? klines[klines.length - 1].split(",")[0] : "";
  if (!lastDate) {
    // 东财失败 → 腾讯源兜底（上证指数）
    const tj = await getJSON(`https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh000001,day,,,5,qfq`, 3);
    const rows = tj?.data?.sh000001?.qfqday || tj?.data?.sh000001?.day;
    if (rows?.length) lastDate = rows[rows.length - 1][0];
  }
  if (!lastDate) {
    console.error("无法获取上证指数K线（东财与腾讯源均失败），交易日检查失败。");
    process.exit(1);
  }
  if (lastDate !== TODAY) {
    console.log(`今日（${TODAY}）非交易日（最近交易日为 ${lastDate}），跳过。`);
    process.exit(0);
  }
  console.log(`交易日确认：${TODAY}（周${weekday}）`);
}

// ---------- 1) 涨跌幅榜（沪深A股，不含北交所） ----------
const FIELDS = "f2,f3,f5,f6,f8,f12,f14,f15,f16,f17,f18,f20,f21,f38,f39";
const FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"; // 深主板、创业板、沪主板、科创板

const j1 = await getJSON(`https://push2delay.eastmoney.com/api/qt/clist/get?pn=1&pz=100&po=1&np=1&fltt=2&invt=2&fid=f3&fs=${FS}&fields=${FIELDS}`);
const j2 = await getJSON(`https://push2delay.eastmoney.com/api/qt/clist/get?pn=1&pz=100&po=0&np=1&fltt=2&invt=2&fid=f3&fs=${FS}&fields=${FIELDS}`);
if (!j1?.data?.diff || !j2?.data?.diff) {
  console.error("涨跌幅榜获取失败", j1?.__error, j2?.__error);
  process.exit(1);
}
const up: any[] = j1.data.diff;
const down: any[] = j2.data.diff;
const UNIVERSE: number = j1.data.total;
console.log(`榜单获取完成：涨幅榜 ${up.length}，跌幅榜 ${down.length}，全市场样本 ${UNIVERSE} 只`);

// ---------- 2) 每股明细：K线 + 前十大流通股东 ----------
type Detail = {
  code: string;
  kline: [string, number, number | null][]; // [日期, 前复权收盘价, 当日换手率%]
  kerr: string;
  ksrc?: string;
  holders: { rank: number; name: string; type: string; num: number; ratio: number | null }[];
  holderDate: string;
  serr: string;
};
const details: Record<string, Detail> = {};

async function fetchStock(d: any): Promise<Detail> {
  const code: string = d.f12;
  const mkt = code.startsWith("6") ? 1 : 0;
  const suffix = mkt === 1 ? "SH" : "SZ";

  const kj = await getJSON(
    `https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=${mkt}.${code}&klt=101&fqt=1&fields1=f1,f2,f3&fields2=f51,f53,f61&beg=${BEG}&end=${END}`
  );
  let kline: any[] = [], kerr = "";
  if (kj?.data?.klines) {
    kline = kj.data.klines.map((s: string) => {
      const [dt, c, t] = s.split(",");
      return [dt, parseFloat(c), parseFloat(t)];
    });
  } else kerr = kj?.__error || "接口未返回K线数据";

  const sj = await getJSON(
    `https://datacenter.eastmoney.com/securities/api/data/v1/get?reportName=RPT_F10_EH_FREEHOLDERS&columns=SECUCODE,END_DATE,HOLDER_RANK,HOLDER_NAME,HOLDER_TYPE,HOLD_NUM,FREE_HOLDNUM_RATIO&filter=(SECUCODE%3D%22${code}.${suffix}%22)&pageNumber=1&pageSize=10&sortTypes=-1,1&sortColumns=END_DATE,HOLDER_RANK&source=HSF10&client=PC`
  );
  let holders: any[] = [], holderDate = "", serr = "";
  if (sj?.result?.data?.length) {
    holderDate = sj.result.data[0].END_DATE.slice(0, 10);
    holders = sj.result.data
      .filter((h: any) => h.END_DATE === sj.result.data[0].END_DATE)
      .map((h: any) => ({ rank: h.HOLDER_RANK, name: h.HOLDER_NAME, type: h.HOLDER_TYPE || "-", num: h.HOLD_NUM, ratio: h.FREE_HOLDNUM_RATIO }));
  } else serr = sj?.__error || "数据源无该股流通股东数据";

  return { code, kline, kerr, holders, holderDate, serr };
}

const all = [...up, ...down];
const queue = [...all];
let done = 0;
async function worker() {
  while (true) {
    const d = queue.shift();
    if (!d) break;
    details[d.f12] = await fetchStock(d);
    done++;
    if (done % 40 === 0) console.log(`明细进度 ${done}/${all.length}`);
  }
}
await Promise.all(Array.from({ length: 6 }, worker));
console.log(`明细获取完成：${Object.keys(details).length} 只`);

// ---------- 3) K线失败 → 腾讯财经兜底（换手率由 成交量/流通股本 推算，报告中标 *） ----------
const floatShares: Record<string, number> = {};
for (const d of all) floatShares[d.f12] = d.f39;
const kfails = Object.values(details).filter((r) => r.kerr).map((r) => r.code);
if (kfails.length) console.log(`K线失败 ${kfails.length} 只，尝试腾讯源兜底…`);
const begDash = `${BEG.slice(0, 4)}-${BEG.slice(4, 6)}-${BEG.slice(6)}`;
for (const code of kfails) {
  const pre = code.startsWith("6") ? "sh" : "sz";
  const url = `https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=${pre}${code},day,${begDash},${TODAY},130,qfq`;
  let j: any = null;
  for (let i = 0; i < 4; i++) {
    try {
      const r = await fetch(url, { signal: AbortSignal.timeout(20000) });
      if (r.ok) { j = await r.json(); break; }
    } catch {}
    await Bun.sleep(1000 * (i + 1));
  }
  const rows = j?.data?.[pre + code]?.qfqday || j?.data?.[pre + code]?.day;
  if (rows?.length) {
    const fs = floatShares[code];
    details[code].kline = rows.map((r: any[]) => {
      const vol = parseFloat(r[5]) * 100; // 手 -> 股
      const t = fs ? +((vol / fs) * 100).toFixed(2) : null;
      return [r[0], parseFloat(r[2]), t];
    });
    details[code].kerr = "";
    details[code].ksrc = "tencent";
  }
  await Bun.sleep(150);
}
const tencentCount = Object.values(details).filter((r) => r.ksrc === "tencent").length;
const remainK = Object.values(details).filter((r) => r.kerr).map((r) => r.code);
const remainS = Object.values(details).filter((r) => r.serr).map((r) => r.code);
console.log(`腾讯兜底 ${tencentCount} 只；K线仍缺 ${remainK.length}（${remainK.join(",") || "无"}）；流通股东缺 ${remainS.length}（${remainS.join(",") || "无"}）`);

// ---------- 4) 生成 HTML 报告 ----------
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

function sparkline(kline: any[], color: string): string {
  const pts = kline.map((k: any[]) => k[1]).filter((v: any) => typeof v === "number" && isFinite(v));
  if (pts.length < 2) return `<div class="nochart">区间K线不足，无法绘图</div>`;
  const W = 300, H = 80, P = 4;
  const min = Math.min(...pts), max = Math.max(...pts);
  const rng = max - min || 1;
  const step = (W - 2 * P) / (pts.length - 1);
  const path = pts.map((v, i) => `${i ? "L" : "M"}${(P + i * step).toFixed(1)},${(H - P - ((v - min) / rng) * (H - 2 * P)).toFixed(1)}`).join("");
  const d0 = kline[0][0], d1 = kline[kline.length - 1][0];
  return `<svg width="${W}" height="${H + 18}" viewBox="0 0 ${W} ${H + 18}"><rect x="0" y="0" width="${W}" height="${H}" fill="#fafafa" stroke="#e5e5e5"/><path d="${path}" fill="none" stroke="${color}" stroke-width="1.5"/><text x="2" y="${H + 13}" class="axlbl">${d0}</text><text x="${W - 2}" y="${H + 13}" text-anchor="end" class="axlbl">${d1}</text><text x="${W - 4}" y="12" text-anchor="end" class="axlbl">高 ${max.toFixed(2)} / 低 ${min.toFixed(2)}</text></svg>`;
}

function turnover5(r: Detail): string {
  const k = r.kline;
  if (!k || k.length === 0) return "—（无K线数据）";
  const last5 = k.slice(-5);
  const vals = last5.map((x: any[]) => x[2]).filter((v: any) => typeof v === "number" && isFinite(v));
  if (vals.length === 0) return "—（无换手数据）";
  const sum = vals.reduce((a: number, b: number) => a + b, 0);
  const note = last5.length < 5 ? `（上市不足5日，仅${last5.length}日）` : "";
  const approx = r.ksrc === "tencent" ? "*" : "";
  return sum.toFixed(2) + "%" + approx + note;
}

function holdersHtml(r: Detail): string {
  if (r.serr || !r.holders?.length) {
    return `<div class="noholder">未能获取：${r.serr || "数据源无该股流通股东数据"}（新上市股票通常尚未披露流通股东，以首份定期报告为准）</div>`;
  }
  const items = r.holders
    .map((h) => `<li>${h.name}<span class="htype">（${h.type}${typeof h.ratio === "number" ? "，占流通股 " + h.ratio.toFixed(2) + "%" : ""}）</span></li>`)
    .join("");
  return `<div class="hdate">截至 ${r.holderDate}（前十大流通股东，来源：定期报告披露）</div><ol class="holders">${items}</ol>`;
}

function card(d: any, idx: number, dir: "up" | "down"): string {
  const code = d.f12, r = details[code] || ({} as Detail);
  const color = dir === "up" ? "#c0392b" : "#1e8449";
  const chg = num(d.f3);
  const chgStr = chg === null ? "—" : (chg > 0 ? "+" : "") + chg.toFixed(2) + "%";
  const ratio = num(d.f39) !== null && num(d.f38) ? ((d.f39 / d.f38) * 100).toFixed(2) + "%" : "—";
  return `<div class="card">
  <div class="chead"><span class="rank">#${idx}</span> <b>${d.f14}</b> <span class="code">${code}</span> <span class="board">${board(code)}</span> <span class="chg" style="color:${color}">${chgStr}</span></div>
  <div class="cbody">
    <table class="kv">
      <tr><td>收盘价</td><td>${px(d.f2)} 元</td><td>开盘价</td><td>${px(d.f17)} 元</td></tr>
      <tr><td>成交额</td><td>${yi(d.f6)}</td><td>当日换手率</td><td>${pct(d.f8)}</td></tr>
      <tr><td>五日换手率</td><td>${turnover5(r)}</td><td>流通市值</td><td>${yi(d.f21)}</td></tr>
      <tr><td>流通股/总股本</td><td>${ratio}</td><td>总市值</td><td>${yi(d.f20)}</td></tr>
    </table>
    <div class="chart">${r.kline?.length ? sparkline(r.kline, color) : `<div class="nochart">未能获取K线：${r.kerr || "无数据"}</div>`}<div class="chartlbl">近半年收盘价走势（前复权）</div></div>
    <div class="hbox"><div class="httl">最新披露前十大流通股东</div>${holdersHtml(r)}</div>
  </div>
</div>`;
}

const section = (title: string, arr: any[], dir: "up" | "down") =>
  `<h2 style="color:${dir === "up" ? "#c0392b" : "#1e8449"}">${title}</h2>` + arr.map((d, i) => card(d, i + 1, dir)).join("\n");

const collectedAt = new Date(Date.now() + 8 * 3600e3).toISOString().slice(0, 16).replace("T", " ");
const tencentNote = tencentCount
  ? `；其中 ${tencentCount} 只股票的历史K线因东方财富接口限流改用腾讯财经公开行情接口补齐（图中数据同为前复权收盘价）`
  : "";

const html = `<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>A股涨跌幅榜 TOP100 · ${TODAY}</title>
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
</style></head><body><div class="wrap">
<h1>A股 涨幅前100 与 跌幅前100 明细报告</h1>
<div class="meta">
<b>交易日：</b>${TODAY}（周${weekday}）｜<b>范围：</b>沪深两市A股（沪主板、深主板含原中小板、创业板、科创板），<b>不含北交所</b>；全市场样本 ${UNIVERSE} 只，按当日涨跌幅排序取前/后各100名。<br>
<b>数据来源：</b>行情快照、K线及前十大流通股东数据取自东方财富公开行情接口（采集于 ${collectedAt}，北京时间）${tencentNote}。<br>
<b>口径说明：</b>①“五日换手率”为最近5个交易日（含当日）单日换手率之和，由日K线数据累加计算；带 <b>*</b> 号者其历史换手率由“成交量÷当前流通股本”推算（腾讯源不直接提供换手率），若近期流通股本有变动可能存在微小偏差。②“流通股东”为最新定期报告公布的前十大流通股东，股东类型（个人/基金/QFII等）为数据源原始标注。③ 涨跌幅榜含当日上市新股与ST股。④ 无法取得的数据以“—”标示并注明原因，<b>本报告不含任何推算之外的估计或编造数据</b>。
</div>
${section("一、涨幅前 100", up, "up")}
${section("二、跌幅前 100", down, "down")}
<footer>数据采集：${TODAY} 收盘后 · 来源：东方财富 / 腾讯财经公开行情接口 · 仅供参考，不构成投资建议</footer>
</div></body></html>`;

import { mkdirSync } from "node:fs";
mkdirSync("reports", { recursive: true });
await Bun.write(`reports/${TODAY}.html`, html);
await Bun.write(`reports/latest.html`, html);
console.log(`报告已生成：reports/${TODAY}.html（${(html.length / 1024).toFixed(0)} KB）`);
