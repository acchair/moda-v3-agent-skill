"""
通达信 ALPHA-SOROS 技术分析模块 (Python 翻译版 v1)
=====================================================
将 ALPHA-SOROS 15/30分钟趋势线图标信号版 V1.1 翻译为 Python，
运行于日K级别 (PERIOD=6)，输出结构化 Markdown 报告。

用法:
    python3 tools/tdx/analyzer.py --stock 603290 --name 斯达半导
    python3 tools/tdx/analyzer.py --stock 603290,600460
"""
import numpy as np
import pandas as pd
import time, sys, os, argparse
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent.parent
OUTPUT_BASE = ROOT / "knowledge/research/tdx_analysis"
OUTPUT_BASE.mkdir(parents=True, exist_ok=True)

# ══════════════════════════════════════════════════════
#  ALPHA-SOROS 分析器（日K版，PERIOD=6）
# ══════════════════════════════════════════════════════

class AlphaSorosAnalyzer:
    """
    通达信 ALPHA-SOROS 公式的 Python 翻译。
    输入 OHLCV DataFrame，输出评分/信号/报告。
    """

    def __init__(self, df: pd.DataFrame, name: str = "", code: str = ""):
        """
        Args:
            df: 日K DataFrame，需含 date/open/high/low/close/volume 列
            name: 股票名称
            code: 股票代码
        """
        self.name = name or code
        self.code = code
        # 统一列名
        col_map = {c.lower(): c for c in df.columns}
        for std, orig in [("date", "date"), ("open", "open"), ("high", "high"),
                           ("low", "low"), ("close", "close"), ("volume", "volume")]:
            if orig in col_map:
                df = df.rename(columns={col_map[orig]: std})
            elif std in df.columns:
                pass  # already correct
        self.df = df.sort_values("date").reset_index(drop=True).copy()
        self.n = len(df)

        # 日K参数 (PERIOD=6)
        self.NX = 120
        self.N1, self.N2, self.N3 = 5, 10, 20  # 短线/中线/生命线
        self.JIAN_TH = 0.20   # 建阈
        self.JIA_TH  = 0.40   # 加阈
        self.RUO_TH  = -0.20  # 弱阈
        self.QING_TH = -0.45  # 清阈
        self.JIAN_VOL = 1.08  # 建量
        self.JIA_VOL  = 1.18  # 加量
        self.QING_VOL = 1.03  # 清量
        self.DEBOUNCE_JIAN = 6
        self.DEBOUNCE_JIA  = 8
        self.DEBOUNCE_JIANC = 5
        self.DEBOUNCE_QING  = 8

        # 计算结果
        self._calc_all()

    # ── 工具 ──
    @staticmethod
    def _sma(series, n, m):
        """SMA(X,N,M): Y = (M*X+(N-M)*Y')/N"""
        result = np.zeros_like(series, dtype=float)
        result[0] = series.iloc[0] if hasattr(series, 'iloc') else series[0]
        for i in range(1, len(series)):
            result[i] = (m * series.iloc[i] + (n - m) * result[i-1]) / n
        return result

    @staticmethod
    def _hhv(series, n):
        return series.rolling(n).max()

    @staticmethod
    def _llv(series, n):
        return series.rolling(n).min()

    @staticmethod
    def _ref(series, n):
        return pd.Series(series).shift(n).values

    @staticmethod
    def _cross(a, b):
        """上穿: a 从下方向上穿越 b"""
        a_s = pd.Series(a) if not isinstance(a, pd.Series) else a
        b_s = pd.Series(b) if not isinstance(b, pd.Series) else b
        return (a_s > b_s) & (a_s.shift(1) <= b_s.shift(1))

    @staticmethod
    def _count(cond, n):
        if not isinstance(cond, pd.Series):
            cond = pd.Series(cond)
        return cond.rolling(n).sum()

    @staticmethod
    def _barslast(cond):
        """距离上次条件成立的天数"""
        result = np.zeros(len(cond), dtype=int)
        last = -1
        for i in range(len(cond)):
            if cond.iloc[i]:
                last = i
            result[i] = i - last if last >= 0 else i
        return pd.Series(result, index=cond.index)

    # ── 计算主流程 ──
    def _calc_all(self):
        C = self.df["close"]
        O = self.df["open"]
        H = self.df["high"]
        L = self.df["low"]
        V = self.df["volume"]
        n = self.n

        # === 均线 ===
        self.M5  = C.rolling(5).mean()
        self.M10 = C.rolling(10).mean()
        self.M20 = C.rolling(20).mean()

        # === 高低区 & 斐波那契 ===
        NX = self.NX
        self.GD = self._hhv(H, NX)  # 高区
        self.DD = self._llv(L, NX)  # 低区
        self.KJ = self.GD - self.DD
        self.ZC = self.DD + self.KJ * 0.191
        self.JS = self.DD + self.KJ * 0.618
        self.YL = self.DD + self.KJ * 0.809

        quwei = (C - self.DD) / (self.KJ + 0.001)
        self.QUWEI  = quwei
        self.DIWEI  = quwei < 0.382
        self.ZHONGWEI = (quwei >= 0.382) & (quwei < 0.618)
        self.GAOWEI  = quwei >= 0.618
        self.JIGAO   = quwei >= 0.809

        # === MACD ===
        EMA12 = C.ewm(span=12, adjust=False).mean()
        EMA26 = C.ewm(span=26, adjust=False).mean()
        self.DIF = EMA12 - EMA26
        self.DEA = self.DIF.ewm(span=9, adjust=False).mean()
        self.MACD2 = (self.DIF - self.DEA) * 2
        self.MACD_STRONG = (self.DIF > self.DEA) & (self.DIF > self._ref(self.DIF, 3))
        self.MACD_WEAK   = (self.DIF < self._ref(self.DIF, 5)) & (self.DEA < self._ref(self.DEA, 5))
        # 背离
        H15_H = self._hhv(H, 15)
        DIF15_H = self._hhv(self.DIF, 15)
        self.DINGBEI = (H >= self._ref(H15_H, 1)) & (self.DIF < self._ref(DIF15_H, 1)) & (self.DIF > 0)
        H10_H = self._hhv(H, 10)
        DIF10_H = self._hhv(self.DIF, 10)
        self.DUANDING = (H >= self._ref(H10_H, 1)) & (self.DIF < self._ref(DIF10_H, 1)) & (self.DIF > 0)

        # === 趋势结构 ===
        MS, MM, ML = self.M5, self.M10, self.M20
        self.DUOPAI = (MS > MM) & (MM > ML)
        self.KONGPAI = (MS < MM) & (MM < ML)
        self.DUAN_SHENG = MS > self._ref(MS, 3)
        self.ZHONG_SHENG = MM > self._ref(MM, 3)
        self.DUAN_JIANG = MS < self._ref(MS, 3)
        self.ZHONG_JIANG = MM < self._ref(MM, 3)
        self.QUSHI_DUO = self.DUOPAI & self.DUAN_SHENG & self.ZHONG_SHENG
        self.QUSHI_KONG = self.KONGPAI & self.DUAN_JIANG & self.ZHONG_JIANG
        self.ZHAN_DUAN = C >= MS
        self.PO_DUAN  = C < MS
        self.ZHAN_ZHONG = C >= MM
        self.PO_ZHONG  = C < MM
        self.JIEGOU_QIANG = (C > self.JS) & (C > MS) & (MS >= MM)
        self.JIEGOU_RUO  = (C < self.JS) & (C < MS)

        # === Alpha 13因子评分 ===
        self._calc_alpha_factors()
        self._calc_signals()

    # ── Alpha 13因子评分 (公式核心) ──
    def _calc_alpha_factors(self):
        C, O, H, L, V = (self.df[c] for c in ["close","open","high","low","volume"])
        n = self.n

        A_振幅 = np.maximum(H - L, 0.001)
        A_昨收 = self._ref(C, 1)
        A_昨收安 = np.maximum(A_昨收, 0.001)
        A_均5 = C.rolling(5).mean()
        A_均10 = C.rolling(10).mean()
        A_趋多 = (C > A_均5) & (A_均5 >= A_均10)
        A_趋空 = (C < A_均5) & (A_均5 <= A_均10)

        # (一) 日内强度
        A_收位 = (C - L) / A_振幅
        A_实体 = np.abs(C - O) / A_振幅
        A_日原 = A_收位 * 0.7 + A_实体 * np.where(C > O, 0.3, np.where(C < O, -0.3, 0))
        A_日分 = np.clip(A_日原, -1, 1)

        # (二) 动量
        A_动3 = (C - self._ref(C, 3)) / (self._ref(C, 3) + 0.001) * 100
        A_动5 = (C - self._ref(C, 5)) / (self._ref(C, 5) + 0.001) * 100
        A_动加 = A_动3 - self._ref(A_动3, 3)
        A_动原 = A_动3 / 4 + A_动加 * 0.3
        A_动分 = np.clip(A_动原, -1, 1)

        # (三) 量能
        A_均量5 = V.rolling(5).mean()
        A_量比 = V / (A_均量5 + 0.001)
        A_量比昨 = self._ref(V, 1) / (self._ref(A_均量5, 1) + 0.001)
        A_量加 = A_量比 - A_量比昨
        A_量加加 = A_量加 - self._ref(A_量加, 1)

        A_量价配 = np.where(C > self._ref(C, 1), A_量比 - 1,
                   np.where(C < self._ref(C, 1), 1 - A_量比, 0))

        A_量比分 = np.where(A_量比 > 1.5, np.minimum((A_量比 - 1) * 0.6, 1),
                   np.where(A_量比 > 1, (A_量比 - 1) * 0.4,
                   np.where(A_量比 < 0.5, np.maximum((A_量比 - 1) * 0.6, -1),
                   np.where(A_量比 < 0.8, (A_量比 - 1) * 0.3, 0))))

        A_量加分 = np.where((A_量比 > 1) & (A_量加 > 0.15),
                           np.minimum(A_量加 * 2 + A_量加加, 1),
                   np.where((A_量比 > 1) & (A_量加 < -0.1), -0.4,
                   np.where((A_量比 < 0.7) & (A_量加 < -0.1), -0.6, 0)))

        A_量价分 = np.clip(A_量价配 * 0.7, -1, 1)

        # 换手率 (近似: CAPITAL 不可用，用 volume 比例代替)
        A_估换 = V / V.rolling(60).mean()  # 近似
        A_均换5 = A_估换.rolling(5).mean()
        A_换比 = A_估换 / (A_均换5 + 0.001)
        A_换分 = np.where(A_换比 > 1.3, np.minimum((A_换比 - 1) * 0.6, 1),
                 np.where(A_换比 < 0.7, np.maximum((A_换比 - 1) * 0.6, -1), 0))

        A_量能原 = A_量比分 * 0.45 + A_量加分 * 0.35 + A_量价分 * 0.10 + A_换分 * 0.10
        A_量能分 = np.clip(A_量能原, -1, 1)

        # (四) 影线
        A_上影 = (H - np.maximum(C, O)) / A_振幅
        A_下影 = (np.minimum(C, O) - L) / A_振幅
        A_影分 = np.clip(A_下影 - A_上影, -1, 1)

        # (五) 缺口
        A_缺口 = (O - A_昨收安) / A_昨收安 * 100
        A_缺基 = np.where((A_缺口 > 0.8) & (A_收位 > 0.6), np.minimum(A_缺口 / 3, 1),
                 np.where((A_缺口 > 0.3) & (A_收位 > 0.4), 0.5,
                 np.where((A_缺口 < -0.8) & (A_收位 < 0.4), np.maximum(A_缺口 / 3, -1),
                 np.where((A_缺口 < -0.3) & (A_收位 < 0.5), -0.5, 0))))
        A_高开砸 = (A_缺口 > 1) & (C < O) & (A_收位 < 0.3)
        A_缺原 = np.where(A_高开砸, -0.8, A_缺基)
        A_缺分 = np.clip(A_缺原, -1, 1)

        # (六) 量价背离
        A_价变3 = (C - self._ref(C, 3)) / (self._ref(C, 3) + 0.001) * 100
        A_量基3 = pd.Series(self._ref(V, 3), index=V.index).rolling(3).mean()
        A_量变3 = (V.rolling(3).mean() - A_量基3) / (A_量基3 + 0.001) * 100
        A_背度 = np.where((A_价变3 > 0) & (A_量变3 < -5), -1,
                 np.where((A_价变3 < 0) & (A_量变3 > 5), 1,
                 np.where((A_价变3 > 2) & (A_量变3 < 0), -0.5,
                 np.where((A_价变3 < -2) & (A_量变3 > 0), 0.5, 0))))
        A_同向 = self._count((C - self._ref(C, 1)) * (V - self._ref(V, 1)) > 0, 5)
        A_背原 = np.where((A_背度 > 0.5) & (A_同向 <= 2),
                         np.minimum(A_背度 * 1.2 + (3 - A_同向) * 0.15, 1),
                 np.where((A_背度 < -0.5) & (A_同向 >= 3),
                         np.maximum(A_背度 * 1.2, -1), A_背度 * 0.8))
        A_背分 = np.clip(A_背原, -1, 1)

        # (七) 短周期位置
        A_高20 = self._hhv(H, 20)
        A_低20 = self._llv(L, 20)
        A_相20 = np.clip((C - A_低20) / (A_高20 - A_低20 + 0.001), 0, 1)
        A_高10 = self._hhv(H, 10)
        A_低10 = self._llv(L, 10)
        A_相10 = np.clip((C - A_低10) / (A_高10 - A_低10 + 0.001), 0, 1)
        A_位变 = A_相20 - self._ref(A_相20, 5)
        A_位原 = np.where((A_相20 > 0.75) & (A_位变 > 0.05),
                         np.minimum(A_相20 * 0.8 + A_位变 * 2, 1),
                 np.where((A_相10 < 0.25) & (A_位变 > 0.08),
                         np.minimum((0.3 - A_相10) * 2 + A_位变, 1),
                 np.where((A_相20 > 0.85) & (A_位变 < -0.05), -0.6, A_位变 * 1.5)))
        A_位分 = np.clip(A_位原, -1, 1)

        # (八) 连阳连阴
        A_阳 = C > O
        A_阴 = C < O
        A_连阳 = np.zeros(n)
        A_连阴 = np.zeros(n)
        last_yin = -1
        last_yang = -1
        for i in range(n):
            if A_阴.iloc[i]:
                last_yang = i
            if A_阳.iloc[i]:
                last_yin = i
            A_连阳[i] = i - last_yin if last_yin >= 0 else i
            A_连阴[i] = i - last_yang if last_yang >= 0 else i
        A_连阳 = pd.Series(A_连阳, index=C.index)
        A_连阴 = pd.Series(A_连阴, index=C.index)
        A_阳占 = self._count(C > O, 5) / 5
        A_涨续 = self._count(C > self._ref(C, 1), 3)
        A_连基 = np.where(A_连阳 >= 5, 1,
                 np.where(A_连阳 >= 4, 0.8,
                 np.where(A_连阳 >= 3, 0.5,
                 np.where((A_连阳 >= 2) & (A_阳占 >= 0.6), 0.3,
                 np.where(A_连阴 >= 5, -1,
                 np.where(A_连阴 >= 4, -0.7,
                 np.where(A_连阴 >= 3, -0.4,
                 np.where((A_连阴 >= 2) & (A_阳占 <= 0.4), -0.2, 0))))))))
        A_连修 = np.where(A_涨续 == 3, 0.15, np.where(A_涨续 == 0, -0.15, 0))
        A_连分 = np.clip(A_连基 + A_连修, -1, 1)

        # (九) 收盘倾向
        A_收倾 = (C - (H + L) / 2) / A_振幅 * 2
        A_收逆 = (A_收倾 > 0.3) & (self._ref(A_收倾, 1) < -0.2)
        A_收跌 = (A_收倾 < -0.3) & (self._ref(A_收倾, 1) < -0.1)
        A_收原 = np.where(A_收逆, 0.9,
                 np.where(A_收倾 > 0.4, 0.7,
                 np.where(A_收倾 > 0.15, 0.3,
                 np.where(A_收跌, -0.8,
                 np.where(A_收倾 < -0.25, -0.4, 0)))))
        A_收分 = np.clip(A_收原, -1, 1)

        # (十) 基础评分
        A_基原 = (A_日分 * 0.20 + A_动分 * 0.20 + A_量能分 * 0.18 +
                  A_影分 * 0.12 + A_缺分 * 0.08 + A_背分 * 0.10 +
                  A_位分 * 0.05 + A_连分 * 0.05 + A_收分 * 0.02)
        A_基分 = np.clip(A_基原, -1, 1)

        # (十一) 波动压缩
        A_单振 = (H - L) / A_昨收安 * 100
        A_均振5 = A_单振.rolling(5).mean()
        A_均振20 = A_单振.rolling(20).mean()
        A_压比 = A_均振5 / (A_均振20 + 0.001)
        A_压多 = (A_压比 < 0.5) & (A_基分 > 0.20) & A_趋多
        A_压空 = (A_压比 < 0.5) & (A_基分 < -0.20) & A_趋空
        A_压调 = np.where((A_压比 < 0.4) & A_压多, 0.06,
                 np.where((A_压比 < 0.5) & A_压多, 0.04,
                 np.where((A_压比 < 0.4) & A_压空, -0.06,
                 np.where((A_压比 < 0.5) & A_压空, -0.04, 0))))
        A_扩调 = np.where(A_压比 > 2.0, np.where(A_基分 >= 0, -0.03, 0.03),
                 np.where(A_压比 > 1.5, np.where(A_基分 >= 0, -0.02, 0.02), 0))
        A_波调 = A_压调 + A_扩调

        # (十二) 短线反转
        A_昨超跌 = (self._ref(C, 1) / (self._ref(C, 2) + 0.001) < 0.97) & (self._ref(A_收位, 1) < 0.3)
        A_今反弹 = (C > self._ref(C, 1)) & (C > O) & (A_收位 > 0.55)
        A_短反 = A_昨超跌 & A_今反弹 & (A_量比 > 1.15)
        A_反加 = np.where(A_短反, 0.06, 0.0)

        # (十三) 综合评分
        A_综原 = A_基分 + A_波调 + A_反加
        A_综限 = np.clip(A_综原, -1, 1)
        # EMA 平滑
        self.A_PINGFEN = pd.Series(A_综限).ewm(span=3, adjust=False).mean().values

        # 保存中间变量供信号使用
        self._A_动3 = A_动3
        self._A_量比 = A_量比
        self._A_趋多 = A_趋多
        self._A_趋空 = A_趋空
        self._A_背度 = A_背度
        self._A_压比 = A_压比
        self._A_基分 = A_基分
        self._A_收位 = A_收位
        self._A_上影 = A_上影
        self._A_收倾 = A_收倾
        self._A_日分 = A_日分
        self._A_动分 = A_动分
        self._A_量能分 = A_量能分
        self._A_影分 = A_影分
        self._A_缺分 = A_缺分
        self._A_背分 = A_背分
        self._A_位分 = A_位分
        self._A_连分 = A_连分
        self._A_收分 = A_收分

    # ── 交易信号 ──
    def _calc_signals(self):
        C, O, H, L, V = (self.df[c] for c in ["close","open","high","low","volume"])
        P = self.A_PINGFEN
        n = self.n

        D3 = self._A_动3
        LB = self._A_量比
        QD = self._A_趋多
        QK = self._A_趋空
        BD = self._A_背度
        YB = self._A_压比
        JF = self._A_基分
        SW = self._A_收位
        SY = self._A_上影
        ST = self._A_收倾

        MS, MM, ML = self.M5, self.M10, self.M20
        QW = self.QUWEI

        # 内部强弱
        A_强 = (P > self.JIAN_TH) & (D3 > 0) & (LB > self.JIAN_VOL) & QD
        A_极强 = (P > self.JIA_TH) & (LB > self.JIA_VOL) & QD
        A_弱 = (P < self.RUO_TH) & QK
        A_极弱 = (P < self.QING_TH) & QK
        A_转强 = self._cross(P, pd.Series(np.full(n, self.JIAN_TH)))
        A_转弱 = self._cross(pd.Series(np.full(n, self.RUO_TH)), P)
        A_顶背 = (BD < -0.5) & (P > 0) & (LB > 0.8)
        A_底背 = (BD > 0.5) & (P < 0) & (LB > 0.8)

        高转弱 = self.GAOWEI & (P < self._ref(P, 1)) & (P < 0.20)
        高强压 = self.JIGAO & (SY > 0.35) & (SW < 0.55)
        量滞涨 = (LB > 1.50) & (C >= self._ref(C, 1)) & (SW < 0.45)
        弱延续 = (P < 0) & (self._count(P < 0, 5) >= 4) & self.QUSHI_KONG
        压预警 = (YB < 0.45) & (np.abs(JF) < 0.35)
        上影压 = (H > L) & ((H - np.maximum(C, O)) / (H - L + 0.0001) > 0.45)
        量缩涨 = (C > self._ref(C, 1)) & (V < V.rolling(5).mean() * 0.85)
        放量跌 = (C < O) & (V > V.rolling(10).mean() * 1.6)
        三阴 = (C < O) & (self._ref(C, 1) < self._ref(O, 1)) & (self._ref(C, 2) < self._ref(O, 2))
        箱顶 = self._ref(self._hhv(H, 13), 1)
        箱底 = self._ref(self._llv(L, 13), 1)
        破箱底 = C < 箱底
        突箱顶 = C > 箱顶
        过热 = self.JIGAO & (P > 0.60) & (LB > 1.8) & (D3 > 5)
        风险压 = A_顶背 | 高强压 | 量滞涨 | self.DINGBEI | self.DUANDING

        # === 建仓 ===
        建0 = (A_转强 & (C > MS) & (MS >= self._ref(MS, 2)) &
               (D3 > 0) & (LB > self.JIAN_VOL) & (QW < 0.75) & ~风险压)
        建1 = (A_底背 & (C > self._ref(C, 1)) & (C > MS) &
               (P > self._ref(P, 1)) & (LB > 0.90) & (QW < 0.60))
        建2 = (self._cross(C, 箱顶) & (P > self.JIAN_TH) &
               (LB > self.JIAN_VOL) & (MS >= MM) & ~self.MACD_WEAK & (QW < 0.809))
        建3 = (self._cross(C, MS) & (P > self.JIAN_TH) &
               (D3 > 0) & (LB > self.JIAN_VOL) & ~self.DIWEI & ~self.JIGAO)
        建仓0 = 建0 | 建1 | 建2 | 建3

        # === 加仓 ===
        加0 = ((P > self.JIA_TH) & (LB > self.JIA_VOL) & (D3 > 0) &
               self.QUSHI_DUO & (C > MS) & (self._count(C >= MS, 5) >= 4) & ~风险压)
        加1 = (self._cross(C, self.YL) & (P > 0.35) & (LB > 1.10) &
               self.QUSHI_DUO & self.MACD_STRONG & ~过热)
        加2 = (突箱顶 & A_极强 & (MS > MM) & (C > self._ref(C, 1)) & ~高强压)
        加仓0 = 加0 | 加1 | 加2

        # === 减仓 ===
        减0 = (self.GAOWEI & (P < self._ref(P, 1)) & (P < 0.25) & 风险压)
        减1 = (self.GAOWEI & self._cross(MS, C) & (P < 0.15))
        减2 = (self.GAOWEI & 量缩涨 & (P < self._ref(P, 1)) & self.MACD_WEAK)
        减3 = (self.JIGAO & 上影压 & (SW < 0.55) & (LB > 1.00))
        减4 = (self.GAOWEI & A_转弱 & (C < MS))
        减仓0 = 减0 | 减1 | 减2 | 减3 | 减4

        # === 清仓 ===
        清0 = ((C < MS) & (C < MM) & (P < self.QING_TH) & A_极弱)
        清1 = ((C < self.JS) & (C < MS) & (self._count(P < 0, 5) >= 4) & 弱延续)
        清2 = (破箱底 & (P < -0.35) & self.MACD_WEAK)
        清3 = (三阴 & (C < MS) & (P < -0.25) & (LB > self.QING_VOL))
        清4 = (放量跌 & (C < MS) & (C < MM) & (P < self.RUO_TH))
        清仓0 = 清0 | 清1 | 清2 | 清3 | 清4

        # === 去重 & 优先级 (清 > 减 > 加 > 建) ===
        self.SIGNAL_CLEAR = 清仓0 & (self._count(清仓0, self.DEBOUNCE_QING) == 1)
        self.SIGNAL_REDUCE = 减仓0 & ~清仓0 & (self._count(减仓0, self.DEBOUNCE_JIANC) == 1)
        self.SIGNAL_ADD    = 加仓0 & ~减仓0 & ~清仓0 & (self._count(加仓0, self.DEBOUNCE_JIA) == 1)
        self.SIGNAL_OPEN   = 建仓0 & ~加仓0 & ~减仓0 & ~清仓0 & (self._count(建仓0, self.DEBOUNCE_JIAN) == 1)

        # 另外保存中间状态
        self._A_顶背 = A_顶背
        self._高强压 = 高强压
        self._量滞涨 = 量滞涨
        self._量缩涨 = 量缩涨
        self._放量跌 = 放量跌
        self._三阴 = 三阴
        self._过热 = 过热

    # ── 最新值 ──
    def _last(self, series):
        if not isinstance(series, pd.Series):
            series = pd.Series(series)
        v = series.iloc[-1]
        if pd.isna(v): return None
        if isinstance(v, (np.floating, float)): return round(float(v), 4)
        if isinstance(v, (np.bool_, bool)): return bool(v)
        return v

    # ── 报告 ──
    def generate_report(self) -> str:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        L = [
            f"# 通达信技术分析: {self.name}({self.code})",
            f"",
            f"> 采集时间: {ts}  |  指标: ALPHA-SOROS 日K版",
            f"> 公式来源: 通达信公式..txt → Python 翻译",
            f"> ⚠️ 本报告翻译自通达信原公式，部分指标已做工程近似（见文末说明）",
            f"",
            "---",
            "",
            "## 1. Alpha 综合评分",
            "",
        ]

        pf = self._last(self.A_PINGFEN)
        if pf is not None:
            label = "偏多" if pf > self.JIAN_TH else ("偏空" if pf < self.RUO_TH else "中性")
            L.append(f"- **当前评分**: {pf:+.4f} ({label})")
            L.append(f"- 建仓阈值: +{self.JIAN_TH:.2f} / 加仓阈值: +{self.JIA_TH:.2f}")
            L.append(f"- 减仓阈值: {self.RUO_TH:+.2f} / 清仓阈值: {self.QING_TH:+.2f}")
            # 评分趋势
            pf_prev = self.A_PINGFEN[-2] if self.n >= 2 else pf
            trend = "↑ 上升" if pf > pf_prev + 0.02 else ("↓ 下降" if pf < pf_prev - 0.02 else "→ 持平")
            L.append(f"- 评分趋势: {trend}")
        L.append("")

        # 13因子明细
        L.append("### 13因子明细 (最新)")
        L.append("")
        L.append("| # | 因子 | 最新值 | 权重 |")
        L.append("|---|------|--------|------|")
        factors = [
            (1, "日内强度", self._A_日分, 0.20),
            (2, "动量",     self._A_动分, 0.20),
            (3, "量能",     self._A_量能分, 0.18),
            (4, "影线",     self._A_影分, 0.12),
            (5, "缺口",     self._A_缺分, 0.08),
            (6, "量价背离", self._A_背分, 0.10),
            (7, "位置",     self._A_位分, 0.05),
            (8, "连阳连阴", self._A_连分, 0.05),
            (9, "收盘倾向", self._A_收分, 0.02),
        ]
        for num, name, series, weight in factors:
            v = self._last(series)
            if v is not None:
                bar = "▓" * max(0, int(abs(v) * 10)) if v > 0 else "░" * max(0, int(abs(v) * 10))
                sign = "+" if v > 0 else ""
                L.append(f"| {num} | {name} | {sign}{v:.3f} {bar} | {weight:.0%} |")
        L.append("")

        # ── 2. 趋势结构 ──
        L += ["## 2. 趋势结构", ""]
        dp = self._last(self.DUOPAI)
        kp = self._last(self.KONGPAI)
        qt = "多排" if dp else ("空排" if kp else "交织")
        L.append(f"- **均线排列**: {qt} (MA5/MA10/MA20)")
        L.append(f"- MA5: {self._last(self.M5):.2f} / MA10: {self._last(self.M10):.2f} / MA20: {self._last(self.M20):.2f}")
        L.append(f"- 收盘站上MA5: {'是' if self._last(self.ZHAN_DUAN) else '否'}  |  MA10: {'是' if self._last(self.ZHAN_ZHONG) else '否'}")
        L.append(f"- 结构: {'强' if self._last(self.JIEGOU_QIANG) else ('弱' if self._last(self.JIEGOU_RUO) else '中性')}")

        # 高低区位置
        qw_val = self._last(self.QUWEI)
        if qw_val is not None:
            zone = "低位区(<0.382)" if qw_val < 0.382 else ("中位区(0.382~0.618)" if qw_val < 0.618 else ("高位区(0.618~0.809)" if qw_val < 0.809 else "极高区(≥0.809)"))
            L.append(f"- **当前区间**: {zone} (位置={qw_val:.3f})")
        L.append(f"- 高区(GD): {self._last(self.GD):.2f} / 低区(DD): {self._last(self.DD):.2f}")
        L.append("")

        # ── 3. 交易信号 ──
        L += ["## 3. 交易信号", ""]
        signals = [
            ("🔴 清仓", self.SIGNAL_CLEAR),
            ("🟠 减仓", self.SIGNAL_REDUCE),
            ("🟢 加仓", self.SIGNAL_ADD),
            ("🔵 建仓", self.SIGNAL_OPEN),
        ]
        latest_date = self.df["date"].iloc[-1]
        for label, sig in signals:
            triggered = self._last(sig)
            L.append(f"- **{label}**: {'⚡ 触发' if triggered else '—'} (日期: {latest_date})")

        # 最近5个信号
        L.append("")
        L.append("### 最近 5 个交易日信号")
        L.append("| 日期 | 建仓 | 加仓 | 减仓 | 清仓 | 评分 |")
        L.append("|------|------|------|------|------|------|")
        tail = min(5, self.n)
        for i in range(self.n - tail, self.n):
            d = str(self.df["date"].iloc[i])[:10]
            jc = "⚡" if self.SIGNAL_OPEN.iloc[i] else ""
            jj = "⚡" if self.SIGNAL_ADD.iloc[i] else ""
            js = "⚡" if self.SIGNAL_REDUCE.iloc[i] else ""
            qc = "⚡" if self.SIGNAL_CLEAR.iloc[i] else ""
            pf_i = self.A_PINGFEN[i]
            L.append(f"| {d} | {jc} | {jj} | {js} | {qc} | {pf_i:+.3f} |")
        L.append("")

        # ── 4. 量能分析 ──
        L += ["## 4. 量能分析", ""]
        lb = self._last(self._A_量比)
        L.append(f"- **量比**: {lb:.2f}" if lb is not None else "- 量比: N/A")
        V5 = self.df["volume"].rolling(5).mean()
        V10 = self.df["volume"].rolling(10).mean()
        V_last = self._last(self.df["volume"])
        V5_last = self._last(V5)
        if V_last and V5_last is not None:
            dl = "⚡ 地量" if V_last < V5_last * 0.95 else ("🔥 放量" if V_last > V10.iloc[-1] * 1.6 else "正常")
            L.append(f"- **量能状态**: {dl}")
        zp = self._last(self._量缩涨)
        fd = self._last(self._放量跌)
        if zp: L.append("- ⚠️ 量缩涨（上涨乏力）")
        if fd: L.append("- ⚠️ 放量跌（恐慌出逃）")
        L.append("")

        # ── 5. MACD ──
        L += ["## 5. MACD", ""]
        dif = self._last(self.DIF)
        dea = self._last(self.DEA)
        macd = self._last(self.MACD2)
        L.append(f"- DIF: {dif:.4f} / DEA: {dea:.4f} / MACD柱: {macd:.4f}")
        L.append(f"- MACD强弱: {'强' if self._last(self.MACD_STRONG) else ('弱' if self._last(self.MACD_WEAK) else '中性')}")
        db = self._last(self.DINGBEI)
        if db: L.append("- ⚠️ **顶背离**: 价格新高但 DIF 未新高")
        dd = self._last(self.DUANDING)
        if dd: L.append("- ⚠️ **短顶背离**: 10周期级别")
        L.append("")

        # ── 6. 综合判断 ──
        L += ["## 6. 综合判断", ""]
        pf_v = pf or 0
        if pf_v > self.JIA_TH and self._last(self.QUSHI_DUO):
            L.append("**趋势+评分共振偏多** — 多头排列且评分高于加仓阈值。适合放在莫大的「矛」仓位中跟踪。")
        elif pf_v > self.JIAN_TH and self._last(self.ZHAN_DUAN):
            L.append("**评分偏多但趋势未确认** — 可作为建仓观察。莫大框架下：先看季K有没有底部放量，再看'好爹'确认，再下手。")
        elif pf_v < self.QING_TH and self._last(self.QUSHI_KONG):
            L.append("**趋势+评分共振偏空** — 空头排列且评分低于清仓阈值。莫大会说：观察，不动手，等主力鸡脚露出来。")
        elif pf_v < self.RUO_TH:
            L.append("**评分偏空** — 建议观望。结合情绪模块看看市场是否在恐慌（逆人性布局窗口）。")
        else:
            L.append("**中性区间** — 无明确方向信号。莫大会说：不做也是一种操作。等信号明确再说。")

        # 风险提示
        warnings = []
        if self._last(self._A_顶背): warnings.append("顶背离")
        if self._last(self._高强压): warnings.append("高位强压力")
        if self._last(self._量滞涨): warnings.append("量滞涨")
        if self._last(self._过热): warnings.append("过热")
        if warnings:
            L.append(f"\n⚠️ **风险预警**: {', '.join(warnings)}")
        L.append("")

        L += [
            "---",
            "",
            "## 工程说明",
            "",
            "- 本模块是通达信 ALPHA-SOROS 公式的 Python 翻译，运行于日K级别",
            "- `WINNER(C)*100` (获利盘/筹码分布) **已跳过**，需通达信验证",
            "- `HY_INDEXC` (行业指数) **已跳过**，板块弱判断暂不可用",
            "- `CAPITAL` 用成交量比例近似（原始公式用总股本计算换手率）",
            "- 建仓/加仓/减仓/清仓信号优先级: 清 > 减 > 加 > 建",
            "",
            "## 免责声明",
            "",
            "本报告基于自动化技术分析，仅供信息参考，不构成任何投资建议。",
            "公式翻译可能存在偏差，请以通达信软件实际输出为准。",
        ]
        return "\n".join(L)

    def run(self) -> str:
        """运行完整分析，输出报告路径"""
        report = self.generate_report()
        outpath = OUTPUT_BASE / f"{self.code}.md"
        outpath.write_text(report, encoding="utf-8")
        print(f"  ✅ TDX报告 → {outpath}")
        print(f"     评分: {self._last(self.A_PINGFEN):+.4f}  |  "
              f"建仓:{'⚡' if self._last(self.SIGNAL_OPEN) else '-'}"
              f" 加仓:{'⚡' if self._last(self.SIGNAL_ADD) else '-'}"
              f" 减仓:{'⚡' if self._last(self.SIGNAL_REDUCE) else '-'}"
              f" 清仓:{'⚡' if self._last(self.SIGNAL_CLEAR) else '-'}")
        return str(outpath)


# ══════════════════════════════════════════════════════
#  Main: 从 AKShare 取数 → 通达信分析 → 报告
# ══════════════════════════════════════════════════════

def fetch_daily_kline(code: str) -> pd.DataFrame:
    """获取日K线数据 (复用 finance_data 的数据源逻辑)"""
    import akshare as ak

    # 方案1: 东财
    try:
        df = ak.stock_zh_a_hist(symbol=code, period="daily", adjust="qfq")
        if not df.empty:
            return df.rename(columns={
                "日期": "date", "开盘": "open", "收盘": "close",
                "最高": "high", "最低": "low", "成交量": "volume",
                "成交额": "amount"
            })
    except Exception as e:
        print(f"  [数据] 东财失败: {e}")

    # 方案2: 新浪
    try:
        pfx = "sh" if code[0] == "6" else "sz"
        df = ak.stock_zh_a_daily(symbol=f"{pfx}{code}", adjust="qfq")
        if not df.empty:
            return df.rename(columns={
                "date": "date", "open": "open", "high": "high",
                "low": "low", "close": "close", "volume": "volume",
            })
    except Exception as e:
        print(f"  [数据] 新浪失败: {e}")

    return pd.DataFrame()


def analyze_stock(code: str, name: str = None) -> str:
    if not name:
        name = code

    print(f"\n{'='*55}")
    print(f"  通达信 ALPHA-SOROS: {name}({code})")
    print(f"{'='*55}")

    print("[1/2] 获取日K线 ...")
    df = fetch_daily_kline(code)
    if df.empty:
        print("  !! 无法获取 K 线数据")
        return ""
    print(f"  -> {len(df)} 条")

    print("[2/2] 运行 ALPHA-SOROS 分析 ...")
    analyzer = AlphaSorosAnalyzer(df, name=name, code=code)
    return analyzer.run()


def main():
    p = argparse.ArgumentParser(description="通达信 ALPHA-SOROS 技术分析 (日K版)")
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
