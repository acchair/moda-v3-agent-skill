# moda-v4

![moda-v4](./ChatGPT%20Image%202026%E5%B9%B47%E6%9C%8825%E6%97%A5%2020_24_27.png)

本地运行的 A 股五因子研究 Skill。它保留旧版最终评分框架，修复关键词评分上限不足、报告格式漂移、修正项未落地和数据链缺失问题。

## 核心规则

- 24 个结构化子项组成 100 分基础分。
- Alpha、情绪/拥挤度、风口催化组成 ±8 修正项。
- 4 类 Hard Cap 实际参与评级。
- 数据缺失得 0 分并标记 `需人工确认`。
- 18 项机构方法按适用条件展示；其中两项只允许对冲突 Alpha 降级，不额外加分。
- 可选 SearXNG / DuckDuckGo MCP 搜索补缺，正文未核验不计分。

## 安装

```powershell
cd "C:\Users\Administrator\Desktop\moda v4"
python -m pip install -r requirements.txt
```

## 运行

```powershell
python tools/run_pipeline.py --stock 300820 --name 英杰电气
```

### 可选搜索验证

默认不依赖外部搜索服务。需要验证供需失衡和国产替代时，设置以下环境变量：

```powershell
$env:MODA_SEARCH_PROVIDER="auto"
$env:SEARXNG_URL="http://127.0.0.1:8888"
$env:DDG_MCP_URL="http://127.0.0.1:7070/mcp"
python tools/run_pipeline.py --stock 300820 --name 英杰电气
```

`auto` 先调用 SearXNG，再降级到 DuckDuckGo MCP。两个 URL 都未配置时，搜索模块只报告 `需人工确认`，不影响其他模块。

DuckDuckGo MCP 可用以下方式启动：

```powershell
uvx --with "duckduckgo-mcp-server[browser]" duckduckgo-mcp-server --transport streamable-http --host 127.0.0.1 --port 7070
```

主要结果：

```text
knowledge/research/scoring/300820.md
knowledge/research/scorecards/300820.json
knowledge/research/pipeline/300820.json
```

## 评分框架

| 因子 | 满分 | 子项数 |
|---|---:|---:|
| F1 产业趋势与资本开支 | 30 | 5 |
| F2 股东与筹码 | 15 | 5 |
| F3 生存能力与龙头 | 20 | 5 |
| F4 利润兑现路径 | 15 | 4 |
| F5 低位与困境反转 | 20 | 5 |
| 合计 | 100 | 24 |

评级：`>=85 根`、`70-84 矛`、`55-69 学习仓`、`<55 不碰`。

Hard Cap：

- 控股股东/实控人减持：最高学习仓。
- ST/退市风险：不碰。
- F1 < 15 或 F3 < 8：最高学习仓。
- 价格分位 >80% 且新鲜市场拥挤度 >=80%：最高矛。

## 数据模块

| 模块 | 内容 |
|---|---|
| finance_data | 行情、财务、估值、三年价格分位 |
| business_data | 主营构成、海外收入 |
| tdx_analysis | Alpha、趋势、技术信号、过热状态 |
| announcements | 180 日公告、增减持、审计与催化 |
| market_events | 股东、户数、质押、解禁、概念、研报 |
| popularity | EastMoney 个股人气排名 |
| social_sentiment | 微博/知乎/百度/抖音/头条/B站热榜与异常推广词 |
| congestion | 全市场拥挤度及新鲜度 |
| supply_demand | 商品现货、基差、库存交叉验证 |
| macro_policy | LPR、PMI、中国政府网最新政策 |
| web_research | SearXNG / DuckDuckGo MCP 搜索、正文读取和双来源验证 |
| scoring | 评分、修正、Hard Cap 和固定格式报告 |

## hot-money 借鉴范围

参考 [godisego/hot-money](https://github.com/godisego/hot-money) 后，v4 采用了多平台独立降级、短缓存、热榜命中明细和异常推广风险交叉验证。没有采用人物角色评分，也没有把简单关键词命中直接当事实。

该项目对机构方法的数量口径并不一致：标题写 17 种，当前索引实际列出 18 种。v4 按实际方法逐项显示状态。量化筛选与投资逻辑追踪仅在同时反向时将 Alpha 向 0 收缩 1 分；其余方法不计分。DCF、LBO、三表、并购、单位经济学和组合再平衡必须满足数据条件才启用。

## 输出顺序

总分与评级 → 五层评分卡 → 24 子项证据 → 修正项 → 舆情与异常推广风险 → Hard Cap → 机构方法交叉验证 → 睡得着检查 → 动态纠错 → 数据覆盖 → 六段莫大框架式结论。

## 验证

```powershell
python -m unittest discover -s tools -p "test*.py" -v
python C:\Users\Administrator\.codex\skills\.system\skill-creator\scripts\quick_validate.py "C:\Users\Administrator\Desktop\moda v4"
```

## 安全与免责声明

仓库不保存密码、Token、Cookie、私钥、代理凭据或浏览器登录状态。本项目只用于研究与学习，不构成投资建议，不包含自动交易或账户操作。

## 支持项目

可以前往[雪球主页](https://xueqiu.com/u/1500823973)了解作者的公开研究。

[![支持作者](./_2026-07-31_000022_473.png)](https://xueqiu.com/u/1500823973)
