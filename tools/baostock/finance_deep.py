"""
BaoStock 深度财报模块 (独立模块 v1)
=====================================
集成 BaoStock 获取 A 股杜邦分析、三张报表、成长指标、行业分类，
补充 AKShare 在财报深度方面的不足。

用法:
    python3 tools/baostock/finance_deep.py --stock 603290 --name 斯达半导
    python3 tools/baostock/finance_deep.py --stock 603290,600460
"""
import baostock as bs
import pandas as pd
import numpy as np
import time, sys, os, argparse
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent.parent
OUTPUT_BASE = ROOT / "knowledge/research/finance_deep"
OUTPUT_BASE.mkdir(parents=True, exist_ok=True)


def _login():
    lg = bs.login()
    if lg.error_code != '0':
        print(f"  [BaoStock] 登录失败: {lg.error_msg}")
        return False
    return True


def _logout():
    bs.logout()


def _code_bs(code: str) -> str:
    """A股代码 → BaoStock 格式"""
    p = code[0]
    return f"{'sh' if p == '6' else 'sz'}.{code}"


# ══════════════════════════════════════════════════════
#  Data Fetchers
# ══════════════════════════════════════════════════════

def fetch_dupont(code: str) -> pd.DataFrame:
    """杜邦分析: ROE / 净利率 / 周转率 / 权益乘数"""
    bs_code = _code_bs(code)
    rs = bs.query_dupont_data(code=bs_code, year=2025, quarter=1)
    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    if not rows: return pd.DataFrame()
    df = pd.DataFrame(rows, columns=rs.fields)
    for c in ['dupontROE', 'dupontNetProfitMargins', 'dupontAssetTurnover',
              'dupontEquityMultiplier', 'dupontNetProfit', 'dupontSalesRevenue',
              'dupontTotalAssets', 'dupontTotalEquity']:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce')
    return df


def fetch_profit(code: str) -> pd.DataFrame:
    """利润表"""
    bs_code = _code_bs(code)
    rs = bs.query_profit_data(code=bs_code, year=2025, quarter=1)
    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    if not rows: return pd.DataFrame()
    df = pd.DataFrame(rows, columns=rs.fields)
    for c in ['roeAvg', 'npMargin', 'gpMargin', 'netProfit', 'operRev',
              'EBITDA', 'EPS', 'PE', 'operProfit', 'netProfitGrowRate']:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce')
    return df


def fetch_balance(code: str) -> pd.DataFrame:
    """资产负债表"""
    bs_code = _code_bs(code)
    rs = bs.query_balance_data(code=bs_code, year=2025, quarter=1)
    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    if not rows: return pd.DataFrame()
    df = pd.DataFrame(rows, columns=rs.fields)
    for c in ['totalAssets', 'totalLiab', 'totalEquity', 'currentAssets',
              'currentLiab', 'cashEquivalents', 'goodwill']:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce')
    return df


def fetch_cashflow(code: str) -> pd.DataFrame:
    """现金流量表"""
    bs_code = _code_bs(code)
    rs = bs.query_cash_flow_data(code=bs_code, year=2025, quarter=1)
    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    if not rows: return pd.DataFrame()
    df = pd.DataFrame(rows, columns=rs.fields)
    for c in ['CFO', 'CFI', 'CFF', 'freeCashFlow', 'netCashFlow']:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce')
    return df


def fetch_growth(code: str) -> pd.DataFrame:
    """成长指标: 同比/环比增长"""
    bs_code = _code_bs(code)
    rs = bs.query_growth_data(code=bs_code, year=2025, quarter=1)
    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    if not rows: return pd.DataFrame()
    df = pd.DataFrame(rows, columns=rs.fields)
    for c in ['YOYOperRev', 'YOYNetProfit', 'YOYEquity',
              'QOQOperRev', 'QOQNetProfit', 'QOQEquity']:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce')
    return df


def fetch_industry(code: str) -> dict:
    """个股 → 行业分类"""
    bs_code = _code_bs(code)
    rs = bs.query_stock_industry(code=bs_code)
    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    if not rows: return {}
    r = rows[0]
    fields = rs.fields
    return {fields[i]: r[i] for i in range(min(len(fields), len(r)))}


def fetch_kline_pepb(code: str, days=250) -> pd.DataFrame:
    """日K线 (含 PE/PB 估值列) — BaoStock 独有优势"""
    bs_code = _code_bs(code)
    end_date = time.strftime("%Y-%m-%d")
    start_date = f"{int(time.strftime('%Y'))-1}-01-01"
    rs = bs.query_history_k_data_plus(
        code=bs_code,
        fields="date,close,peTTM,pbMRQ,psTTM,pcfNcfTTM,turn",
        start_date=start_date, end_date=end_date,
        frequency="d", adjustflag="2"
    )
    if rs.error_code != '0':
        print(f"  [PE/PB] 查询失败: {rs.error_msg}")
        return pd.DataFrame()
    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    if not rows: return pd.DataFrame()
    df = pd.DataFrame(rows, columns=rs.fields)
    for c in ['close', 'peTTM', 'pbMRQ', 'psTTM', 'pcfNcfTTM', 'turn']:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce')
    df['date'] = pd.to_datetime(df['date'])
    return df


# ══════════════════════════════════════════════════════
#  Report Generator
# ══════════════════════════════════════════════════════

def _safe_num(v, fmt=".2f"):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "-"
    if isinstance(v, float):
        return f"{v:{fmt}}"
    return str(v)


def _to_yi(v, fmt=".2f"):
    """转亿"""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "-"
    return f"{v/1e8:{fmt}}亿"


def build_report(code: str, name: str,
                 dupont: pd.DataFrame, profit: pd.DataFrame,
                 balance: pd.DataFrame, cashflow: pd.DataFrame,
                 growth: pd.DataFrame, industry: dict,
                 kline_pe: pd.DataFrame) -> str:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    L = [
        f"# 深度财报报告: {name}({code})",
        f"",
        f"> 采集时间: {ts}  |  数据源: BaoStock",
        f"> 补充 AKShare 的财报深度不足",
        f"",
        "---",
    ]

    # ── 1. 杜邦分析 ──
    L += ["## 1. 杜邦分析 (ROE 拆解)", ""]
    if not dupont.empty:
        latest = dupont.iloc[-1]
        roe = latest.get('dupontROE')
        npm = latest.get('dupontNetProfitMargins')
        ato = latest.get('dupontAssetTurnover')
        em  = latest.get('dupontEquityMultiplier')
        L.append(f"- **ROE**: {_safe_num(roe, '.2f')}% " +
                 ("(优秀)" if roe and roe > 15 else ("(一般)" if roe and roe > 8 else "(偏低)")))
        L.append(f"- **净利率**: {_safe_num(npm, '.2f')}%")
        L.append(f"- **资产周转率**: {_safe_num(ato, '.3f')} 次")
        L.append(f"- **权益乘数**: {_safe_num(em, '.2f')}x")
        L.append("")
        L.append("> 莫大框架: ROE 高 + 净利率高 = 赚钱能力强。权益乘数高 = 杠杆大（需好爹兜底）。")
        # 历史趋势
        if len(dupont) >= 4:
            L.append("")
            L.append("### ROE 变化趋势")
            L.append("| 报告期 | ROE(%) | 净利率(%) | 周转率 | 权益乘数 |")
            L.append("|--------|--------|-----------|--------|----------|")
            for _, r in dupont.tail(6).iterrows():
                pd_ = str(r.get('dupontPubDate', ''))[:10] or str(r.get('dupontPublishDate', ''))[:10] or '-'
                L.append(
                    f"| {pd_} | {_safe_num(r.get('dupontROE'), '.2f')} "
                    f"| {_safe_num(r.get('dupontNetProfitMargins'), '.2f')} "
                    f"| {_safe_num(r.get('dupontAssetTurnover'), '.3f')} "
                    f"| {_safe_num(r.get('dupontEquityMultiplier'), '.2f')} |"
                )
    else:
        L.append("⚠️ 无杜邦分析数据")
    L.append("")

    # ── 2. 利润表摘要 ──
    L += ["## 2. 利润表摘要", ""]
    if not profit.empty:
        p = profit.iloc[-1]
        rev = p.get('operRev')
        np_ = p.get('netProfit')
        eps = p.get('EPS')
        npm = p.get('npMargin')
        gpm = p.get('gpMargin')
        L.append("| 指标 | 数值 |")
        L.append("|------|------|")
        if rev is not None: L.append(f"| 营业总收入 | {_to_yi(rev)} |")
        if np_ is not None: L.append(f"| 净利润 | {_to_yi(np_)} |")
        if eps is not None: L.append(f"| 每股收益(EPS) | {_safe_num(eps)} |")
        if npm is not None: L.append(f"| 净利率 | {_safe_num(npm, '.2f')}% |")
        if gpm is not None: L.append(f"| 毛利率 | {_safe_num(gpm, '.2f')}% |")

        # 历史利润
        if len(profit) >= 4:
            L.append("")
            L.append("### 利润趋势")
            L.append("| 报告期 | 营收(亿) | 净利润(亿) | EPS | 净利率(%) |")
            L.append("|--------|----------|------------|-----|-----------|")
            for _, r in profit.tail(6).iterrows():
                pd_ = str(r.get('pubDate', ''))[:10] or str(r.get('publishDate', ''))[:10] or '-'
                L.append(
                    f"| {pd_} | {_to_yi(r.get('operRev'))} | {_to_yi(r.get('netProfit'))} "
                    f"| {_safe_num(r.get('EPS'))} | {_safe_num(r.get('npMargin'), '.2f')} |"
                )
    else:
        L.append("⚠️ 无利润表数据")
    L.append("")

    # ── 3. 资产负债表摘要 ──
    L += ["## 3. 资产负债表摘要", ""]
    if not balance.empty:
        b = balance.iloc[-1]
        ta = b.get('totalAssets')
        tl = b.get('totalLiab')
        te = b.get('totalEquity')
        cash = b.get('cashEquivalents')
        gw  = b.get('goodwill')
        L.append("| 指标 | 数值 |")
        L.append("|------|------|")
        if ta is not None: L.append(f"| 总资产 | {_to_yi(ta)} |")
        if tl is not None: L.append(f"| 总负债 | {_to_yi(tl)} |")
        if te is not None: L.append(f"| 净资产 | {_to_yi(te)} |")
        if cash is not None: L.append(f"| 现金及等价物 | {_to_yi(cash)} |")
        if gw is not None: L.append(f"| 商誉 | {_to_yi(gw)} |")
        if ta and te:
            ratio = tl / ta * 100 if ta else 0
            L.append(f"| 资产负债率 | {ratio:.1f}% |")
        L.append("")
        L.append('> 莫大框架: "账户躺着全是现金" = 现金多+负债低+商誉少。这就是安全边际。')
    else:
        L.append("⚠️ 无资产负债表数据")
    L.append("")

    # ── 4. 现金流量 ──
    L += ["## 4. 现金流量", ""]
    if not cashflow.empty:
        cf = cashflow.iloc[-1]
        cfo = cf.get('CFO')
        cfi = cf.get('CFI')
        cff = cf.get('CFF')
        fcf = cf.get('freeCashFlow')
        L.append("| 指标 | 数值 |")
        L.append("|------|------|")
        if cfo is not None: L.append(f"| 经营活动现金流 | {_to_yi(cfo)} |")
        if cfi is not None: L.append(f"| 投资活动现金流 | {_to_yi(cfi)} |")
        if cff is not None: L.append(f"| 筹资活动现金流 | {_to_yi(cff)} |")
        if fcf is not None: L.append(f"| 自由现金流 | {_to_yi(fcf)} |")
        L.append("")
        cfo_ok = cfo and cfo > 0
        L.append(f"- 经营现金流: {'✅ 正' if cfo_ok else '⚠️ 负'} — {'赚钱' if cfo_ok else '在烧钱'}")
        if cfo and np_:
            quality = cfo / np_ if np_ else 0
            L.append(f"- 现金流/净利润: {quality:.2f}x {'(质量高)' if quality > 1 else '(需关注)'}")
    else:
        L.append("⚠️ 无现金流量数据")
    L.append("")

    # ── 5. 成长性 ──
    L += ["## 5. 成长性指标", ""]
    if not growth.empty:
        g = growth.iloc[-1]
        yoy_rev = g.get('YOYOperRev')
        yoy_np  = g.get('YOYNetProfit')
        qoq_rev = g.get('QOQOperRev')
        qoq_np  = g.get('QOQNetProfit')
        L.append("| 指标 | 数值 | 方向 |")
        L.append("|------|------|------|")
        L.append(f"| 营收同比 | {_safe_num(yoy_rev, '.2f')}% | {'📈' if yoy_rev and yoy_rev > 0 else '📉'} |")
        L.append(f"| 净利同比 | {_safe_num(yoy_np, '.2f')}% | {'📈' if yoy_np and yoy_np > 0 else '📉'} |")
        L.append(f"| 营收环比 | {_safe_num(qoq_rev, '.2f')}% | {'📈' if qoq_rev and qoq_rev > 0 else '📉'} |")
        L.append(f"| 净利环比 | {_safe_num(qoq_np, '.2f')}% | {'📈' if qoq_np and qoq_np > 0 else '📉'} |")
    else:
        L.append("⚠️ 无成长数据")
    L.append("")

    # ── 6. PE/PB 估值走势 ──
    L += ["## 6. PE/PB 估值走势 (BaoStock K线含估值)", ""]
    if not kline_pe.empty:
        last = kline_pe.iloc[-1]
        L.append(f"- **PE(TTM)**: {_safe_num(last.get('peTTM'), '.2f')}")
        L.append(f"- **PB(MRQ)**: {_safe_num(last.get('pbMRQ'), '.2f')} {'⚡ <1 破净!' if last.get('pbMRQ') and last['pbMRQ'] < 1 else ''}")
        L.append(f"- **PS(TTM)**: {_safe_num(last.get('psTTM'), '.2f')}")
        L.append(f"- **PCF(NcfTTM)**: {_safe_num(last.get('pcfNcfTTM'), '.2f')}")

        # PE 区间
        pe_valid = kline_pe[kline_pe['peTTM'] > 0]['peTTM']
        if len(pe_valid) > 0:
            pe_min, pe_max = pe_valid.min(), pe_valid.max()
            pe_median = pe_valid.median()
            pe_now = last.get('peTTM')
            percentile = (pe_valid < pe_now).mean() * 100 if pe_now and not np.isnan(pe_now) else 50
            L.append("")
            L.append(f"- PE 历史区间: {pe_min:.1f} ~ {pe_max:.1f} (中位数 {pe_median:.1f})")
            L.append(f"- **当前 PE 分位**: {percentile:.0f}% — " +
                     ("偏高" if percentile > 70 else ("偏低" if percentile < 30 else "适中")))
            L.append(f"- 莫大框架: PE分位低 + PB<1 = 被市场嫌弃的迹象，需要看'好爹'和行业前景确认")

        # PB 分位
        pb_valid = kline_pe[kline_pe['pbMRQ'] > 0]['pbMRQ']
        if len(pb_valid) > 0 and last.get('pbMRQ') and not np.isnan(last['pbMRQ']):
            pb_percentile = (pb_valid < last['pbMRQ']).mean() * 100
            L.append(f"- PB 分位: {pb_percentile:.0f}%")
    else:
        L.append("⚠️ 无估值走势数据")
    L.append("")

    # ── 7. 行业分类 ──
    L += ["## 7. 行业分类", ""]
    if industry:
        L.append("| 维度 | 分类 |")
        L.append("|------|------|")
        for k, v in industry.items():
            if k and v:
                L.append(f"| {k} | {v} |")
    else:
        L.append("⚠️ 无行业分类数据")
    L.append("")

    L += [
        "---",
        "",
        "## 数据源说明",
        "",
        "- 杜邦分析/利润表/资产负债表/现金流量/成长指标: **BaoStock**",
        "- K线含估值(PE/PB/PS/PCF): **BaoStock** `query_history_k_data_plus`",
        "- 行业分类: **BaoStock** `query_stock_industry`",
        "- 与 AKShare 互补: AKShare 提供行情+同行比较，BaoStock 提供财报深度",
        "",
        "## 免责声明",
        "",
        "本报告基于自动化数据采集，仅供信息参考，不构成任何投资建议。",
        "财报数据可能有滞后，请以公司正式公告为准。",
    ]
    return "\n".join(L)


# ══════════════════════════════════════════════════════
#  Main
# ══════════════════════════════════════════════════════

def analyze_stock(code: str, name: str = None) -> str:
    if not name:
        name = code

    print(f"\n{'='*55}")
    print(f"  BaoStock深度财报: {name}({code})")
    print(f"{'='*55}")

    if not _login():
        return ""

    try:
        print("[1/6] 杜邦分析 ...")
        dupont = fetch_dupont(code)
        print(f"  -> {len(dupont)} 期")

        print("[2/6] 利润表 ...")
        profit = fetch_profit(code)
        print(f"  -> {len(profit)} 期")

        print("[3/6] 资产负债表 ...")
        balance = fetch_balance(code)
        print(f"  -> {len(balance)} 期")

        print("[4/6] 现金流量 ...")
        cashflow = fetch_cashflow(code)
        print(f"  -> {len(cashflow)} 期")

        print("[5/6] 成长性 ...")
        growth = fetch_growth(code)
        print(f"  -> {len(growth)} 期")

        print("[6/6] 行业 & 估值K线 ...")
        industry = fetch_industry(code)
        kline_pe = fetch_kline_pepb(code)
        print(f"  -> 行业: {'OK' if industry else '无'}  |  估值K线: {len(kline_pe)} 条")

        report = build_report(code, name, dupont, profit, balance,
                              cashflow, growth, industry, kline_pe)

    finally:
        _logout()

    outpath = OUTPUT_BASE / f"{code}.md"
    outpath.write_text(report, encoding="utf-8")

    ok = sum(1 for x in [dupont, profit, balance, cashflow, growth] if not x.empty)
    print(f"\n  ✅ 报告 ({ok}/5 数据源可用) → {outpath}")
    print(f"{'='*55}")
    return str(outpath)


def main():
    p = argparse.ArgumentParser(description="BaoStock 深度财报采集")
    p.add_argument("--stock", required=True, help="股票代码 (如 603290)")
    p.add_argument("--name", help="股票名称 (选填)")
    args = p.parse_args()

    codes = [c.strip() for c in args.stock.split(",")]
    for code in codes:
        try:
            analyze_stock(code, args.name)
        except Exception as e:
            print(f"[Error] {code}: {e}")
            import traceback
            traceback.print_exc()
        time.sleep(0.5)


if __name__ == "__main__":
    main()
