"""
AKShare 个股基本面 + 行情数据模块 (独立模块 v1)
===============================================
集成 AKShare 获取 A 股实时行情、历史 K 线、财务指标、同行比较，
输出结构化 Markdown 报告供莫大 persona 参考。

数据源优先级: 东方财富 → 新浪 → 腾讯
用法:
    python3 tools/akshare/finance_data.py --stock 603290 --name 斯达半导
    python3 tools/akshare/finance_data.py --stock 603290,600460
"""
import time, sys, os, argparse
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent.parent
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

def fetch_kline_daily(code: str) -> pd.DataFrame:
    """日K线: 优先东财, 回退新浪"""
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


def fetch_kline_quarterly(code: str) -> pd.DataFrame:
    """季K线: 用月K降采样 (AKShare 不直接支持季度)"""
    print(f"  [季K] 从月K降采样 ...", end=" ")
    try:
        # 优先东财月K
        df = ak.stock_zh_a_hist(symbol=code, period="monthly", adjust="qfq")
        if df.empty:
            raise ValueError("月K空")
        df = df.rename(columns=EM_COL_MAP_DAILY)
        df["date"] = pd.to_datetime(df["date"])
    except Exception:
        # 回退: 用新浪日K聚合
        pfx = "sh" if code[0] == "6" else "sz"
        try:
            df = ak.stock_zh_a_daily(symbol=f"{pfx}{code}", adjust="qfq")
            df = df.rename(columns=SINA_COL_MAP)
            df["date"] = pd.to_datetime(df["date"])
        except Exception as e:
            print(f"失败: {e}")
            return pd.DataFrame()

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
    """实时行情: 优先东财全量表, 回退新浪"""
    # 方案1: 东财
    try:
        df = ak.stock_zh_a_spot_em()
        row = df[df["代码"] == code]
        if not row.empty:
            r = row.iloc[0]
            print(f"  [行情] 东财 → {r.get('最新价', 'N/A')}")
            return {
                "source": "东方财富",
                "最新价": r.get("最新价"), "涨跌幅": r.get("涨跌幅"),
                "涨跌额": r.get("涨跌额"), "换手率": r.get("换手率"),
                "量比": r.get("量比"), "市盈率-动态": r.get("市盈率-动态"),
                "市净率": r.get("市净率"), "总市值": r.get("总市值"),
                "流通市值": r.get("流通市值"),
                "60日涨跌幅": r.get("60日涨跌幅"),
                "年初至今涨跌幅": r.get("年初至今涨跌幅"),
            }
    except Exception as e:
        print(f"  [行情] 东财失败: {e}")

    # 方案2: 新浪
    try:
        df = ak.stock_zh_a_spot()
        pfx = "sh" if code[0] == "6" else "sz"
        col_code = [c for c in df.columns if "代码" in c or "code" in c.lower()]
        code_col = col_code[0] if col_code else df.columns[0]
        row = df[df[code_col].str.contains(code, na=False)]
        if not row.empty:
            r = row.iloc[0]
            print(f"  [行情] 新浪 → {r.iloc[2] if len(r) > 2 else 'N/A'}")
            # 新浪列序: 代码, 名称, 最新价, 涨跌额, 涨跌幅, 买价, 卖价, ...
            cols = df.columns.tolist()
            result = {"source": "新浪"}
            if len(cols) > 2: result["最新价"] = r.iloc[2]
            if len(cols) > 3: result["涨跌额"] = r.iloc[3]
            if len(cols) > 4: result["涨跌幅"] = r.iloc[4]
            return result
    except Exception as e:
        print(f"  [行情] 新浪失败: {e}")

    return {}


def fetch_company_info(code: str) -> dict:
    """公司信息: 东财"""
    try:
        df = ak.stock_individual_info_em(symbol=code)
        if not df.empty:
            info = {}
            for _, row in df.iterrows():
                k = str(row.get("item", ""))
                v = row.get("value", "")
                if k and pd.notna(v):
                    info[k] = str(v)
            print(f"  [信息] → {len(info)} 项")
            return info
    except Exception as e:
        print(f"  [信息] 东财失败: {e}")
    return {}


def fetch_valuation_comparison(code: str) -> pd.DataFrame:
    """同行估值"""
    try:
        df = ak.stock_zh_valuation_comparison_em(symbol=code)
        if not df.empty:
            print(f"  [估值] → {len(df)} 家")
            return df
    except Exception as e:
        print(f"  [估值] 失败: {e}")
    return pd.DataFrame()


def fetch_growth_comparison(code: str) -> pd.DataFrame:
    """同行成长性"""
    try:
        df = ak.stock_zh_growth_comparison_em(symbol=code)
        if not df.empty:
            print(f"  [成长] → {len(df)} 家")
            return df
    except Exception as e:
        print(f"  [成长] 失败: {e}")
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


def build_report(code: str, name: str,
                 spot: dict, info: dict,
                 kline_daily: pd.DataFrame,
                 kline_quarterly: pd.DataFrame,
                 valuation: pd.DataFrame,
                 growth: pd.DataFrame) -> str:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    L = [
        f"# 基本面+行情报告: {name}({code})",
        f"",
        f"> 采集时间: {ts}  |  数据源: AKShare",
        f"> 雪球: [个股页](https://xueqiu.com/S/{'SH' if code[0]=='6' else 'SZ'}{code})  "
        f"|  东财: [股吧](https://guba.eastmoney.com/list,{code},99,f.html)",
        f"",
        "---",
    ]

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
        keys = ["总市值", "流通市值", "行业", "上市时间", "总股本", "流通股",
                "净利润", "营业收入", "每股净资产", "每股公积金", "每股未分配利润"]
        L.append("| 指标 | 数值 |")
        L.append("|------|------|")
        for k in keys:
            v = info.get(k)
            if v:
                L.append(f"| {k} | {v} |")
    else:
        L.append("⚠️ 无公司信息数据")
    L.append("")

    # ── 3. 近期行情 ──
    L += ["## 3. 近期行情 (日K)", ""]
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

    # ── 4. 季K 趋势 ──
    L += ["## 4. 季K 趋势（莫大最重视）", ""]
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

    # ── 5. 同行估值 ──
    L += ["## 5. 同行估值对比", ""]
    if not valuation.empty:
        v = valuation
        wanted = ["代码", "简称", "市盈率", "市净率", "市销率", "总市值"]
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

    # ── 6. 成长比较 ──
    L += ["## 6. 同行成长性对比", ""]
    if not growth.empty:
        g = growth
        wanted = ["代码", "简称", "基本每股收益同比增长", "营业收入同比增长", "净利润同比增长"]
        cols = [c for c in wanted if c in g.columns]
        if not cols:
            cols = list(g.columns[:6])
        L.append("| " + " | ".join(cols) + " |")
        L.append("|" + "|".join(["------"] * len(cols)) + "|")
        for _, r in g.head(10).iterrows():
            L.append("| " + " | ".join(_safe_num(r.get(c, "")) for c in cols) + " |")
    else:
        L.append("⚠️ 无同行成长数据")
    L.append("")

    L += [
        "---",
        "",
        "## 免责声明",
        "",
        "本报告基于 AKShare 自动采集，仅供信息参考，不构成任何投资建议。",
        "数据可能因网络延迟、交易所休市等原因不完整。",
        "请以交易所官网、券商正式公告为准。",
    ]

    return "\n".join(L)


# ══════════════════════════════════════════════════════
#  Main
# ══════════════════════════════════════════════════════

def analyze_stock(code: str, name: str = None) -> str:
    if not name:
        name = code

    print(f"\n{'='*55}")
    print(f"  {name}({code})")
    print(f"{'='*55}")

    print("[1/5] 实时行情 ...")
    spot = fetch_spot(code)

    print("[2/5] 公司信息 ...")
    info = fetch_company_info(code)

    print("[3/5] 日K线 ...")
    kline_daily = fetch_kline_daily(code)

    print("[4/5] 季K线 ...")
    kline_quarterly = fetch_kline_quarterly(code)

    print("[5/5] 同行比较 ...")
    valuation = fetch_valuation_comparison(code)
    growth = fetch_growth_comparison(code)

    report = build_report(code, name, spot, info,
                          kline_daily, kline_quarterly,
                          valuation, growth)

    outpath = OUTPUT_BASE / f"{code}.md"
    outpath.write_text(report, encoding="utf-8")

    # 快速摘要
    ok = sum(1 for x in [spot, info, not kline_daily.empty,
                          not kline_quarterly.empty] if x)
    print(f"\n  ✅ 报告 ({ok}/4 数据源可用) → {outpath}")
    print(f"{'='*55}")
    return str(outpath)


def main():
    p = argparse.ArgumentParser(description="AKShare 个股基本面+行情数据采集")
    p.add_argument("--stock", required=True, help="股票代码 (如 603290)")
    p.add_argument("--name", help="股票名称 (选填)")
    args = p.parse_args()

    codes = [c.strip() for c in args.stock.split(",")]
    for code in codes:
        try:
            analyze_stock(code, args.name)
        except Exception as e:
            print(f"[Error] {code}: {e}")
        time.sleep(0.5)


if __name__ == "__main__":
    main()
