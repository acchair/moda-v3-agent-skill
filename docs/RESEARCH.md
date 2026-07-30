# moda v3 数据源调研记录

## 1. 调研范围

本文件记录 2026-07-12 至 2026-07-13 在本机对 `000001 平安银行` 的实际验证结果。结论描述的是当前环境和当前接口状态，不代表数据源长期稳定。

## 2. easy_tdx 1.19.0

### 已验证可用

| 能力 | 接口/用途 | 结果 |
|---|---|---|
| 日/月K线 | `get_stock_kline` | 可用，TDX 实测 800 条日K |
| 实时行情 | `get_stock_quotes` | 可用 |
| 公告 | `CninfoClient.get_announcements` | 可用，实测近30日2条 |
| 三张财报 | `SinaClient.get_financial_report` | 可用，每类实测8期 |
| 所属板块 | `get_belong_board` | 可用，可识别股份制银行等行业 |
| 同行行情与估值 | `get_board_members` | 可用，实测9家同业 |
| 当前资金流 | `get_capital_flow` | 可用，作为东财120日资金流失败后的当前快照 |

### 可派生数据

- 营收和净利润增长率
- 简化杜邦：ROE、净利率、资产周转率、权益乘数
- 当前 PE/PB 快照
- 同行业 PE/PB 对比

### 不能覆盖

- 机构研报
- 个股新闻正文
- 未来限售解禁计划
- 海外收入和出口占比
- 雪球/东财社区情绪
- 乐咕行业拥挤度
- 完整公司档案和主营描述

`get_symbol_info` 是行情级证券信息，不应当作完整公司资料。

### 已处理问题

`MacClient.from_best_host()` 会写入用户目录下的共享配置。多个智能体或进程并发运行时可能触发 Windows 文件占用错误。当前适配器自行选择主机并直接创建客户端，不再并发写配置。

## 3. AKShare 与东财直连

### 观察

- `stock_zh_a_hist`、实时行情和资金流端点可能出现 `RemoteDisconnected`。
- 同一环境中，人气、新闻和部分数据中心接口仍可正常返回。
- 浏览器页面请求头不应直接复用于 API 请求，可能增加断连概率。

### 当前策略

- 行情和K线由 easy_tdx 优先。
- 东财直连只承担其独有数据，失败后快速降级，不长时间重复重试。
- `a_stock_data_provider.py` 统一处理多种响应结构。
- 限售解禁零行视为“当前查询区间无事件”，不是接口失败。

实际验证中，市场事件模块达到 `10/10`：研报、板块、龙虎榜、解禁、融资融券、大宗交易、股东户数、分红、资金流和新闻均完成采集或有效空结果判定。

## 4. 财务与估值降级

- 利润表、资产负债表、现金流：easy_tdx/Sina
- 成长性：由财报同比字段生成
- 简化杜邦：由利润表和资产负债表计算
- 行业：easy_tdx 所属板块
- 估值：easy_tdx 当前快照

旧深度财报模块已从运行时、依赖和报告链移除。历史 PE/PB 分位暂不输出；只有当前估值时，结论必须标记为当前快照。

AxData 已完成接口调研。财务与公司侧可用 `stock_finance_summary_tdx`、`stock_profit_cashflow_summary_tdx`、`stock_balance_summary_tdx`、`stock_company_profile_tdx` 和 `stock_business_composition_tdx`；估值与同行侧可用 `stock_valuation_metrics_tdx`、`stock_valuation_series_tdx`、`stock_valuation_band_tdx` 和 `concept_constituent_comparison_tdx`；股东侧可用 `stock_share_capital_tdx` 与 `stock_shareholder_change_plans_tdx`。其中历史估值序列是当前最明确的增量，但这些接口多数是 source-request-only，TDX 源与现有 easy_tdx 重叠，AxData 0.1.3 仍为 Alpha 且会新增 `pyarrow`、DuckDB 等依赖，因此暂不加入生产链。

2026-07-30 对 `300820` 复核确认：easy_tdx 可直接返回“其他发电设备”行业及 25 家成分股的实时 PE、TTM PE、每股净资产和市值；easy_tdx/Sina 可返回利润表、资产负债表和现金流量表。因此当前先复用已有适配器，只有需要历史 PE/PB Band 或股东增减持增强时再评估 AxData。

## 5. CloakBrowser

### 已验证

- 自动后台启动和端口 `9222` 检测可用。
- 保留包内 `user-data` 登录状态。
- 雪球热帖实测 10 条。
- 东财股吧实测 20 条并按作者去重。
- 东财F10无地区数据时，同花顺主营页面可作为出口占比备用。

### 风险

- 页面结构变化会使选择器失效。
- 登录 Cookie 和浏览器用户目录属于敏感本地状态，不应提交或外传。
- 同花顺地区分类需要保留原始行，避免把产品分类误判为地区收入。

## 6. 乐咕乐股

旧实现从 HTML 表格提取行业拥挤度，但当前页面已改为图表组件。新实现通过 CloakBrowser 页面上下文读取其实际 JSON 接口。

实测结果：

- 取得 131 个申万二级行业。
- easy_tdx 将平安银行映射到“股份制银行”。
- 行业拥挤度状态为 `neutral`。
- 全市场拥挤度仍由 AKShare `stock_a_congestion_lg` 提供。
- 市场宽度页面仍依赖网页结构，应视为增强证据而非硬依赖。

## 7. QVeris 备用调研

QVeris 的 Gildata A 股公告工具已验证可返回有效公告，但会消耗额度。当前 easy_tdx/CNINFO 公告链已可用，因此没有把 QVeris 加入生产依赖。

## 8. A 股市场看板数据口径

市场看板不调用乐咕乐股会员接口。行业面板通过项目现有 `a_stock_data_provider._get_json` 请求东方财富公开行业列表 `push2 ... /clist/get` 及行业板块日线 `push2his ... /stock/kline/get`；已验证 `90.BK1027` 日线可返回历史成交额。首屏只显示已经取得的真实快照与 SQLite 缓存，后台再按板块补齐最多 60 个交易日。占比的分母为同日已取得行业板块成交额之和，横截面排名用于红高绿低热力色，不将缺失日期、行业或成交额填为零。若东方财富临时断连且本地缓存为空，`easy_tdx` 当前行业排行作为无日期快照降级，按该接口返回集合计算比例，绝不伪装为历史数据。

融资面板使用 AKShare `macro_china_market_margin_sh` 与 `macro_china_market_margin_sz` 的融资买入额，按共同交易日合并；上证指数日线优先使用 `stock_zh_index_daily_em`，失败时尝试 `stock_zh_index_daily`。分母使用 `stock_sse_deal_daily` 的主板 A 股与科创板成交金额（该接口为亿元，转换为元），加上 `stock_szse_summary` 的主板 A 股与创业板 A 股成交金额（元）。这一定义为“沪深 A 股融资买入 / 成交额”，不含北交所，也不再使用上证指数成交额代理。四个成交额分项任一缺失，`market_turnover` 与 `ratio` 均为 `null`。

状态语义：`LIVE` 表示本次在线数据完整，`CACHE` 表示进程或本地 SQLite 缓存可读，`PARTIAL` 表示日期、行业、融资侧或成交额分母不完整，`UNAVAILABLE` 表示没有可展示数据，`SYNCING` 表示已返回真实可读数据但后台尚在补齐。接口同时返回来源、数据日期、缓存年龄、同步进度和失败原因，便于复核。

## 9. 当前端到端基线

当前 `tools/run_pipeline.py` 先用 easy_tdx 获取一次共享日K，再并行执行 `finance_data`、`tdx_analysis` 和 `announcements`，最后执行 `scoring`。网页工作台直接调用该入口，不维护独立模块清单。

每个模块同时检查退出码和报告更新时间，评分器只接收本次成功模块的新报告，最终状态写入 `knowledge/research/pipeline/{code}.json`。外部数据源失败时保留模块错误，不能把旧报告冒充为本次成功结果。

## 10. Webapp 参考项目

本次工作台参考 `yaoleifly/ai-stock-pool` 的信息组织方式：左侧筛选、顶部决策摘要、图谱/矩阵/列表多视图、主动发现和压力指标。

采用内容：

- 紧凑研究工作台布局；
- 候选与正式研究池分离；
- 每项指标展示来源、日期和缺失状态；
- 原生 JavaScript、CSS 和 SVG。

未采用内容：

- 美股股票池和跨市场映射；
- 新闻、arXiv 和美国政策压力数据；
- Yahoo Finance 行情依赖；
- 参考项目的静态数据文件和部署链。

moda v3 的候选来自本地行业成交额历史与精确产业关系，市场压力来自融资占比、上证指数和行业成交结构。
