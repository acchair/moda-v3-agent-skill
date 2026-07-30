from __future__ import annotations

import os
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from tools.akshare import announcements, finance_data
from tools import run_pipeline
from tools.scoring import grader
from tools.providers import axdata_provider
from tools.webapp import runner


class PipelineEfficiencyTest(unittest.TestCase):
    def test_stockcode_text_does_not_trigger_st_cap(self) -> None:
        self.assertEqual(grader._rating(70, "url contains stockcode", "英杰电气"), ("矛", "评分达到 B 档"))

    def test_negative_growth_caps_profit_score(self) -> None:
        score, reason = grader._apply_metric_guard(
            "F4 利润兑现路径", 15, {"revenue_yoy": -0.07, "profit_yoy": -0.28, "operating_cashflow": 1}
        )
        self.assertEqual(score, 6)
        self.assertIn("同比均下降", reason)

    def test_factor_floor_caps_rating(self) -> None:
        self.assertEqual(grader._rating(95, "", "正常公司", {"F1": 14, "F3": 20}), ("学习仓", "F1低于15或F3低于8"))
        self.assertEqual(grader._rating(95, "", "正常公司", {"F1": 20, "F3": 7}), ("学习仓", "F1低于15或F3低于8"))

    def test_plain_reduction_does_not_trigger_control_cap(self) -> None:
        self.assertEqual(grader._rating(70, "普通股东减持公告", "正常公司"), ("矛", "评分达到 B 档"))
        self.assertEqual(grader._rating(70, "控股股东减持公告", "正常公司"), ("学习仓", "控股股东或实控人减持"))

    def test_missing_keywords_do_not_add_score(self) -> None:
        score, hits = grader._score_factor("无同行估值数据\n暂无订单数据", 20, ("估值", "订单"))
        self.assertEqual((score, hits), (0, []))

    def test_negative_profit_and_pe_caps(self) -> None:
        score, reason = grader._apply_metric_guard("F3 生存能力与龙头", 18, {"net_profit": -1})
        self.assertEqual(score, 6)
        self.assertIn("净利润为负", reason)
        score, reason = grader._apply_metric_guard("F5 低位与困境反转", 18, {"pe_ttm": -2})
        self.assertEqual(score, 4)
        self.assertIn("TTM PE为负", reason)

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
            with patch.object(grader, "REPORT_ROOT", root):
                text, sources = grader._read_reports("300820", since=started)
            self.assertEqual(text, "新报告")
            self.assertEqual(sources, ["tdx_analysis"])

    def test_quarterly_kline_reuses_daily_frame(self) -> None:
        dates = pd.date_range("2024-01-01", periods=400, freq="D")
        daily = pd.DataFrame(
            {"date": dates, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0, "amount": 15.0}
        )
        with patch.object(finance_data, "fetch_kline_daily", side_effect=AssertionError("network fetch not expected")):
            quarterly = finance_data.fetch_kline_quarterly("300820", daily)
        self.assertFalse(quarterly.empty)

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

    def test_running_stock_job_is_reused(self) -> None:
        runner.JOBS.clear()
        with patch.object(runner.threading, "Thread") as thread:
            first = runner.start_stock_job("300820", "英杰电气")
            second = runner.start_stock_job("300820", "英杰电气")
        self.assertEqual(first, second)
        self.assertEqual(thread.call_count, 1)
        runner.JOBS.clear()

    def test_batch_stocks_are_deduplicated(self) -> None:
        runner.JOBS.clear()
        with patch.object(runner.threading, "Thread"):
            job_id = runner.start_batch_job([{"code": "300820"}, {"code": "300820"}])
        self.assertEqual(len(runner.JOBS[job_id]["payload"]["stocks"]), 1)
        runner.JOBS.clear()


if __name__ == "__main__":
    unittest.main()
