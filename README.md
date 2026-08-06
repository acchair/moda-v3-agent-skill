# moda-v4

![moda-v3](./ChatGPT%20Image%202026%E5%B9%B47%E6%9C%8825%E6%97%A5%2020_24_27.png)

面向 A 股研究的六层评分 Skill，支持数据来源标注、保守硬约束和固定格式输出。

> 六层评分 · 来源可追溯 · 缺失数据不猜测 · Hard Cap 风险控制

## 快速安装

将下面的提示词交给Agent：

```text
请将 https://github.com/acchair/moda-v3-agent-skill 安装为 moda-v3 Skill：克隆到你的 skills 目录，安装 requirements.txt，读取 SKILL.md，并保持 tools 与 knowledge 的相对目录结构。随后按六层评分框架分析 A 股。
```

也可以手动安装：

```bash
git clone https://github.com/acchair/moda-v3-agent-skill.git
cd moda-v3-agent-skill
python -m pip install -r requirements.txt
```

## 使用方法

直接向 Agent 提供股票名称或六位股票代码，例如：

```text
/moda-v3 中国平安
/moda-v3 601318
```

Agent 会确认股票代码并运行基础流水线：

```bash
python tools/run_pipeline.py --stock 000001 --name 平安银行
```

流水线会获取行情、财务、技术因子和公告数据，并生成六层评分报告。报告保存在 `knowledge/research/`。

重点报告：

```text
knowledge/research/finance_data/000001.md
knowledge/research/tdx_analysis/000001.md
knowledge/research/announcements/000001.md
knowledge/research/scoring/000001.md
```

## 输出格式

最终分析固定按以下顺序输出：

1. 原始综合分、研究分、覆盖率、行动评级与技术信号
2. 一句话结论与六段最终判断
3. easy-tdx 技术指标与缠论日线结构
4. 六层图形概览
5. 六层评分卡及 F1-F6 逐项诊断
6. 舆情、社交热榜与异常推广风险
7. Hard Cap 检查
8. 机构方法交叉验证
9. 睡得着检查
10. 动态纠错触发器
11. 数据覆盖、待确认项与免责声明

每个关键判断都标注实际数据来源。报告缺失、接口失败或无法交叉验证的内容统一标记为 `需人工确认`，不会自动转为负面结论；报告另外显示未知可得分上限、已确认扣分和最终行动状态。

## 六层评分框架

| 因子 | 满分 | 核心判断 |
|---|---:|---|
| F1 产业趋势与资本开支 | 30 | 行业景气、政策、供需、产能和订单 |
| F2 股东与筹码 | 15 | 增减持、质押、解禁和股东户数 |
| F3 生存能力与龙头 | 20 | 营收、利润、现金、负债和行业地位 |
| F4 利润兑现路径 | 15 | 主营、订单、产能、收入和公告兑现 |
| F5 低位与困境反转 | 10 | PE/PB、价格位置、市场冷落、业绩拐点和预期差 |
| F6 修正项 | 10 | 技术结构、机构方向、情绪拥挤和风口催化 |
| **合计** | **100** | **原始分保留兼容；研究分按已知证据归一化** |

行动评级标准：

| 分数 | 评级 |
|---:|---|
| `>=85` | 根 |
| `70-84` | 矛 |
| `55-69` | 学习仓 |
| `<55` | 不碰 |
| 覆盖率 `<60%` 且无 Hard Cap | 待补证 |

Hard Cap：

- ST 或退市风险：直接评为 `不碰`。
- 控股股东或实控人减持：最高评为 `学习仓`。

搜索结果按来源分级：巨潮资讯、沪深北交易所等法定信息披露正文可作为高确信度证据；雪球、东方财富、大智慧等金融论坛只用于收集线索，不参与确认或计分。

## 导出结果

需要保存完整答复时，将最终内容写入 UTF-8 文本并执行：

```powershell
python tools/export_skill_output.py --stock 000001 --name 平安银行 --input final.md
```

默认导出到 `knowledge/output/`。可通过环境变量 `MODA_OUTPUT_DIR` 更改目录。

## 平台兼容

| 平台 | 使用方式 | 状态 |
|---|---|---|
| Codex | 读取 `SKILL.md` 与 `agents/openai.yaml` | 推荐 |
| Claude Code | 将仓库放入 Skills 目录并读取 `SKILL.md` | 支持 |
| Hermes Agent | 使用安装提示词并授予仓库访问权限 | 支持 |
| OpenMinis | 在手机端安装本 Skill；使用 Python/Linux 沙箱执行数据模块 | 支持 |
| 其他支持 `SKILL.md` 的 Agent | 复制仓库到对应 Skills 目录 | 支持 |

### 手机端

[![OpenMinis](https://openminis.app/icon-dark.png)](https://openminis.app/)

访问 [OpenMinis](https://openminis.app/) 下载手机端 Agent，即可安装并使用本 Skill。移动端不依赖 PowerShell、Windows 路径或本机浏览器登录状态；缺少本地搜索服务时自动降级到公共搜索，并保留来源状态。

Windows 使用 `python` 也可以。Apple/macOS 和 OpenMinis 使用 `python3`；Apple 移动端应将仓库放入 Agent 的工作区，由 Agent 的 Linux 沙箱执行。所有报告仍写入 `knowledge/research/`，不依赖 Windows 专用路径。

## 隐私与安全

仓库不包含浏览器登录状态、Cookie、本机日志或历史分析报告。可选代理凭据只从环境变量读取，禁止写入代码、报告或提交记录。

## 支持项目

如果这个项目对选股研究有帮助，可以前往[雪球主页](https://xueqiu.com/u/1500823973?scene=1036&share_uid=1500823973&share_type=weixin&data_type=link&data_model=utl&fix_uid=1500823973)支持作者。

[![支持作者](./_2026-07-31_000022_473.png)](https://xueqiu.com/u/1500823973?scene=1036&share_uid=1500823973&share_type=weixin&data_type=link&data_model=utl&fix_uid=1500823973)

## 免责声明

本项目仅供研究与学习，不构成任何投资建议。
