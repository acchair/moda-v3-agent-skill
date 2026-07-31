# moda v4 架构

## 流程

```text
股票代码/名称
  -> tools/run_pipeline.py
     -> 共享日 K 缓存
     -> 第一阶段并行采集（8 模块）
     -> 主营与行业上下文
     -> 第二阶段采集（供需、宏观政策、可选搜索验证）
     -> 结构化证据合并
     -> 24 子项基础分 + ±8 修正
     -> 4 类 Hard Cap
     -> 固定格式 Markdown + JSON scorecard
```

## 关键边界

- `tools/scoring/evidence.py` 只读取本次运行后新生成的报告。
- 采集器通过 `<!-- moda_*: {...} -->` 输出结构化数据，Markdown 正文用于人工复核。
- `tools/scoring/model.py` 是唯一评分规则源。
- `tools/scoring/grader.py` 只负责合并、评分和报告渲染。
- TDX 是 Alpha 原始分来源；两项机构技术复核只允许在双重冲突时向 0 收缩 1 分。
- 其他机构方法在评分后运行，不向基础分、修正项或 Hard Cap 回写。

## 两阶段采集

第一阶段：财务、主营、技术、公告、市场事件、人气、社交热榜、市场拥挤度。

第二阶段：根据第一阶段得到的行业、主营和概念运行商品供需映射、宏观政策和可选搜索验证，减少错误行业匹配。

## 降级

- 单模块失败：记录失败，不读取旧报告。
- 商品映射未命中：不强行生成供需分。
- 搜索后端未配置、403、超时或正文读取失败：保留错误状态，不把摘要当证据。
- 社交平台失败：其他平台继续；少于 3 个平台可用时热度标记不完整。
- 拥挤度过期：展示但不计分、不触发 Hard Cap。
- 评分器始终对缺失项输出 0 分和 `需人工确认`。

## 输出目录

- `knowledge/research/{module}/{code}.md`：模块证据。
- `knowledge/research/scoring/{code}.md`：最终固定格式报告。
- `knowledge/research/scorecards/{code}.json`：证据与评分结构。
- `knowledge/research/pipeline/{code}.json`：本次模块状态。
- `knowledge/research/web_research/{code}.md`：搜索查询、正文读取状态和交叉验证结果。

运行数据由 `.gitignore` 排除。
