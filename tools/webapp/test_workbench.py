from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from tools.webapp import reports, workbench


class WorkbenchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.chain_db = root / "chain.db"
        self.state_db = root / "state.db"
        self.research = root / "research"
        (self.research / "scoring").mkdir(parents=True)
        connection = sqlite3.connect(self.chain_db)
        try:
            connection.executescript(
                """
                CREATE TABLE companies (
                    id INTEGER PRIMARY KEY, code6 TEXT, name TEXT, full_name TEXT,
                    market_value REAL, sw_industry_lv1 TEXT, sw_industry_lv2 TEXT,
                    sw_industry_lv3 TEXT, akshare_industry TEXT, ckg_industry_name TEXT, source TEXT
                );
                CREATE TABLE company_industry (
                    company_id INTEGER, industry_name TEXT, source TEXT
                );
                CREATE TABLE company_product (
                    company_id INTEGER, product_name TEXT
                );
                INSERT INTO companies VALUES
                    (1, '000001', '测试银行', '测试银行股份有限公司', 100, '银行', '银行', '银行', NULL, NULL, 'TEST'),
                    (2, '000002', '测试科技', '测试科技股份有限公司', 200, '电子', '元件', '元件', NULL, NULL, 'TEST');
                INSERT INTO company_industry VALUES (2, '元件', 'EXACT');
                INSERT INTO company_product VALUES (2, '测试产品');
                """
            )
            connection.commit()
        finally:
            connection.close()
        self.old_chain, self.old_state, self.old_research = workbench.CHAIN_DB, workbench.STATE_DB, reports.RESEARCH_ROOT
        workbench.CHAIN_DB, workbench.STATE_DB, reports.RESEARCH_ROOT = self.chain_db, self.state_db, self.research

    def tearDown(self) -> None:
        workbench.CHAIN_DB, workbench.STATE_DB, reports.RESEARCH_ROOT = self.old_chain, self.old_state, self.old_research
        self.temp.cleanup()

    def write_score(self, code: str = "000001") -> None:
        (self.research / "scoring" / f"{code}.md").write_text(
            """# 五层评分: 测试(000001)

- 总分: 72/100
- 评级: 矛
- 评级原因: 评分达到 B 档
- 数据来源: finance_data, tdx_analysis

| 因子 | 分数 | 判断逻辑 | 数据来源 | 状态 |
|---|---:|---|---|---|
| F1 产业趋势与资本开支 | 20/30 | 行业, 订单 | easy_tdx/TDX | 有自动证据 |
| F2 股东与筹码 | 10/15 | 股东 | easy_tdx/CNINFO | 有自动证据 |
""",
            encoding="utf-8",
        )

    def test_report_parser_and_pool_crud(self) -> None:
        self.write_score()
        summary = reports.extract_score_summary("000001")
        self.assertEqual(summary["score"], 72)
        self.assertEqual(summary["rating"], "矛")
        self.assertEqual(len(summary["factors"]), 2)

        workbench.put_pool_entry("000001", "core", "重点跟踪")
        pool = workbench.get_pool()
        self.assertEqual(pool["items"][0]["state"], "core")
        self.assertEqual(pool["items"][0]["note"], "重点跟踪")

        workbench.put_pool_entry("000001", "ignore")
        self.assertEqual(workbench.get_pool()["total"], 0)
        workbench.delete_pool_entry("000001")
        self.assertEqual(workbench.get_pool()["total"], 1)

    def test_pool_validation(self) -> None:
        with self.assertRaises(ValueError):
            workbench.put_pool_entry("999999", "watch")
        with self.assertRaises(ValueError):
            workbench.put_pool_entry("000001", "later")
        with self.assertRaises(ValueError):
            workbench.put_pool_entry("000001", "watch", "x" * 501)

    def test_local_search_and_full_industry_list(self) -> None:
        with closing(sqlite3.connect(self.chain_db)) as connection:
            industry = connection.execute("SELECT sw_industry_lv3 FROM companies WHERE code6 = '000002'").fetchone()[0]
        result = workbench.search_companies(industry)
        self.assertEqual(result["stocks"][0]["code"], "000002")
        self.assertEqual(len(workbench.get_pool()["industries"]), 2)

    def test_exact_discovery_mapping(self) -> None:
        market = {
            "panels": {
                "sector_amount_ratio": {
                    "status": "live", "source": "TEST", "as_of": "2026-07-27",
                    "warming": [
                        {"name": "元件", "warming_change": 1.2, "recent_average": 3.0, "previous_average": 1.8},
                        {"name": "银行", "warming_change": -0.2, "recent_average": 1.8, "previous_average": 2.0},
                    ],
                }
            }
        }
        quote = {"quotes": [{"code": "000002", "status": "live", "price": 10, "change_pct": 1}]}
        with patch.object(workbench, "get_quotes", return_value=quote):
            result = workbench.build_discovery(market)
        self.assertEqual(result["status"], "live")
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(result["candidates"][0]["code"], "000002")
        self.assertEqual(result["candidates"][0]["evidence"][1]["source"], "EXACT")
        self.assertNotIn("score", result["candidates"][0])

    def test_pressure_weight_and_missing_data(self) -> None:
        dates = [f"2026-07-{day:02d}" for day in range(1, 13)]
        margin_rows = [
            {"date": day, "ratio": 5 + index * .1, "sh_index": 3000 + index * 4}
            for index, day in enumerate(dates)
        ]
        sector_rows = [
            {"values": [{"ratio": 12 + index * .1} for index in range(12)]},
            {"values": [{"ratio": 8 - index * .05} for index in range(12)]},
        ]
        payload = {
            "as_of": dates[-1], "sources": ["TEST"],
            "panels": {
                "margin_index": {"rows": margin_rows, "source": "MARGIN", "as_of": dates[-1]},
                "sector_amount_ratio": {"rows": sector_rows, "dates": dates, "source": "SECTOR", "as_of": dates[-1]},
            },
        }
        result = workbench.build_market_pressure(payload)
        self.assertEqual(result["available_weight"], 100)
        self.assertEqual(result["status"], "live")
        self.assertIsNotNone(result["score"])

        missing = workbench.build_market_pressure({"panels": {}})
        self.assertEqual(missing["status"], "unavailable")
        self.assertIsNone(missing["score"])


if __name__ == "__main__":
    unittest.main()
