from __future__ import annotations

import os
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import numpy as np
import requests

from tools.akshare import announcements, finance_data
from tools import run_pipeline
from tools.scoring import grader
from tools.scoring import evidence as evidence_module
from tools.scoring.model import score_evidence
from tools.scoring import web_research
from tools.providers import axdata_provider
from tools.tdx.analyzer import AlphaSorosAnalyzer


def full_evidence() -> dict:
    values = {
        "track_strength": 1.0, "chain_stage": "upstream", "supply_evidence_count": 3,
        "supply_tightening": True, "chokepoint_score": 100, "capex_strength": 1.0,
        "controller_action": "increase", "top1_holder_pct": 30, "holder_count_change_pct": -8,
        "top10_quality": 1.0, "pledge_ratio": 0, "unlock_ratio": 0,
        "background_quality": 1.0, "leadership_strength": 1.0, "net_profit": 1,
        "operating_cashflow": 1, "debt_ratio": 0.3, "cash_to_debt": 0.5,
        "st_risk": False, "audit_risk": False, "goodwill_risk": False, "specialized_strength": 1.0,
        "business_chain_match": 1.0, "overseas_revenue_ratio": 40,
        "revenue_yoy": 0.2, "profit_yoy": 0.2, "order_growth": 20,
        "price_percentile_3y": 0.1, "pe_ttm": 10, "peer_pe_ttm_median": 20, "pb": 1,
        "attention_heat": 0.1, "revenue_yoy_delta": 0.1, "profit_yoy_delta": 0.1,
        "alpha_score": 0.5, "market_congestion": 0.3, "market_congestion_fresh": True,
        "alpha_trend": "上升", "ma_structure": "bullish", "momentum_20d": 0.08,
        "ma20_slope_5d": 0.03, "volume_ratio_20d": 1.3, "technical_position": 0.3,
        "technical_overheat": False,
        "verified_catalyst_count": 2, "technical_signal": "建仓",
        "technical_structure_score": 4, "technical_structure_reason": "技术结构明确偏多",
        "technical_indicators": {},
        "chan_structure": {"status": "可分析", "latest_direction": "向上", "relation": "中枢上方",
                           "current_price": 30.0, "support": 28.0, "resistance": 33.0},
    }
    values["metric_sources"] = {key: ["test"] for key in values if key != "metric_sources"}
    return values


class PipelineEfficiencyTest(unittest.TestCase):
    def test_full_framework_reaches_100_and_root(self) -> None:
        card = score_evidence(full_evidence())
        self.assertEqual(card.base_score, 90)
        self.assertEqual(card.adjustment_score, 10)
        self.assertEqual(card.final_score, 100)
        self.assertEqual(card.rating, "根")
        self.assertEqual(next(factor for factor in card.factors if factor.key == "F5").score, 10)
        self.assertEqual(next(factor for factor in card.factors if factor.key == "F6").score, 10)
        self.assertEqual([factor.key for factor in card.factors], ["F1", "F2", "F3", "F4", "F5", "F6"])
        self.assertEqual(sum(len(factor.subfactors) for factor in card.factors), 28)

    def test_missing_evidence_scores_zero(self) -> None:
        card = score_evidence({"metric_sources": {}})
        self.assertEqual(card.base_score, 0)
        self.assertTrue(all(item.score == 0 for factor in card.factors for item in factor.subfactors))
        self.assertTrue(all(item.status == "需人工确认" for factor in card.factors for item in factor.subfactors))

    def test_f5_modifiers_are_bounded_to_ten_points(self) -> None:
        positive = score_evidence(full_evidence())
        self.assertEqual(positive.adjustment_score, 10)
        negative_data = full_evidence()
        negative_data.update({
            "alpha_score": -1, "price_percentile_3y": 0.95, "attention_heat": 0.95,
            "verified_catalyst_count": 0, "trap_risk_level": "高", "ma_structure": "bearish",
            "momentum_20d": -0.10, "ma20_slope_5d": -0.03, "volume_ratio_20d": 1.4,
            "alpha_trend": "下降", "technical_signal": "清仓", "technical_position": 0.9,
            "technical_overheat": True, "technical_structure_score": 0,
        })
        negative = score_evidence(negative_data)
        self.assertGreaterEqual(negative.adjustment_score, 0)
        self.assertLessEqual(negative.adjustment_score, 10)
        self.assertEqual(negative.adjustment_score, 0)
        self.assertEqual(negative.final_score, negative.base_score)

    def test_institutional_direction_uses_two_methods(self) -> None:
        card = score_evidence(full_evidence())
        institutional = next(item for item in card.adjustments if item.key == "institutional_direction")
        self.assertEqual(institutional.score, 2)
        self.assertIn("量化选股筛选=看多", institutional.reason)
        self.assertIn("投资逻辑追踪=看多", institutional.reason)

    def test_institutional_direction_is_separate_from_technical_structure(self) -> None:
        data = full_evidence()
        data.update({
            "ma_structure": "bearish", "momentum_20d": -0.10, "ma20_slope_5d": -0.03,
            "volume_ratio_20d": 1.4, "alpha_trend": "下降", "technical_signal": "清仓",
            "technical_position": 0.9, "technical_overheat": True, "technical_structure_score": 4,
        })
        card = score_evidence(data)
        adjustments = {item.key: item for item in card.adjustments}
        self.assertEqual(adjustments["institutional_direction"].score, 0)
        self.assertEqual(adjustments["technical_structure"].score, 4)

    def test_missing_institutional_methods_do_not_create_score(self) -> None:
        data = full_evidence()
        for key in ("ma_structure", "momentum_20d", "ma20_slope_5d", "volume_ratio_20d",
                    "alpha_trend", "technical_signal", "technical_position", "technical_overheat"):
            data.pop(key, None)
        card = score_evidence(data)
        institutional = next(item for item in card.adjustments if item.key == "institutional_direction")
        self.assertEqual(institutional.score, 0)
        self.assertEqual(institutional.status, "需人工确认")

    def test_high_trap_risk_zeros_sentiment_score(self) -> None:
        data = full_evidence()
        data["trap_risk_level"] = "高"
        card = score_evidence(data)
        sentiment = next(item for item in card.adjustments if item.key == "sentiment")
        self.assertEqual(sentiment.score, 0)

    def test_social_heat_alone_is_not_treated_as_positive_or_negative(self) -> None:
        data = full_evidence()
        data.update({"price_percentile_3y": 0.5, "attention_heat": 0.9, "social_heat": 0.9})
        card = score_evidence(data)
        sentiment = next(item for item in card.adjustments if item.key == "sentiment")
        self.assertEqual(sentiment.score, 1)

    def test_low_price_cold_attention_and_sound_f1_is_plus_two(self) -> None:
        data = full_evidence()
        data.update({"price_percentile_3y": 0.2, "attention_heat": 0.2, "social_heat": 0.2})
        card = score_evidence(data)
        sentiment = next(item for item in card.adjustments if item.key == "sentiment")
        self.assertEqual(sentiment.score, 2)

    def test_zero_announcement_catalysts_remain_partial_without_web_confirmation(self) -> None:
        data = full_evidence()
        data["verified_catalyst_count"] = 0
        catalyst = next(item for item in score_evidence(data).adjustments if item.key == "catalyst")
        self.assertEqual(catalyst.score, 0)
        self.assertEqual(catalyst.status, "部分覆盖")

    def test_st_hard_cap(self) -> None:
        data = full_evidence()
        data["st_risk"] = True
        self.assertEqual(score_evidence(data).rating, "不碰")

    def test_controller_reduction_hard_cap(self) -> None:
        data = full_evidence()
        data["controller_action"] = "reduction"
        self.assertEqual(score_evidence(data).rating, "学习仓")

    def test_factor_floor_hard_cap(self) -> None:
        data = full_evidence()
        data.update({"track_strength": 0, "chain_stage": "downstream", "supply_tightening": False,
                     "chokepoint_score": 0, "capex_strength": 0})
        self.assertEqual(score_evidence(data).rating, "学习仓")

    def test_stale_congestion_does_not_cap(self) -> None:
        data = full_evidence()
        data.update({"price_percentile_3y": 0.9, "attention_heat": 0.2, "market_congestion": 0.95,
                     "market_congestion_fresh": False})
        cap = score_evidence(data).hard_caps[-1]
        self.assertEqual(cap["result"], "需人工确认")

    def test_fresh_congestion_caps_at_spear(self) -> None:
        data = full_evidence()
        data.update({"price_percentile_3y": 0.9, "attention_heat": 0.2, "market_congestion": 0.95,
                     "market_congestion_fresh": True})
        card = score_evidence(data)
        self.assertEqual(card.hard_caps[-1]["result"], "已触发")
        self.assertEqual(card.rating, "矛")

    def test_axdata_is_opt_in(self) -> None:
        with patch.dict(os.environ, {"MODA_AXDATA": "0"}):
            self.assertFalse(axdata_provider.available())
            self.assertIsNone(axdata_provider.fetch("valuation", "300820"))

    def test_collectors_run_in_parallel(self) -> None:
        def slow_module(*_args) -> dict:
            time.sleep(0.1)
            return {"ok": True}

        started = time.perf_counter()
        with patch.object(run_pipeline, "run_module", side_effect=slow_module):
            results = run_pipeline.run_collectors([("a", "a.py", []), ("b", "b.py", []), ("c", "c.py", [])])
        self.assertEqual(len(results), 3)
        self.assertLess(time.perf_counter() - started, 0.25)

    def test_scoring_reads_only_current_reports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / "finance_data" / "300820.md"
            fresh = root / "tdx_analysis" / "300820.md"
            old.parent.mkdir()
            fresh.parent.mkdir()
            old.write_text("旧报告", encoding="utf-8")
            fresh.write_text("新报告", encoding="utf-8")
            started = time.time()
            os.utime(old, (started - 60, started - 60))
            with patch.object(evidence_module, "REPORT_ROOT", root):
                reports = evidence_module.read_reports("300820", ("finance_data", "tdx_analysis"), since=started)
            self.assertEqual(reports, {"tdx_analysis": "新报告"})

    def test_report_contains_required_sections(self) -> None:
        evidence = full_evidence()
        evidence["completed_modules"] = []
        card = score_evidence(evidence)
        report = grader.render_report("300820", "英杰电气", evidence, card, ())
        for heading in ("## 综合得分", "## 一句话结论与最终判断", "## 技术分析（easy-tdx 日 K）", "## 行业景气度交叉验证", "## 六层图形概览",
                        "## 六层评分卡", "## F5 低位与困境反转", "## F6 修正项", "## 舆情、社交热榜与异常推广风险",
                        "## Hard Cap 检查", "## 机构方法交叉验证", "## 睡得着检查"):
            self.assertIn(heading, report)
        conclusion = report.split("## 一句话结论与最终判断", 1)[1].split("## 六层图形概览", 1)[0]
        for number in range(1, 7):
            self.assertIn(f"{number}. ", conclusion)
        self.assertNotIn("## 最终结论", report)
        for forbidden in ("说白了", "他娘的", "我认为", "我觉得"):
            self.assertNotIn(forbidden, conclusion)
        self.assertLess(report.index("## 一句话结论与最终判断"), report.index("## 技术分析（easy-tdx 日 K）"))
        self.assertLess(report.index("## 技术分析（easy-tdx 日 K）"), report.index("## 六层图形概览"))
        self.assertIn("| 指标 | 当前读数 | 当前评价 |", report)
        self.assertNotIn("| 排名 | 指标", report)
        self.assertNotIn("A股适用性", report)
        self.assertIn("当前价格：30.0；支撑位：28.0；压力位：33.0", report)
        self.assertIn("F6 是独立的第六层，已计入综合分", report)
        self.assertIn("**1. 一句话逻辑**\n\n", report)
        self.assertIn("**6. 最终判断**\n\n", report)

    def test_coldness_requires_f3_survival_gate(self) -> None:
        data = full_evidence()
        for key in ("background_quality", "leadership_strength", "net_profit", "operating_cashflow",
                    "debt_ratio", "cash_to_debt", "st_risk", "audit_risk", "goodwill_risk", "specialized_strength"):
            data.pop(key, None)
            data["metric_sources"].pop(key, None)
        f5 = next(factor for factor in score_evidence(data).factors if factor.key == "F5")
        coldness = next(item for item in f5.subfactors if item.key == "coldness")
        self.assertEqual(coldness.score, 0)
        self.assertIn("生存门槛未通过", coldness.reason)

    def test_expectation_gap_requires_low_attention(self) -> None:
        data = full_evidence()
        data["attention_heat"] = 0.8
        f5 = next(factor for factor in score_evidence(data).factors if factor.key == "F5")
        gap = next(item for item in f5.subfactors if item.key == "expectation_gap")
        self.assertEqual(gap.score, 0)

    def test_industry_prosperity_only_changes_confidence_not_score(self) -> None:
        baseline = score_evidence(full_evidence())
        data = full_evidence()
        data.update({
            "industry_prosperity_status": "走弱",
            "industry_prosperity_coverage": "完整",
            "industry_prosperity_conflicts": ["利润改善但营收边际下降"],
            "industry_financial_signal": {"status": "走弱"},
            "industry_supply_signal": {"status": "走弱"},
        })
        checked = score_evidence(data)
        self.assertEqual(checked.final_score, baseline.final_score)
        realization = next(item for factor in checked.factors for item in factor.subfactors if item.key == "realization")
        self.assertEqual(realization.status, "部分覆盖")

    def test_concept_only_track_is_a_clue_not_a_score(self) -> None:
        reports = {"market_events": '<!-- moda_market_events: {"concepts": ["AI算力", "商业航天"]} -->'}
        evidence = evidence_module.build_evidence("301128", "强瑞技术", reports)
        self.assertNotIn("track_strength", evidence)
        self.assertIn("AI 算力与数据中心", evidence["track_clues"])

    def test_one_dominant_track_and_revenue_backed_chain(self) -> None:
        reports = {
            "finance_data": '<!-- moda_metrics: {"industry": "半导体"} -->',
            "business_data": '<!-- moda_business: {"main_business": "半导体设备", "business_items": ["半导体设备"], "business_breakdown": [{"category": "按产品分类", "item": "半导体设备", "revenue_ratio": 0.4}]} -->',
            "market_events": '<!-- moda_market_events: {"concepts": ["AI算力", "商业航天", "储能"]} -->',
        }
        evidence = evidence_module.build_evidence("301128", "强瑞技术", reports)
        self.assertEqual(evidence["dominant_track"], "半导体国产替代")
        self.assertNotIn("AI 算力与数据中心", evidence["track_reason"])
        self.assertGreaterEqual(evidence["business_chain_revenue_ratio"], 0.3)
        self.assertFalse(evidence["chain_partial"])

    def test_unconfirmed_business_revenue_caps_chain_match(self) -> None:
        data = full_evidence()
        data.update({"business_chain_match": 1.0, "business_match_partial": True})
        f4 = next(factor for factor in score_evidence(data).factors if factor.key == "F4")
        business_match = next(item for item in f4.subfactors if item.key == "business_match")
        self.assertEqual(business_match.score, 2)
        self.assertEqual(business_match.status, "部分覆盖")

    def test_report_progress_bars_are_bounded_and_complete(self) -> None:
        self.assertEqual(grader._progress_bar(-1, 100, 10), "░" * 10)
        self.assertEqual(grader._progress_bar(50, 100, 10), "█" * 5 + "░" * 5)
        self.assertEqual(grader._progress_bar(101, 100, 10), "█" * 10)

        evidence = full_evidence()
        evidence["completed_modules"] = []
        card = score_evidence(evidence)
        report = grader.render_report("301128", "强瑞技术", evidence, card, ())
        overview = report.split("## 六层图形概览", 1)[1].split("## 六层评分卡", 1)[0]
        for factor in card.factors:
            self.assertIn(factor.key, overview)
            self.assertIn(grader._progress_bar(factor.score, factor.maximum), overview)

    def test_web_supply_requires_two_domains_categories_and_authority(self) -> None:
        records = [
            {"fetch_status": "ok", "domain": "cninfo.com.cn", "source_tier": "A", "supply_categories": ["orders"], "supply_direction": "tightening"},
            {"fetch_status": "ok", "domain": "example.com", "source_tier": "B", "supply_categories": ["capacity"], "supply_direction": "tightening"},
        ]
        result = web_research._validate_supply(records)
        self.assertEqual(result["status"], "已验证")
        self.assertTrue(result["tightening"])

    def test_web_supply_rejects_duplicate_domain_or_single_category(self) -> None:
        duplicate_domain = [
            {"fetch_status": "ok", "domain": "cninfo.com.cn", "source_tier": "A", "supply_categories": ["orders"], "supply_direction": "tightening"},
            {"fetch_status": "ok", "domain": "cninfo.com.cn", "source_tier": "A", "supply_categories": ["capacity"], "supply_direction": "tightening"},
        ]
        single_category = [
            {"fetch_status": "ok", "domain": "cninfo.com.cn", "source_tier": "A", "supply_categories": ["orders"], "supply_direction": "tightening"},
            {"fetch_status": "ok", "domain": "example.com", "source_tier": "B", "supply_categories": ["orders"], "supply_direction": "tightening"},
        ]
        self.assertEqual(web_research._validate_supply(duplicate_domain)["status"], "需人工确认")
        self.assertEqual(web_research._validate_supply(single_category)["status"], "需人工确认")

    def test_web_supply_conflicting_directions_do_not_score(self) -> None:
        records = [
            {"fetch_status": "ok", "domain": "cninfo.com.cn", "source_tier": "A", "supply_categories": ["orders"], "supply_direction": "tightening"},
            {"fetch_status": "ok", "domain": "example.com", "source_tier": "B", "supply_categories": ["capacity"], "supply_direction": "loosening"},
        ]
        self.assertEqual(web_research._validate_supply(records)["status"], "证据冲突")

    def test_statutory_disclosure_is_high_confidence(self) -> None:
        self.assertEqual(web_research._source_role("cninfo.com.cn"), ("法定信息披露", "A"))
        self.assertEqual(web_research._source_role("www.szse.cn"), ("法定信息披露", "A"))
        row = web_research._classify({
            "url": "https://static.cninfo.com.cn/finalpage/example.pdf",
            "content": "强瑞技术的半导体设备产品用于国产替代，具体产品已经量产",
        }, "强瑞技术")
        self.assertEqual(row["source_role"], "法定信息披露")
        self.assertTrue(row["company_product_relation"])

    def test_financial_forums_are_clue_only_and_cannot_confirm(self) -> None:
        for domain in ("xueqiu.com", "guba.eastmoney.com", "news.gw.com.cn"):
            self.assertEqual(web_research._source_role(domain), ("线索来源", "C"))
        records = [
            {"fetch_status": "ok", "domain": "cninfo.com.cn", "source_tier": "A",
             "source_role": "法定信息披露", "supply_categories": ["orders"], "supply_direction": "tightening"},
            {"fetch_status": "ok", "domain": "xueqiu.com", "source_tier": "C",
             "source_role": "线索来源", "supply_categories": ["capacity"], "supply_direction": "tightening"},
        ]
        result = web_research._validate_supply(records)
        self.assertEqual(result["status"], "需人工确认")
        self.assertEqual(result["evidence_count"], 1)

    def test_web_chokepoint_requires_company_and_industry_crosscheck(self) -> None:
        records = [
            {"fetch_status": "ok", "domain": "cninfo.com.cn", "source_tier": "A", "company_product_relation": True, "industry_dependency": False},
            {"fetch_status": "ok", "domain": "miit.gov.cn", "source_tier": "A", "company_product_relation": False, "industry_dependency": True},
        ]
        result = web_research._validate_chokepoint(records)
        self.assertEqual(result["status"], "已验证")
        self.assertEqual(result["score"], 80)

    def test_web_risk_requires_company_and_authority_body(self) -> None:
        records = [
            {"fetch_status": "ok", "source_tier": "A", "company_named": True,
             "risk_signals": {"delisting": [], "audit": ["保留意见"], "goodwill": []}},
            {"fetch_status": "ok", "source_tier": "B", "company_named": True,
             "risk_signals": {"delisting": ["退市风险警示"], "audit": [], "goodwill": []}},
        ]
        result = web_research._validate_risk(records)
        self.assertEqual(result["status"], "已验证")
        self.assertTrue(result["audit_risk"])
        self.assertIsNone(result["st_risk"])

    def test_web_risk_ignores_report_template_and_unqualified_goodwill_text(self) -> None:
        harmless = web_research._classify({
            "url": "https://static.cninfo.com.cn/example.pdf",
            "content": "强瑞技术 非标准审计意见提示 适用 不适用。审计意见为：标准的无保留意见。公司执行商誉减值测试。",
        }, "强瑞技术")
        self.assertEqual(harmless["risk_signals"]["audit"], [])
        self.assertEqual(harmless["risk_signals"]["goodwill"], [])

        risky = web_research._classify({
            "url": "https://static.cninfo.com.cn/example.pdf",
            "content": "强瑞技术被出具保留意见，并计提了相关商誉减值准备。",
        }, "强瑞技术")
        self.assertTrue(risky["risk_signals"]["audit"])
        self.assertTrue(risky["risk_signals"]["goodwill"])

    def test_web_specialized_requires_authority_and_company_name(self) -> None:
        valid = [{"fetch_status": "ok", "source_tier": "A", "company_named": True, "specialized_labels": ["专精特新小巨人"]}]
        invalid = [{"fetch_status": "ok", "source_tier": "B", "company_named": True, "specialized_labels": ["专精特新小巨人"]}]
        self.assertEqual(web_research._validate_specialized(valid)["strength"], 1.0)
        self.assertEqual(web_research._validate_specialized(invalid)["status"], "需人工确认")

    def test_web_catalyst_requires_authority_company_event_and_fresh_date(self) -> None:
        valid = [{"fetch_status": "ok", "source_tier": "A", "company_named": True,
                  "catalyst_categories": ["orders"], "evidence_fresh": True}]
        stale = [{**valid[0], "evidence_fresh": False}]
        self.assertEqual(web_research._validate_catalysts(valid)["verified_count"], 1)
        self.assertEqual(web_research._validate_catalysts(stale)["status"], "需人工确认")

    def test_search_timeout_and_http_error_degrade_cleanly(self) -> None:
        with patch.dict(os.environ, {"SEARXNG_URL": "https://search.example", "DDG_MCP_URL": ""}, clear=False), \
             patch.object(web_research, "_searxng_search", side_effect=TimeoutError):
            used, rows, errors = web_research._search("auto", "test", 0.1)
        self.assertEqual(used, "none")
        self.assertEqual(rows, [])
        self.assertIn("searxng:TimeoutError", errors)

        with patch.dict(os.environ, {"SEARXNG_URL": "https://search.example", "DDG_MCP_URL": ""}, clear=False), \
             patch.object(web_research, "_searxng_search", side_effect=requests.HTTPError("403 Forbidden")):
            used, rows, errors = web_research._search("auto", "test", 0.1)
        self.assertEqual((used, rows), ("none", []))
        self.assertIn("searxng:HTTPError", errors)

    def test_duckduckgo_mcp_numbered_results_are_parsed(self) -> None:
        text = "Found 2 search results:\n\n1. 标题一\n   URL: https://example.com/a\n   Summary: 摘要一\n\n2. 标题二\n   URL: https://example.org/b\n   Summary: 摘要二\n"
        rows = web_research._parse_ddg_text(text)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["title"], "标题一")
        self.assertEqual(rows[1]["url"], "https://example.org/b")

    def test_web_fallback_does_not_override_complete_structured_supply(self) -> None:
        supply = '<!-- moda_supply_demand: {"supply_evidence_count": 3, "supply_tightening": false} -->'
        web = '<!-- moda_web_research: {"web_supply_validation": {"status": "已验证", "evidence_count": 2, "tightening": true}} -->'
        evidence = evidence_module.build_evidence("000001", "测试", {"supply_demand": supply, "web_research": web})
        self.assertEqual(evidence["supply_evidence_count"], 3)
        self.assertFalse(evidence["supply_tightening"])
        self.assertNotIn("supply_web_fallback", evidence)

    def test_web_fallback_can_fill_missing_supply_and_chokepoint(self) -> None:
        web = (
            '<!-- moda_web_research: {"web_supply_validation": {"status": "已验证", "evidence_count": 2, '
            '"tightening": true}, "web_chokepoint_validation": {"status": "已验证", "score": 80}} -->'
        )
        evidence = evidence_module.build_evidence("000001", "测试", {"web_research": web})
        self.assertEqual(evidence["supply_evidence_count"], 2)
        self.assertTrue(evidence["supply_tightening"])
        self.assertEqual(evidence["chokepoint_score"], 80)

    def test_quarterly_kline_reuses_daily_frame(self) -> None:
        dates = pd.date_range("2024-01-01", periods=400, freq="D")
        daily = pd.DataFrame(
            {"date": dates, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0, "amount": 15.0}
        )
        with patch.object(finance_data, "fetch_kline_daily", side_effect=AssertionError("network fetch not expected")):
            quarterly = finance_data.fetch_kline_quarterly("300820", daily)
        self.assertFalse(quarterly.empty)

    def test_tdx_report_accepts_numpy_score_array(self) -> None:
        dates = pd.date_range("2025-01-01", periods=180, freq="D")
        close = np.linspace(20, 30, len(dates)) + np.sin(np.arange(len(dates)) / 5)
        frame = pd.DataFrame({
            "date": dates, "open": close - 0.2, "high": close + 0.5,
            "low": close - 0.5, "close": close, "volume": np.linspace(1000, 2000, len(dates)),
        })
        analyzer = AlphaSorosAnalyzer(frame, "测试", "000001")
        self.assertIsInstance(analyzer.A_PINGFEN, np.ndarray)
        report = analyzer.generate_report()
        self.assertIn("moda_technical", report)
        self.assertIn("缠论（日线简化结构）", report)
        for indicator in ("OBV", "30日BIAS", "MACD", "BOLL", "ATR", "DMI", "RSI", "WR"):
            self.assertIn(indicator, report)
        self.assertIn("当前价", report)
        self.assertIn("支撑位", report)
        self.assertIn("压力位", report)
        self.assertIn("技术结构得分", report)

    def test_finance_metrics_preserve_goodwill_risk_boolean(self) -> None:
        assets = pd.DataFrame([{"资产总计": 100.0, "负债合计": 20.0, "货币资金": 10.0, "商誉": 5.0}])
        metrics = finance_data._report_metrics("000001", {}, {}, pd.DataFrame(), pd.DataFrame(), {"fzb": assets})
        self.assertIs(metrics["goodwill_risk"], False)

    def test_company_peers_use_easy_tdx_industry(self) -> None:
        boards = pd.DataFrame([
            {"board_type": 4, "board_code": "880952", "board_name": "芯片"},
            {"board_type": 12, "board_code": "881285", "board_name": "其他发电设备"},
        ])
        members = pd.DataFrame([{
            "code": "300820", "name": "英杰电气", "close": 46.75,
            "pe_dynamic": 71.47, "pe_ttm": 46.61, "net_assets": 11.613,
            "total_market_cap_ab": 10_388_041_728, "eps": 0.16,
        }])
        with patch("tools.providers.easy_tdx_provider.fetch_belong_boards", return_value=boards), \
             patch("tools.providers.easy_tdx_provider.fetch_board_members", return_value=members) as fetch_members:
            info, peers = finance_data.fetch_company_and_peers("300820")
        fetch_members.assert_called_once_with("881285")
        self.assertEqual(info["行业"], "其他发电设备")
        self.assertAlmostEqual(peers.iloc[0]["市净率"], 46.75 / 11.613)

    def test_announcements_use_one_stock_request(self) -> None:
        frame = pd.DataFrame(
            [{"date": datetime.now().strftime("%Y-%m-%d"), "title": "测试公告", "type": "PDF", "url": "https://example.test"}]
        )
        with patch("tools.providers.easy_tdx_provider.fetch_announcements", return_value=frame) as fetch:
            result = announcements.fetch_announcements("300820", days=30)
        fetch.assert_called_once()
        self.assertEqual(result["total"], 1)

if __name__ == "__main__":
    unittest.main()
