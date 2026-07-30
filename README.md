![image](https://github.com/acchair/moda-v3-agent-skill/blob/17a0930388984c2a90307d119618931949a2fcbb/ChatGPT%20Image%202026%E5%B9%B47%E6%9C%8825%E6%97%A5%2020_24_27.png)

如果您觉得帮助到你进行选股,欢迎到雪球帖子下进行打赏
![image](https://github.com/acchair/moda-v3-agent-skill/blob/2e50bfe35ad24b1cce924ab0a036db3b8d5d5971/_2026-07-31_000022_473.png)
https://xueqiu.com/u/1500823973?scene=1036&share_uid=1500823973&share_type=weixin&data_type=link&data_model=utl&fix_uid=1500823973

# 如何安装 moda-v3

## 让你的 Agent 按五层框架研究 A 股

选择你自己习惯的 Agent，复制一段提示词，Agent 会完成安装和分析准备。

五层评分 | 数据来源标注 | 保守硬约束

## 配置 Agent

### 一段提示词

将以下内容粘贴给可访问本私有仓库的 Agent：

```text
请将 https://github.com/acchair/moda-v3-agent-skill 安装为 moda-v3 Skill：克隆到你的 skills 目录，安装 requirements.txt，读取 SKILL.md，并保持 tools 与 knowledge 的相对目录结构。随后按五层评分框架分析 A 股。
```

### 兼容方式

| 平台 | 使用方式 | 状态 |
|---|---|---|
| Codex | 读取 `SKILL.md` 与 `agents/openai.yaml` | 推荐 |
| Claude Code | 将仓库放入其 Skills 目录后读取 `SKILL.md` | 支持 |
| Hermes Agent | 粘贴上述提示词并授予私有仓库访问权限 | 支持 |
| 其他支持 `SKILL.md` 的 Agent | 复制仓库到对应 Skills 目录 | 支持 |

## 评分框架

| 因子 | 满分 | 核心判断 |
|---|---:|---|
| F1 产业趋势与资本开支 | 30 | 行业景气、政策、供需、产能和订单 |
| F2 股东与筹码 | 15 | 增减持、质押、解禁和股东户数 |
| F3 生存能力与龙头 | 20 | 营收、利润、现金、负债和行业地位 |
| F4 利润兑现路径 | 15 | 主营、订单、产能、收入和公告兑现 |
| F5 低位与困境反转 | 20 | PE/PB、价格位置、估值和反转证据 |
| 合计 | 100 | 仅对有来源的证据评分 |

评级：`>=85 根`、`>=70 矛`、`>=55 学习仓`、`<55 不碰`。

硬约束：ST 或退市风险直接为 `不碰`；控股股东或实控人减持最高为 `学习仓`；缺失数据一律标注 `需人工确认`，不得自动转为正面结论。

## 运行分析

```powershell
python -m pip install -r requirements.txt
python tools/run_pipeline.py --stock 000001 --name 平安银行
```

报告写入 `knowledge/research/`。最终答复可通过 `tools/export_skill_output.py` 导出；设置 `MODA_OUTPUT_DIR` 可以更改导出目录。

## 使用流程

### 1. 给 Agent 下达任务

直接提供股票名称或 6 位代码，例如：

```text
/moda-v3 中国平安或者股票代码
```

### 2. 运行基础流水线

Agent 自动确认代码后运行：

```powershell
python tools/run_pipeline.py --stock 000001 --name 平安银行
```

流水线先获取一次共享日K，并行生成基本面与行情、技术因子和公告互动报告，最后生成五层评分报告。基本面使用 easy_tdx/TDX 行业与同行快照，并通过 easy_tdx/Sina 获取三张财报。

### 3. 读取报告并核对来源

重点读取以下文件：

```text
knowledge/research/finance_data/000001.md
knowledge/research/tdx_analysis/000001.md
knowledge/research/announcements/000001.md
knowledge/research/scoring/000001.md
```

每项结论都保留来源标签；报告缺失、接口失败或无法交叉验证的字段统一标记为 `需人工确认`。

### 4. 形成最终结论

按 F1 至 F5 汇总分数，再依次检查 ST/退市风险、控股股东或实控人减持、估值过热和产业逻辑证伪。结论只能使用 `根`、`矛`、`学习仓` 或 `不碰`。

### 5. 导出答复

将最终答复写入 UTF-8 文本后执行：

```powershell
python tools/export_skill_output.py --stock 000001 --name 平安银行 --input final.md
```

默认导出到 `knowledge/output/`；可设置 `MODA_OUTPUT_DIR` 改为其他目录。

## 隐私边界

仓库不包含浏览器二进制、登录状态、Cookie、本机日志或历史报告。可选代理凭据只从环境变量读取，禁止提交到仓库。

## A股研究工作台

```powershell
python -m pip install -r requirements.txt
python -m tools.webapp.start
```

终端会输出本地地址，默认从 `http://127.0.0.1:8765` 开始寻找可用端口。工作台提供研究池、产业图谱、特征矩阵、股票列表、主动发现和 A 股市场压力，并可直接运行当前 5 模块流水线。

- 产业候选只使用 SQLite 中的精确行业关系。
- 行情优先使用 `easy_tdx`，失败时保留缺失状态。
- 压力分数至少需要 70% 指标权重，否则不输出总分。
- `market_dashboard.db` 和 `workbench.db` 是本地可变数据，不提交到仓库。
- `/dashboard` 保留为兼容入口并跳转到统一工作台。
