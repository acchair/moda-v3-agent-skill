# moda v3 架构说明

## 1. 总体结构

```text
Agent / 命令行
  -> tools/run_pipeline.py
     -> easy_tdx 共享日 K 缓存
     -> 并行采集
        -> tools/akshare/finance_data.py
        -> tools/tdx/analyzer.py
        -> tools/akshare/announcements.py
     -> tools/scoring/grader.py
     -> knowledge/research/
```

项目不包含网页服务、前端资源或本地 Web 数据库。

## 2. 统一流水线

`tools/run_pipeline.py` 执行以下流程：

1. 使用 `easy_tdx` 获取一次日 K，并写入本次共享缓存。
2. 并行运行基本面、技术和公告模块。
3. 基本面模块并行获取行情、TDX 行业同行和 Sina 三张财报。
4. 公告模块并行获取公告与互动易数据。
5. 只把本次成功且新生成的报告交给评分器。
6. 将模块状态写入 `knowledge/research/pipeline/{code}.json`。

## 3. 评分规则

评分器先根据报告证据建立 F1-F5 基础分，再用营收、归母净利润、经营现金流、资产负债率、现金覆盖负债、负利润、负 PE 和同行 TTM PE 约束矛盾高分。

- F1 低于 15 或 F3 低于 8：评级最高为学习仓。
- 控股股东或实控人减持：评级最高为学习仓。
- ST 或退市风险：评级为不碰。
- 缺失或失败来源：标记需人工确认，不补造正面结论。

## 4. 数据与输出

- `knowledge/research/finance_data/`：基本面和行情报告。
- `knowledge/research/tdx_analysis/`：技术分析报告。
- `knowledge/research/announcements/`：公告与互动报告。
- `knowledge/research/scoring/`：五层评分报告。
- `knowledge/research/pipeline/`：本次模块状态与共享缓存。
- `knowledge/output/`：按需导出的最终答复。

运行生成的数据不提交到 Git。`MODA_OUTPUT_DIR` 可覆盖最终答复导出目录。

## 5. 降级规则

- 共享日 K 获取失败：各模块按自身降级路径继续运行。
- 单个采集模块失败：保留错误状态，评分器不读取该模块的旧报告。
- 覆盖不足：输出覆盖率并标记需人工确认。
- AxData：仅在 `MODA_AXDATA=1` 时作为可选增强，不属于默认依赖。

## 6. 安全边界

只接受 6 位 A 股代码。密码、Token、Cookie、私钥和代理凭据不得写入代码、日志、报告或仓库，只能从环境变量读取。
