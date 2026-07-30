# moda v3 架构说明

## 1. 总体结构

```text
浏览器工作台
  -> FastAPI tools/webapp/app.py
     -> 研究池与候选 tools/webapp/workbench.py
     -> 产业链 SQLite tools/webapp/chain_db.py
     -> 市场面板 tools/webapp/dashboard.py
     -> 任务调度 tools/webapp/runner.py
        -> tools/run_pipeline.py
           -> 4 个研究模块 -> knowledge/research/
```

前端使用原生 HTML、CSS、JavaScript 和 SVG，不需要 Node 构建链或图表依赖。

## 2. 统一流水线

网页和命令行使用同一入口 `tools/run_pipeline.py`：

1. `easy_tdx` 获取一次日K并写入本次共享缓存。
2. 并行运行 `tools/akshare/finance_data.py`、`tools/tdx/analyzer.py` 和 `tools/akshare/announcements.py`；基本面内部并行取行情、TDX 行业同行和 Sina 三张财报，公告内部并行取公告与互动易。
3. 将本次成功且新生成的报告交给 `tools/scoring/grader.py`。

基本面报告内嵌本次结构化指标，评分器以关键词建立基础分，再用营收、归母净利润、经营现金流、资产负债率、现金覆盖负债、负利润、负 PE 和同行 TTM PE 限制明显矛盾的高分。F1<15 或 F3<8 时评级最高为学习仓。评分报告使用五列表格，网页解析同一格式并显示实际数据源与因子覆盖率。

任务调度器只负责启动该入口、转发日志和读取 `knowledge/research/pipeline/{code}.json`，不维护第二份模块清单。

## 3. 本地数据

- `a_stock_chain.db`：随项目分发的产业链基准库，支持 `A_STOCK_CHAIN_DB` 覆盖路径。
- `market_dashboard.db`：行业成交额和市场成交额缓存，不提交 Git。
- `workbench.db`：研究池状态与备注，不提交 Git。
- `knowledge/research/`：运行生成的模块报告和 pipeline JSON。

研究池为显式 `watch/core` 状态与已有评分报告的并集；显式 `ignore` 会隐藏对应标的。

## 4. API

- `/api/pool`：研究池筛选、分页和状态维护。
- `/api/quotes`：最多 50 只 A 股的 easy_tdx 行情。
- `/api/discovery`：行业升温和精确公司关系候选。
- `/api/market-pressure`：四项 A 股市场压力指标。
- `/api/chain/*`：公司、行业、产品和图谱关系。
- `/api/analyze/*`、`/api/jobs/*`：流水线启动、状态和日志。
- `/api/reports/{code}`：报告原文和结构化 F1-F5 摘要。
- `/api/dashboard/market`：底层行业与融资市场面板，保留兼容。

## 5. 降级规则

- 行情失败：返回该股票 `unavailable`，其他股票照常返回。
- 行业历史不足：主动发现为空，不使用模糊名称补候选。
- 压力有效权重不足 70%：状态为 `unavailable`，总分为 `null`。
- 单个流水线模块失败：任务标记为部分完成并保留成功报告与错误日志。
- 评分只读取本次成功模块的新报告，不复用旧报告。
- 模块结果附带覆盖率；覆盖不足显示在流水线状态中，但不把缺失数据伪装成正面结论。
- AxData 仅作为显式开启的可选增强，不增加默认运行时依赖。
- 产业链数据库缺失：相关接口返回不可用状态，不影响报告读取。

## 6. 安全边界

只接受 6 位 A 股代码；研究池状态使用固定枚举；备注限制 500 字。密码、Token、Cookie 和代理凭据只能来自环境变量，不写入网页、日志或数据库。
