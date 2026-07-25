"""
AKShare 公告 + 互动易数据模块
=============================
采集个股最新公告(东方财富)和投资者互动问答(巨潮资讯互动易)，
输出结构化 Markdown 报告供莫大分析参考。

用法:
    python3 tools/akshare/announcements.py --stock 002466 --name 天齐锂业
    python3 tools/akshare/announcements.py --stock 002466,603290 --days 7
"""
import time, sys, os, argparse
from datetime import datetime, timedelta
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools/akshare"))

# ══ 反限流 ══
from anti_rate_limit import apply_patch
apply_patch()

import akshare as ak
import pandas as pd
OUTPUT_BASE = ROOT / "knowledge/research/announcements"
OUTPUT_BASE.mkdir(parents=True, exist_ok=True)


def fetch_irm_qa(code: str, name: str = None) -> dict:
    """获取巨潮资讯互动易问答列表"""
    print(f"  [互动易] 获取 {name or code} 的问答 ...")
    try:
        df = ak.stock_irm_cninfo(symbol=code)
        if df is None or df.empty:
            print(f"  [互动易] 无数据")
            return {"total": 0, "qa_list": []}

        qa_list = []
        for _, row in df.iterrows():
            question = str(row.get("问题", "")) if pd.notna(row.get("问题")) else ""
            answer = str(row.get("回答内容", "")) if pd.notna(row.get("回答内容")) else "(未回复)"
            qa_list.append({
                "q_time": str(row.get("提问时间", ""))[:10],
                "a_time": str(row.get("更新时间", ""))[:10],
                "question": question.strip(),
                "answer": answer.strip(),
                "asker": str(row.get("提问者", "匿名")),
                "source": str(row.get("来源", "")),
            })

        # 按提问时间倒序
        qa_list.sort(key=lambda x: x["q_time"], reverse=True)
        print(f"  [互动易] ✅ 共 {len(qa_list)} 条问答")
        return {"total": len(qa_list), "qa_list": qa_list}
    except Exception as e:
        print(f"  [互动易] AKShare失败: {e}")
        # ── 回退 CloakBrowser ──
        try:
            print(f"  [互动易] 尝试 CloakBrowser 备用...")
            from cninfo_backup import fetch_cninfo_irm
            irm_raw = fetch_cninfo_irm(code, name)
            qa_list = []
            for qa in irm_raw:
                qa_list.append({
                    "q_time": qa.get("time", ""),
                    "a_time": "",
                    "question": qa.get("question", ""),
                    "answer": qa.get("answer", "(未回复)"),
                    "asker": "",
                    "source": "cninfo-backup",
                })
            qa_list.sort(key=lambda x: x["q_time"], reverse=True)
            print(f"  [互动易] CloakBrowser备用: {len(qa_list)} 条")
            return {"total": len(qa_list), "qa_list": qa_list, "source": "cloakbrowser"}
        except Exception as e2:
            print(f"  [互动易] CloakBrowser备用也失败: {e2}")
            return {"total": 0, "qa_list": [], "error": str(e)}


def fetch_announcements(code: str, name: str = None, days: int = 7) -> dict:
    """获取东方财富个股公告 (过滤)"""
    print(f"  [公告] 获取近{days}天公告 (过滤 {code}) ...")
    ann_list = []
    errors = []

    for i in range(days):
        date_str = (datetime.now() - timedelta(days=i)).strftime("%Y%m%d")
        try:
            df = ak.stock_notice_report(symbol="全部", date=date_str)
            if df is not None and not df.empty and "代码" in df.columns:
                # 过滤目标股票代码
                mask = df["代码"].astype(str).str.strip() == code
                matched = df[mask]
                for _, row in matched.iterrows():
                    ann_list.append({
                        "date": date_str,
                        "title": str(row.get("公告标题", "")).strip(),
                        "type": str(row.get("公告类型", "")).strip(),
                        "url": str(row.get("网址", "")).strip(),
                    })
            time.sleep(0.15)  # 避免请求过快
        except Exception as e:
            errors.append(f"  [公告] {date_str} 失败: {str(e)[:60]}")

    ann_list.sort(key=lambda x: x["date"], reverse=True)
    print(f"  [公告] AKShare: {len(ann_list)} 条 (近{days}天)")
    if errors:
        for e in errors[:3]:
            print(e)

    # ── AKShare结果太少 → 回退 CloakBrowser ──
    if len(ann_list) == 0 and len(errors) > days / 2:
        try:
            print(f"  [公告] AKShare多数失败，尝试 CloakBrowser 备用...")
            from cninfo_backup import fetch_cninfo_announcements
            cninfo_ann = fetch_cninfo_announcements(code, name, days)
            if cninfo_ann:
                ann_list = cninfo_ann
                ann_list.sort(key=lambda x: x.get("date", ""), reverse=True)
                print(f"  [公告] CloakBrowser备用: {len(ann_list)} 条")
        except Exception as e:
            print(f"  [公告] CloakBrowser备用也失败: {e}")

    return {"total": len(ann_list), "ann_list": ann_list, "days": days}


def extract_keywords_from_qa(qa_list: list) -> dict:
    """从问答中提取关注主题和关键词"""
    if not qa_list:
        return {}

    from collections import Counter
    import re

    # 高频关注主题
    topic_keywords = {
        "产能/投产": ["产能", "投产", "产线", "扩产", "产量"],
        "订单/客户": ["订单", "客户", "供应", "合作", "配套"],
        "业绩/利润": ["业绩", "利润", "营收", "盈利", "增长"],
        "分红/回购": ["分红", "回购", "派息", "回报"],
        "股东人数": ["股东人数", "股东户数", "股东数"],
        "技术/研发": ["技术", "研发", "专利", "产品"],
        "行业/政策": ["行业", "政策", "补贴", "监管"],
        "股价/市值": ["股价", "市值", "涨", "跌", "估值"],
        "并购/重组": ["收购", "并购", "重组", "整合"],
        "风险/诉讼": ["风险", "诉讼", "处罚", "退市"],
    }

    topic_count = Counter()
    for qa in qa_list:
        text = qa["question"] + " " + qa["answer"]
        for topic, kws in topic_keywords.items():
            if any(kw in text for kw in kws):
                topic_count[topic] += 1

    return {
        "hot_topics": topic_count.most_common(5),
        "unanswered": sum(1 for q in qa_list if "(未回复)" in q["answer"]),
    }


def generate_report(code: str, name: str, irm_data: dict, ann_data: dict) -> str:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        f"# 公告与互动: {name or code}({code})",
        f"",
        f"> 采集时间: {ts}",
        f"> 数据源: 东方财富公告 + 巨潮资讯互动易",
        f"",
        "---",
        f"",
    ]

    # ── 公告 ──
    lines.append("## 最新公告")
    lines.append("")
    ann_total = ann_data.get("total", 0)
    ann_days = ann_data.get("days", 7)
    if ann_total == 0:
        lines.append(f"近 {ann_days} 天无公告。")
    else:
        lines.append(f"近 {ann_days} 天共 **{ann_total}** 条公告：")
        lines.append("")
        lines.append("| 日期 | 类型 | 标题 |")
        lines.append("|------|------|------|")
        for a in ann_data.get("ann_list", [])[:20]:
            title = a["title"].replace("|", "/")[:60]
            lines.append(f"| {a['date']} | {a['type']} | [{title}]({a['url']}) |")
    lines.append("")

    # ── 互动易 ──
    lines.append("## 投资者互动问答")
    lines.append("")
    qa_list = irm_data.get("qa_list", [])
    irm_total = irm_data.get("total", 0)

    if irm_total == 0:
        lines.append("暂无互动问答数据。")
    else:
        # 关键词分析
        kw = extract_keywords_from_qa(qa_list)
        if kw.get("hot_topics"):
            lines.append("### 热议主题")
            lines.append("")
            for topic, count in kw["hot_topics"]:
                bar = "█" * min(count, 10)
                lines.append(f"- {topic}: {bar} ({count}次)")
            lines.append("")

        unanswered = kw.get("unanswered", 0)
        if unanswered > 0:
            lines.append(f"> ⚠️ 有 {unanswered} 条问题未获回复")
            lines.append("")

        lines.append(f"### 最近问答 (共 {irm_total} 条)")
        lines.append("")

        # 只展示最近15条
        for i, qa in enumerate(qa_list[:15]):
            lines.append(f"#### Q{i+1}. {qa['q_time']} | {qa['asker']} ({qa['source']})")
            lines.append("")
            # 截断过长问题
            q_text = qa["question"][:200]
            if len(qa["question"]) > 200:
                q_text += "..."
            lines.append(f"> {q_text}")
            lines.append("")
            # 回答
            a_text = qa["answer"]
            if a_text == "(未回复)":
                lines.append("**⚠️ 尚未回复**")
            else:
                a_text = a_text[:300]
                if len(qa["answer"]) > 300:
                    a_text += "..."
                lines.append(f"**回复**: {a_text}")
            lines.append("")

    lines += [
        "---",
        "## 免责声明",
        "",
        "数据来自东方财富和巨潮资讯网公开信息，仅供信息参考，不构成投资建议。",
    ]
    return "\n".join(lines)


def process_stock(code: str, name: str = None, days: int = 7):
    if not name:
        name = code

    print(f"\n{'='*55}")
    print(f"  公告+互动易: {name}({code})  (近{days}天)")
    print(f"{'='*55}")

    # 并行获取 (串行避免被封)
    irm_data = fetch_irm_qa(code, name)
    time.sleep(0.3)
    ann_data = fetch_announcements(code, name, days)

    # 生成报告
    report = generate_report(code, name, irm_data, ann_data)
    outpath = OUTPUT_BASE / f"{code}.md"
    outpath.write_text(report, encoding="utf-8")
    print(f"  ✅ → {outpath}")
    return str(outpath)


def main():
    p = argparse.ArgumentParser(description="AKShare 公告+互动易采集模块")
    p.add_argument("--stock", required=True, help="股票代码，逗号分隔")
    p.add_argument("--name", help="股票名称")
    p.add_argument("--days", type=int, default=7, help="公告回溯天数 (默认7)")
    args = p.parse_args()

    codes = [c.strip() for c in args.stock.split(",")]
    for code in codes:
        try:
            process_stock(code, args.name, args.days)
        except Exception as e:
            print(f"[Error] {code}: {e}")
        time.sleep(0.5)


if __name__ == "__main__":
    main()
