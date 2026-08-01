"""
金融情感分析引擎 (v2) — 加权词典 + 子句打分 + Emoji

借鉴 Stoneshisi/xueqiudongcaishuju 的 NLP 方法:
  - 按标点切子句，逐句打分取均值
  - 加权情感词典 (-0.5 ~ +0.5)
  - 否定词窗口反转 (前 6 字)
  - Emoji 情感映射 (雪球/东财常用)
  - jieba 分词 + 停用词过滤 (关键词提取)

独立于莫大 persona，纯数据引擎。
"""
import re
import sys, io, os
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import jieba
import jieba.analyse

# ── Paths ──
_DICT_DIR = Path(__file__).resolve().parent / "sentiment_dict"


# ══════════════════════════════════════════════════════
#  1. Dictionary Loader
# ══════════════════════════════════════════════════════

class FinanceSentimentDict:
    """加权金融情感词典 (借鉴 xueqiudongcaishuju 格式)"""

    def __init__(self):
        self.pos_words: dict[str, float] = {}   # word → score (0~0.5)
        self.neg_words: dict[str, float] = {}   # word → score (-0.5~0)
        self.stopwords: set = set()
        self.negators: set = {"不", "没", "未", "无", "别", "勿", "莫", "非", "未必", "没有"}
        self.bull_markers = {"看多", "看涨", "做多", "买入", "加仓", "全仓", "满仓"}
        self.bear_markers = {"看空", "看跌", "做空", "卖出", "减仓", "清仓", "割肉"}
        self._loaded = False

    def load(self):
        if self._loaded:
            return

        # 加权词库 (from xueqiudongcaishuju)
        sent_path = _DICT_DIR / "finance_sentiment.txt"
        if sent_path.exists():
            for line in sent_path.read_text("utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) != 2:
                    continue
                try:
                    word, score = parts[0], float(parts[1])
                except ValueError:
                    continue
                if score >= 0:
                    self.pos_words[word] = score
                else:
                    self.neg_words[word] = score
                jieba.add_word(word)  # 确保分词准确

        # 停用词
        stop_path = _DICT_DIR / "finance_stopwords.txt"
        if stop_path.exists():
            for line in stop_path.read_text("utf-8").splitlines():
                w = line.strip()
                if w and not w.startswith("#"):
                    self.stopwords.add(w)

        # 补充否定词
        neg_path = _DICT_DIR / "negation.txt"
        if neg_path.exists():
            for line in neg_path.read_text("utf-8").splitlines():
                w = line.strip()
                if w:
                    self.negators.add(w)

        self._loaded = True
        print(f"[Engine] dict: pos={len(self.pos_words)} neg={len(self.neg_words)} "
              f"stop={len(self.stopwords)} negators={len(self.negators)}")


# ══════════════════════════════════════════════════════
#  2. Emoji & Emoticon Mapping
# ══════════════════════════════════════════════════════

EMOJI_SENT = {
    "🚀": 0.40, "🐂": 0.35, "📈": 0.30, "💎": 0.30, "💪": 0.20,
    "✅": 0.20, "💰": 0.20, "🔥": 0.20, "🤑": 0.30, "🎉": 0.20,
    "👍": 0.15, "💯": 0.20, "🌟": 0.15, "🟢": 0.15,
    "🔻": -0.30, "🐻": -0.30, "📉": -0.30, "💩": -0.40, "😭": -0.30,
    "💀": -0.30, "🚨": -0.25, "⚠": -0.15, "⚠️": -0.15, "👎": -0.20,
    "😡": -0.25, "😱": -0.30, "🤡": -0.25, "❌": -0.20, "🔴": -0.15,
}

TEXT_EMOTICONS = {
    "[赞]": 0.15, "[强]": 0.20, "[牛]": 0.30, "[666]": 0.25,
    "[哭]": -0.25, "[亏]": -0.30, "[跪]": -0.30, "[捂脸]": -0.15,
    "[滴汗]": -0.15, "[无语]": -0.20, "[怒]": -0.30,
}


# ══════════════════════════════════════════════════════
#  3. Text Cleaner
# ══════════════════════════════════════════════════════

_clean_re = re.compile(r"<[^>]+>|https?://\S+|@[\w\-]+|\$[^$]+\$")
_sent_split = re.compile(r"[，。！？；,.!?;\n]+")


def clean_text(text: str) -> str:
    """去 HTML/链接/@mention/$ticker"""
    if not text:
        return ""
    return _clean_re.sub(" ", text).strip()


def split_clauses(text: str) -> list[str]:
    """按标点切子句"""
    if not text:
        return []
    return [p.strip() for p in _sent_split.split(text) if p.strip()]


# ══════════════════════════════════════════════════════
#  4. Core Scorer
# ══════════════════════════════════════════════════════

class SentimentScorer:
    """加权子句级情感打分器"""

    def __init__(self, dictionary: FinanceSentimentDict = None):
        self.dict = dictionary or FinanceSentimentDict()
        self.dict.load()

    def score_text(self, text: str) -> dict:
        """
        对整段文本打分。
        返回: {score, label, pos_hits, neg_hits, emoji_delta, clauses}
        """
        text = clean_text(text)
        if not text or len(text) < 3:
            return self._empty_result()

        # 1. Emoji 打分
        emoji_delta, emoji_hits = self._emoji_score(text)

        # 2. 显式标签检测
        has_bull = any(m in text for m in self.dict.bull_markers)
        has_bear = any(m in text for m in self.dict.bear_markers)

        # 3. 子句打分
        clauses = split_clauses(text)
        clause_scores = []
        total_pos, total_neg = 0.0, 0.0

        for clause in clauses:
            pos_sum, neg_sum, pos_n, neg_n = self._score_clause(clause)
            total_pos += pos_sum
            total_neg += neg_sum
            clause_scores.append({
                "text": clause[:80],
                "score": round(pos_sum + neg_sum, 3),
                "pos_hits": pos_n,
                "neg_hits": neg_n,
            })

        # 4. 合成总分
        n_clauses = len(clause_scores) or 1
        base_score = (total_pos + total_neg) / n_clauses  # 子句均值
        base_score += emoji_delta  # emoji 加成

        # 显式标签微调
        if has_bull and not has_bear:
            base_score += 0.1
        elif has_bear and not has_bull:
            base_score -= 0.1

        # Clamp
        base_score = max(-1.0, min(1.0, base_score))

        # 标签
        if base_score > 0.1:
            label = "看多"
        elif base_score < -0.1:
            label = "看空"
        else:
            label = "中性"

        return {
            "score": round(base_score, 3),
            "label": label,
            "pos_hits": round(total_pos, 2),
            "neg_hits": round(total_neg, 2),
            "emoji_delta": round(emoji_delta, 2),
            "clauses": clause_scores,
        }

    def _score_clause(self, clause: str) -> tuple:
        """子句级打分: 词典匹配 + 否定窗口反转"""
        words = list(jieba.cut(clause))
        pos_sum, neg_sum = 0.0, 0.0
        pos_n, neg_n = 0, 0

        for i, w in enumerate(words):
            w = w.strip()
            if not w or w in self.dict.stopwords:
                continue

            if w in self.dict.pos_words:
                score = self.dict.pos_words[w]
                # 检查前方 3 词否定窗口
                ctx = "".join(words[max(0, i-3):i])
                if any(n in ctx for n in self.dict.negators):
                    neg_sum += abs(score) * 0.6  # 否定反转
                    neg_n += 1
                else:
                    pos_sum += score
                    pos_n += 1

            elif w in self.dict.neg_words:
                score = self.dict.neg_words[w]
                ctx = "".join(words[max(0, i-3):i])
                if any(n in ctx for n in self.dict.negators):
                    pos_sum += abs(score) * 0.4  # "不跌" = mild positive
                    pos_n += 1
                else:
                    neg_sum += score
                    neg_n += 1

        return pos_sum, neg_sum, pos_n, neg_n

    def _emoji_score(self, text: str) -> tuple:
        """Emoji/表情符 打分"""
        delta = 0.0
        hits = 0
        for emo, val in EMOJI_SENT.items():
            cnt = text.count(emo)
            if cnt:
                delta += val * cnt
                hits += cnt
        for em, val in TEXT_EMOTICONS.items():
            cnt = text.count(em)
            if cnt:
                delta += val * cnt
                hits += cnt
        return delta, hits

    def extract_keywords(self, text: str, topn: int = 10) -> list:
        """提取关键词 (jieba TF-IDF, 过滤停用词)"""
        text = clean_text(text)
        if not text:
            return []
        keywords = jieba.analyse.extract_tags(
            text, topK=topn * 2, withWeight=True
        )
        result = []
        for word, weight in keywords:
            if word not in self.dict.stopwords and len(word) > 1:
                result.append((word, round(weight, 3)))
                if len(result) >= topn:
                    break
        return result

    @staticmethod
    def _empty_result():
        return {
            "score": 0.0, "label": "中性",
            "pos_hits": 0.0, "neg_hits": 0.0,
            "emoji_delta": 0.0, "clauses": [],
        }


# ══════════════════════════════════════════════════════
#  5. Aggregator (多帖聚合)
# ══════════════════════════════════════════════════════

def aggregate_posts(scored_posts: list[dict]) -> dict:
    """
    聚合多条帖子的情感结果。
    输入: [{score, label, pos_hits, neg_hits, ...}, ...]
    输出: 统计摘要
    """
    if not scored_posts:
        return {"count": 0, "avg_score": 0, "distribution": {}, "top_keywords": []}

    scores = [p["score"] for p in scored_posts]
    labels = [p["label"] for p in scored_posts]

    distribution = {}
    for l in labels:
        distribution[l] = distribution.get(l, 0) + 1

    return {
        "count": len(scored_posts),
        "avg_score": round(sum(scores) / len(scores), 3),
        "distribution": distribution,
        "score_range": (round(min(scores), 2), round(max(scores), 2)),
    }
