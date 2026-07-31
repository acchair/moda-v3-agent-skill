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
        "st_risk": False, "audit_risk": False, "specialized_strength": 1.0,
        "business_chain_match": 1.0, "overseas_revenue_ratio": 40,
        "revenue_yoy": 0.2, "profit_yoy": 0.2, "order_growth": 20,
        "price_percentile_3y": 0.1, "pe_ttm": 10, "peer_pe_ttm_median": 20, "pb": 1,
        "attention_heat": 0.1, "revenue_yoy_delta": 0.1, "profit_yoy_delta": 0.1,
        "alpha_score": 0.5, "market_congestion": 0.3, "market_congestion_fresh": True,
        "alpha_trend": "上升", "ma_structure": "bullish", "momentum_20d": 0.08,
        "ma20_slope_5d": 0.03, "volume_ratio_20d": 1.3, "technical_position": 0.3,
        "technical_overheat": False,
        "verified_catalyst_count": 2, "technical_signal": "建仓",
    }
    values["metric_sources"] = {key: ["test"] for key in values if key != "metric_sources"}
    return values


class PipelineEfficiencyTest(unittest.TestCase):
    def test_full_framework_reaches_100_and_root(self) -> None:
        card = score_evidence(full_evidence())
        self.assertEqual(card.base_score, 100)
        self.assertEqual(card.final_score, 100)
        self.assertEqual(card.rating, "根")
        self.assertEqual(sum(len(factor.subfactors) for factor in card.factors), 24)

    def test_missing_evidence_scores_zero(self) -> None:
        card = score_evidence({"metric_sources": {}})
        self.assertEqual(card.base_score, 0)
        self.assertTrue(all(item.score == 0 for factor in card.factors for item in factor.subfactors))
        self.assertTrue(all(item.status == "需人工确认" for factor in card.factors for item in factor.subfactors))

    def test_adjustment_bounds_are_plus_minus_eight(self) -> None:
        positive = score_evidence(full_evidence())
        self.assertLessEqual(positive.adjustment_score, 8)
        negative_data = full_evidence()
        negative_data.update({
            "alpha_score": -1, "price_percentile_3y": 0.95, "attention_heat": 0.95,
            "verified_catalyst_count": 0, "trap_risk_level": "高", "ma_structure": "bearish",
            "momentum_20d": -0.10, "ma20_slope_5d": -0.03, "volume_ratio_20d": 1.4,
            "alpha_trend": "下降", "technical_signal": "清仓", "technical_position": 0.9,
            "technical_overheat": True,
        })
        negative = score_evidence(negative_data)
        self.assertGreaterEqual(negative.adjustment_score, -8)
        self.assertEqual(negative.adjustment_score, -6)

    def test_alpha_crosscheck_confirmation_keeps_base_score(self) -> None:
        card = score_evidence(full_evidence())
        alpha = next(item for item in card.adjustments if item.key == "alpha")
        self.assertEqual(alpha.score, 3)
        self.assertIn("同向确认", alpha.reason)

    def test_alpha_crosscheck_double_conflict_moves_one_point_toward_zero(self) -> None:
        data = full_evidence()
        data.update({
            "ma_structure": "bearish", "momentum_20d": -0.10, "ma20_slope_5d": -0.03,
            "volume_ratio_20d": 1.4, "alpha_trend": "下降", "technical_signal": "清仓",
            "technical_position": 0.9, "technical_overheat": True,
        })
        card = score_evidence(data)
        alpha = next(item for item in card.adjustments if item.key == "alpha")
        self.assertEqual(alpha.score, 2)
        self.assertIn("冲突降级", alpha.reason)

    def test_alpha_crosscheck_never_creates_score_without_tdx_alpha(self) -> None:
        data = full_evidence()
        data.pop("alpha_score")
        card = score_evidence(data)
        alpha = next(item for item in card.adjustments if item.key == "alpha")
        self.assertEqual(alpha.score, 0)
        self.assertEqual(alpha.status, "需人工确认")

    def test_high_trap_risk_is_sentiment_minus_three(self) -> None:
        data = full_evidence()
        data["trap_risk_level"] = "高"
        card = score_evidence(data)
        sentiment = next(item for item in card.adjustments if item.key == "sentiment")
        self.assertEqual(sentiment.score, -3)

    def test_social_heat_alone_is_not_treated_as_positive_or_negative(self) -> None:
        data = full_evidence()
        data.update({"price_percentile_3y": 0.5, "attention_heat": 0.9, "social_heat": 0.9})
        card = score_evidence(data)
        sentiment = next(item for item in card.adjustments if item.key == "sentiment")
        self.assertEqual(sentiment.score, 0)

    def test_low_price_cold_attention_and_sound_f1_is_plus_two(self) -> None:
        data = full_evidence()
        data.update({"price_percentile_3y": 0.2, "attention_heat": 0.2, "social_heat": 0.2})
        card = score_evidence(data)
        sentiment = next(item for item in card.adjustments if item.key == "sentiment")
        self.assertEqual(sentiment.score, 2)

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
        for heading in ("## 五层评分卡", "## 修正项", "## 舆情、社交热榜与异常推广风险",
                        "## Hard Cap 检查", "## 机构方法交叉验证", "## 睡得着检查"):
            self.assertIn(heading, report)
        conclusion = report.split("## 最终结论", 1)[1]
        for number in range(1, 7):
            self.assertIn(f"{number}. ", conclusion)
        for forbidden in ("说白了", "他娘的", "我认为", "我觉得"):
            self.assertNotIn(forbidden, conclusion)

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

    def test_web_chokepoint_requires_company_and_industry_crosscheck(self) -> None:
        records = [
            {"fetch_status": "ok", "domain": "cninfo.com.cn", "source_tier": "A", "company_product_relation": True, "industry_dependency": False},
            {"fetch_status": "ok", "domain": "miit.gov.cn", "source_tier": "A", "company_product_relation": False, "industry_dependency": True},
        ]
        result = web_research._validate_chokepoint(records)
        self.assertEqual(result["status"], "已验证")
        self.assertEqual(result["score"], 80)

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
        self.assertIn("moda_technical", analyzer.generate_report())

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
