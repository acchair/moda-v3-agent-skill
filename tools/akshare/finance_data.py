"""
个股基本面 + 行情数据模块
========================
集成 easy_tdx、Sina、efinance 和 AKShare 获取 A 股行情与基本面数据，
输出结构化 Markdown 报告供莫大 persona 参考。

数据源优先级: easy_tdx/TDX/Sina → efinance/AKShare
用法:
    python3 tools/akshare/finance_data.py --stock 603290 --name 斯达半导
    python3 tools/akshare/finance_data.py --stock 603290,600460
"""
import time, sys, os, argparse, json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools/akshare"))

# ══ 反限流: 必须在 import akshare 之前 ══
from anti_rate_limit import apply_patch
apply_patch()

import akshare as ak
import pandas as pd
import numpy as np
OUTPUT_BASE = ROOT / "knowledge/research/finance_data"

# 东方财富列名 → 统一列名映射
EM_COL_MAP_DAILY = {"日期": "date", "开盘": "open", "收盘": "close",
                     "最高": "high", "最低": "low", "成交量": "volume",
                     "成交额": "amount", "涨跌幅": "pct_chg", "换手率": "turnover"}
SINA_COL_MAP = {"date": "date", "open": "open", "high": "high", "low": "low",
                "close": "close", "volume": "volume", "amount": "amount",
                "outstanding_share": "outstanding", "turnover": "turnover"}

OUTPUT_BASE.mkdir(parents=True, exist_ok=True)


# ══════════════════════════════════════════════════════
#  Data Fetchers (each with fallback)
# ══════════════════════════════════════════════════════

def fetch_kline_daily(code: str, kline_file: Path | None = None) -> pd.DataFrame:
    """日K线: 本次共享缓存 → easy_tdx → 东财 → 新浪。"""
    if kline_file and kline_file.stem == code and kline_file.exists():
        df = pd.read_csv(kline_file, parse_dates=["date"])
        print(f"  [日K] 共享缓存 → {len(df)} 条")
        return df

    try:
        from tools.providers.easy_tdx_provider import fetch_kline_daily as fetch_easy_tdx_kline

        df = fetch_easy_tdx_kline(code)
        if not df.empty:
            print(f"  [日K] easy_tdx → {len(df)} 条")
            return df
    except Exception as e:
        print(f"  [日K] easy_tdx失败: {e}")

    # 方案1: 东财 (最近一年)
    try:
        df = ak.stock_zh_a_hist(symbol=code, period="daily", adjust="qfq")
        if not df.empty:
            df = df.rename(columns=EM_COL_MAP_DAILY)
            df["date"] = pd.to_datetime(df["date"])
            print(f"  [日K] 东财 → {len(df)} 条")
            return df
    except Exception as e:
        print(f"  [日K] 东财失败: {e}")

    # 方案2: 新浪
    try:
        pfx = "sh" if code[0] == "6" else "sz"
        df = ak.stock_zh_a_daily(symbol=f"{pfx}{code}", adjust="qfq")
        if not df.empty:
            df = df.rename(columns=SINA_COL_MAP)
            df["date"] = pd.to_datetime(df["date"])
            # 新浪没有涨跌幅，手动算
            if "pct_chg" not in df.columns:
                df["pct_chg"] = df["close"].pct_change() * 100
            print(f"  [日K] 新浪 → {len(df)} 条")
            return df
    except Exception as e:
        print(f"  [日K] 新浪失败: {e}")

    return pd.DataFrame()


def fetch_kline_quarterly(code: str, daily: pd.DataFrame | None = None) -> pd.DataFrame:
    """季K线: 从已取得的日K本地聚合。"""
    print("  [季K] 从日K降采样 ...", end=" ")
    df = daily.copy() if daily is not None else fetch_kline_daily(code)
    if df.empty:
        print("失败: 日K为空")
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"])

    # 降采样到季度
    df = df.set_index("date")
    q = df.resample("QE").agg({
        "open": "first", "close": "last",
        "high": "max", "low": "min",
        "volume": "sum", "amount": "sum",
    })
    q["pct_chg"] = (q["close"].pct_change() * 100).round(2)
    q = q.dropna(subset=["open"]).reset_index()
    q["date"] = q["date"].apply(lambda dt: f"{dt.year}-Q{(dt.month-1)//3+1}")
    print(f"{len(q)} 条")
    return q


def fetch_spot(code: str) -> dict:
    """实时行情: easy_tdx 单股查询，回退 efinance 单股查询。"""
    try:
        from tools.providers.easy_tdx_provider import fetch_realtime_quote

        result = fetch_realtime_quote(code)
        if result:
            print(f"  [行情] easy_tdx → {result.get('最新价', 'N/A')}")
            return result
    except Exception as e:
        print(f"  [行情] easy_tdx失败: {e}")

    try:
        from tools.efinance.provider import fetch_realtime_quotes

        result = fetch_realtime_quotes(code)
        if result:
            print(f"  [行情] efinance → {result.get('最新价', 'N/A')}")
            return result
    except Exception as e:
        print(f"  [行情] efinance失败: {e}")

    return {}


def fetch_company_and_peers(code: str) -> tuple[dict, pd.DataFrame]:
    """从所属行业板块一次取得行业标签和同行估值快照。"""
    from tools.providers.easy_tdx_provider import fetch_belong_boards, fetch_board_members

    try:
        boards = fetch_belong_boards(code)
    except Exception as e:
        print(f"  [行业] easy_tdx失败: {e}")
        return {}, pd.DataFrame()
    if boards is None or boards.empty:
        return {}, pd.DataFrame()

    industries = boards[pd.to_numeric(boards["board_type"], errors="coerce") == 12]
    if industries.empty:
        return {}, pd.DataFrame()
    names = list(dict.fromkeys(industries["board_name"].dropna().astype(str)))
    info = {"source": "easy_tdx/TDX", "行业": " / ".join(names)}
    board = industries.iloc[-1]

    try:
        members = fetch_board_members(str(board["board_code"]))
    except Exception as e:
        print(f"  [同行] easy_tdx失败: {e}")
        return info, pd.DataFrame()
    if members is None or members.empty:
        return info, pd.DataFrame()

    close = pd.to_numeric(members.get("close"), errors="coerce")
    net_assets = pd.to_numeric(members.get("net_assets"), errors="coerce")
    peers = pd.DataFrame({
        "代码": members["code"].astype(str).str.zfill(6),
        "简称": members["name"],
        "市盈率": pd.to_numeric(members.get("pe_dynamic"), errors="coerce"),
        "市盈率-TTM": pd.to_numeric(members.get("pe_ttm"), errors="coerce"),
        "市净率": close.div(net_assets.where(net_assets > 0)),
        "总市值": pd.to_numeric(members.get("total_market_cap_ab"), errors="coerce"),
        "每股收益": pd.to_numeric(members.get("eps"), errors="coerce"),
    })
    peers = peers[close > 0].copy()
    peers["_target"] = peers["代码"].eq(code)
    peers = peers.sort_values(["_target", "总市值"], ascending=[False, False]).drop(columns="_target")
    print(f"  [同行] {board['board_name']} → {len(peers)} 家")
    return info, peers


def fetch_financial_report(code: str, report_type: str) -> pd.DataFrame:
    """财报: easy_tdx 封装的 Sina JSON 接口。"""
    try:
        from tools.providers.easy_tdx_provider import fetch_financial_report as fetch_sina_report

        df = fetch_sina_report(code, report_type, num=8)
        if not df.empty:
            print(f"  [财报/{report_type}] easy_tdx/Sina → {len(df)} 期")
            return df
    except Exception as e:
        print(f"  [财报/{report_type}] easy_tdx/Sina失败: {e}")
    return pd.DataFrame()


# ══════════════════════════════════════════════════════
#  Report Generator
# ══════════════════════════════════════════════════════

def _safe_num(v, fmt=".2f"):
    """安全格式化数字"""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "-"
    if isinstance(v, float):
        return f"{v:{fmt}}"
    return str(v)


def _report_metrics(code: str, spot: dict, info: dict, kline_daily: pd.DataFrame,
                    valuation: pd.DataFrame, financials: dict[str, pd.DataFrame]) -> dict:
    def latest(report_type: str, column: str):
        frame = financials.get(report_type, pd.DataFrame())
        if frame.empty or column not in frame.columns:
            return None
        value = pd.to_numeric(pd.Series([frame.iloc[0][column]]), errors="coerce").iloc[0]
        return None if pd.isna(value) else float(value)

    metrics = {
        "industry": info.get("行业") if info else None,
        "latest_price": spot.get("最新价") if spot else None,
        "revenue_yoy": latest("lrb", "营业收入_同比"),
        "profit_yoy": latest("lrb", "归属于母公司所有者的净利润_同比"),
        "operating_cashflow": latest("llb", "经营活动产生的现金流量净额"),
        "net_profit": latest("lrb", "归属于母公司所有者的净利润"),
    }
    income = financials.get("lrb", pd.DataFrame())
    if len(income) >= 2:
        for column, target in (
            ("营业收入_同比", "revenue_yoy_delta"),
            ("归属于母公司所有者的净利润_同比", "profit_yoy_delta"),
        ):
            if column in income.columns:
                values = pd.to_numeric(income[column].head(2), errors="coerce")
                if len(values) == 2 and values.notna().all():
                    metrics[target] = float(values.iloc[0] - values.iloc[1])
    assets, liabilities = latest("fzb", "资产总计"), latest("fzb", "负债合计")
    if assets and liabilities is not None:
        metrics["debt_ratio"] = liabilities / assets
    cash = latest("fzb", "货币资金")
    if cash is not None and liabilities and liabilities > 0:
        metrics["cash_to_debt"] = cash / liabilities
    goodwill = latest("fzb", "商誉")
    if goodwill is not None:
        metrics["goodwill"] = goodwill
        if assets and assets > 0:
            goodwill_ratio = goodwill / assets
            metrics["goodwill_to_assets"] = goodwill_ratio
            if goodwill_ratio <= 0.10:
                metrics["goodwill_risk"] = False
            elif goodwill_ratio >= 0.20:
                metrics["goodwill_risk"] = True
    if not valuation.empty:
        target = valuation[valuation["代码"].eq(code)]
        if not target.empty:
            metrics["pe_ttm"] = float(target.iloc[0]["市盈率-TTM"])
            metrics["pb"] = float(target.iloc[0]["市净率"])
        peers = pd.to_numeric(valuation.loc[~valuation["代码"].eq(code), "市盈率-TTM"], errors="coerce")
        peers = peers[peers > 0]
        if not peers.empty:
            metrics["peer_pe_ttm_median"] = float(peers.median())
    if not kline_daily.empty and "close" in kline_daily.columns:
        close = pd.to_numeric(kline_daily["close"], errors="coerce").dropna().tail(800)
        if len(close) >= 240:
            latest_close, low, high = float(close.iloc[-1]), float(close.min()), float(close.max())
            if high > low:
                metrics["price_percentile_3y"] = (latest_close - low) / (high - low)
                metrics["drawdown_from_3y_high"] = latest_close / high - 1
    clean: dict = {}
    for key, value in metrics.items():
        if value is None:
            continue
        if isinstance(value, (bool, np.bool_)):
            clean[key] = bool(value)
        elif isinstance(value, (int, float, np.number)):
            if np.isfinite(value):
                clean[key] = float(value)
        else:
            clean[key] = value
    return clean


def build_report(code: str, name: str,
                 spot: dict, info: dict,
                 kline_daily: pd.DataFrame,
                 kline_quarterly: pd.DataFrame,
                 valuation: pd.DataFrame,
                 financials: dict[str, pd.DataFrame]) -> str:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    L = [
        f"# 基本面+行情报告: {name}({code})",
        f"",
        f"> 采集时间: {ts}  |  数据源: easy_tdx/TDX/Sina + efinance/AKShare",
        f"> 雪球: [个股页](https://xueqiu.com/S/{'SH' if code[0]=='6' else 'SZ'}{code})  "
        f"|  东财: [股吧](https://guba.eastmoney.com/list,{code},99,f.html)",
        f"",
        "---",
    ]
    L.append(f"<!-- moda_metrics: {json.dumps(_report_metrics(code, spot, info, kline_daily, valuation, financials), ensure_ascii=False)} -->")

    # ── 1. 实时行情 ──
    L += ["## 1. 实时行情", ""]
    if spot:
        src = spot.pop("source", "")
        L.append(f"*来源: {src}*  \n")
        L.append("| 指标 | 数值 |")
        L.append("|------|------|")
        for k in ["最新价", "涨跌幅", "涨跌额", "换手率", "量比",
                   "市盈率-动态", "市净率", "总市值", "流通市值",
                   "60日涨跌幅", "年初至今涨跌幅"]:
            v = spot.get(k)
            if v is not None:
                L.append(f"| {k} | {_safe_num(v)} |")
    else:
        L.append("⚠️ 无实时行情数据（可能非交易时间或网络问题）")
    L.append("")

    # ── 2. 公司信息 ──
    L += ["## 2. 公司信息", ""]
    if info:
        L.append(f"*来源: {info.get('source', '')}*  \n")
        keys = ["行业"]
        L.append("| 指标 | 数值 |")
        L.append("|------|------|")
        for k in keys:
            v = info.get(k)
            if v:
                L.append(f"| {k} | {v} |")
    else:
        L.append("⚠️ 无公司信息数据")
    L.append("")

    # ── 3. 财务摘要 ──
    L += ["## 3. 财务摘要", "", "*来源: easy_tdx/Sina*  ", ""]
    financial_columns = {
        "利润表": ("lrb", ["报告期", "营业收入", "营业收入_同比", "归属于母公司所有者的净利润", "归属于母公司所有者的净利润_同比", "基本每股收益"]),
        "资产负债表": ("fzb", ["报告期", "货币资金", "应收账款", "存货", "资产总计", "负债合计", "归属于母公司股东权益合计"]),
        "现金流量表": ("llb", ["报告期", "经营活动产生的现金流量净额", "投资活动产生的现金流量净额", "筹资活动产生的现金流量净额"]),
    }
    for title, (report_type, wanted) in financial_columns.items():
        frame = financials.get(report_type, pd.DataFrame())
        L += [f"### {title}", ""]
        cols = [column for column in wanted if column in frame.columns]
        if frame.empty or not cols:
            L += ["⚠️ 无数据", ""]
            continue
        L.append("| " + " | ".join(cols) + " |")
        L.append("|" + "|".join(["------"] * len(cols)) + "|")
        for _, row in frame.head(4).iterrows():
            values = []
            for column in cols:
                value = row.get(column, "")
                if column.endswith("_同比") and pd.notna(value):
                    values.append(f"{float(value) * 100:.2f}%")
                else:
                    values.append(_safe_num(value))
            L.append("| " + " | ".join(values) + " |")
        L.append("")

    # ── 4. 近期行情 ──
    L += ["## 4. 近期行情 (日K)", ""]
    if not kline_daily.empty:
        recent = kline_daily.tail(10).sort_values("date", ascending=False)
        L.append("| 日期 | 开盘 | 收盘 | 最高 | 最低 | 涨跌幅% | 成交量 |")
        L.append("|------|------|------|------|------|---------|--------|")
        for _, r in recent.iterrows():
            L.append(
                f"| {str(r['date'])[:10]} | {_safe_num(r.get('open'))} | {_safe_num(r.get('close'))} "
                f"| {_safe_num(r.get('high'))} | {_safe_num(r.get('low'))} "
                f"| {_safe_num(r.get('pct_chg'))} | {_safe_num(r.get('volume'), '.0f')} |"
            )

        tail60 = kline_daily.tail(60)
        if len(tail60) > 0:
            h, l = tail60["high"].max(), tail60["low"].min()
            avg_v = tail60["volume"].mean()
            close_now = tail60["close"].iloc[-1]
            chg_60 = ((close_now / tail60["close"].iloc[0] - 1) * 100) if len(tail60) > 1 else 0
            L.append(f"\n**60日统计**: 最高 {_safe_num(h)} / 最低 {_safe_num(l)} "
                     f"/ 涨跌 {_safe_num(chg_60)}% / 日均量 {avg_v:,.0f}")
    else:
        L.append("⚠️ 无日K数据")
    L.append("")

    # ── 5. 季K 趋势 ──
    L += ["## 5. 季K 趋势（莫大最重视）", ""]
    if not kline_quarterly.empty and len(kline_quarterly) >= 2:
        q_data = kline_quarterly.tail(8).sort_values("date", ascending=False)
        L.append("| 季度 | 开盘 | 收盘 | 最高 | 最低 | 涨跌幅% | 成交量 |")
        L.append("|------|------|------|------|------|---------|--------|")
        for _, r in q_data.iterrows():
            L.append(
                f"| {r['date']} | {_safe_num(r.get('open'))} | {_safe_num(r.get('close'))} "
                f"| {_safe_num(r.get('high'))} | {_safe_num(r.get('low'))} "
                f"| {_safe_num(r.get('pct_chg'))} | {_safe_num(r.get('volume'), '.0f')} |"
            )

        # 莫大信号检测
        tail8 = kline_quarterly.tail(8)
        if len(tail8) >= 4:
            vols = tail8["volume"]
            avg_vol = vols.mean()
            last_vol = vols.iloc[-1]
            if last_vol > avg_vol * 1.5:
                L.append(f"\n⚡ **底部放巨量**: 最近季度成交量 {last_vol:,.0f}，"
                         f"显著高于 8 季均值 {avg_vol:,.0f}（{last_vol/avg_vol:.1f}x）。"
                         '莫大常说"主力的鸡脚露出"，值得关注。')

            recent_low = tail8["low"].min()
            last_close = tail8["close"].iloc[-1]
            if last_close < recent_low * 1.15:
                L.append(f"\n📉 **接近 N 季低点**: 当前 {_safe_num(last_close)}，"
                         f"距 8 季最低 {_safe_num(recent_low)} 不到 15%。"
                         '如果基本面没变，可能是被市场嫌弃的窗口。')
    else:
        L.append("⚠️ 无季K数据")
    L.append("")

    # ── 6. 同行估值 ──
    L += ["## 6. 同行估值对比", ""]
    if not valuation.empty:
        v = valuation
        wanted = ["代码", "简称", "市盈率", "市盈率-TTM", "市净率", "总市值", "每股收益"]
        cols = [c for c in wanted if c in v.columns]
        if not cols:
            cols = list(v.columns[:6])
        L.append("| " + " | ".join(cols) + " |")
        L.append("|" + "|".join(["------"] * len(cols)) + "|")
        for _, r in v.head(10).iterrows():
            L.append("| " + " | ".join(_safe_num(r.get(c, "")) for c in cols) + " |")
    else:
        L.append("⚠️ 无同行估值数据")
    L.append("")

    L += [
        "---",
        "",
        "## 免责声明",
        "",
        "本报告基于 easy_tdx、Sina、efinance 和 AKShare 自动采集，仅供信息参考，不构成任何投资建议。",
        "数据可能因网络延迟、交易所休市等原因不完整。",
        "请以交易所官网、券商正式公告为准。",
    ]

    return "\n".join(L)


# ══════════════════════════════════════════════════════
#  Main
# ══════════════════════════════════════════════════════

def analyze_stock(code: str, name: str = None, kline_file: Path | None = None) -> str:
    if not name:
        name = code

    print(f"\n{'='*55}")
    print(f"  {name}({code})")
    print(f"{'='*55}")

    print("[1/3] 并行获取行情、行业同行和三张财报 ...")
    with ThreadPoolExecutor(max_workers=5) as executor:
        spot_future = executor.submit(fetch_spot, code)
        company_future = executor.submit(fetch_company_and_peers, code)
        financial_futures = {
            report_type: executor.submit(fetch_financial_report, code, report_type)
            for report_type in ("lrb", "fzb", "llb")
        }

        print("[2/3] 读取日K线 ...")
        kline_daily = fetch_kline_daily(code, kline_file)
        print("[3/3] 从日K生成季K ...")
        kline_quarterly = fetch_kline_quarterly(code, kline_daily)

        spot = spot_future.result()
        info, valuation = company_future.result()
        financials = {report_type: future.result() for report_type, future in financial_futures.items()}

    report = build_report(code, name, spot, info,
                          kline_daily, kline_quarterly,
                          valuation, financials)

    outpath = OUTPUT_BASE / f"{code}.md"
    outpath.write_text(report, encoding="utf-8")

    # 快速摘要
    ok = sum(1 for x in [spot, info, not kline_daily.empty, not kline_quarterly.empty,
                          not valuation.empty, any(not frame.empty for frame in financials.values())] if x)
    print(f"\n  ✅ 报告 ({ok}/6 数据集可用) → {outpath}")
    print(f"{'='*55}")
    return str(outpath)


def main():
    p = argparse.ArgumentParser(description="AKShare 个股基本面+行情数据采集")
    p.add_argument("--stock", required=True, help="股票代码 (如 603290)")
    p.add_argument("--name", help="股票名称 (选填)")
    p.add_argument("--kline-file", type=Path, help="本次流水线共享的日K文件")
    args = p.parse_args()

    codes = [c.strip() for c in args.stock.split(",")]
    for code in codes:
        try:
            analyze_stock(code, args.name, args.kline_file)
        except Exception as e:
            print(f"[Error] {code}: {e}")
        time.sleep(0.5)


if __name__ == "__main__":
    main()
