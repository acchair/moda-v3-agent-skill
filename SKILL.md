---
name: moda-v3
description: Analyze mainland China A-share stocks with the moda-v3 five-factor framework, local data-collection scripts, source-labeled evidence, hard caps, and disciplined ratings. Use when the user supplies an A-share code or asks to analyze an A-share company, valuation, industry position, shareholder risk, technical setup, or a research decision.
---

# Moda V3 A-share Analysis

## Run

1. Resolve a supplied stock name with the bundled market-data adapter first: `python -c "from tools.efinance.provider import search_stock; print(search_stock('{name}', 5))"`. Use external search only when the local result is empty; if uncertain, state the ambiguity instead of guessing.
2. From this skill directory, run:

```powershell
python tools/run_pipeline.py --stock {code} --name {name}
```

3. Read `knowledge/research/pipeline/{code}.json`, then read only reports whose current-run module status is `ok: true`. If a source fails, mark the relevant conclusion `需人工确认`; never read an older report as a fallback.
4. Only when the user requests a file or reusable artifact, export the exact final response with:

```powershell
python tools/export_skill_output.py --stock {code} --name {name}
```

Pass the response through standard input or `--input`. The default output directory is `knowledge/output/`; set `MODA_OUTPUT_DIR` to override it.

## Output Rules

- 使用中文、客观分析师语言，不使用第一人称，不模仿人物口吻。
- 严格使用下方固定模板。标题、顺序和五个因子都不得省略；不要改成自由摘要、散文或只有一张总表。
- 第一行必须同时给出总分、评级和技术信号。评级只能是 `根`、`矛`、`学习仓`、`不碰`；技术信号取自本次 `[TDX]` 报告，缺失时写 `需人工确认`，不得根据评级臆造。
- 每个关键判断紧跟实际来源标签，例如 `[easy_tdx/TDX]`、`[easy_tdx/Sina]`、`[a-stock-data]`、`[AKShare]` 或 `[TDX]`。
- 评分报告中的关键词分只作为基础分；营收、利润、现金流、负债或相对估值等结构化数据与关键词证据矛盾时，必须采用财务封顶结果。
- 模块或因子覆盖不完整时写 `需人工确认`，不得把缺失数据转成正面结论，也不得读取旧报告补位。
- 应用 Hard Cap：控股股东或实控人减持最高为 `学习仓`；ST 或退市风险为 `不碰`；F1 低于 15 或 F3 低于 8 最高为 `学习仓`。
- 内容只用于研究，不构成投资建议。

## Fixed Output Template

占位符必须替换为本次报告中的实际内容。证据不足时保留对应栏目并填写 `需人工确认`。

```markdown
总分：{X}/100｜评级：{根/矛/学习仓/不碰}｜技术信号：{本次 TDX 信号或需人工确认}

# {股票名称}（{六位代码}）五层诊断

## 一句话结论
{用一至两句话说明最强逻辑、最大短板和当前评级原因。} {来源标签}

## 五层评分卡
| 因子 | 得分 | 核心判断 | 状态 |
|---|---:|---|---|
| F1 产业趋势与资本开支 | {X}/30 | {一句话} {来源标签} | {已验证/部分覆盖/需人工确认} |
| F2 股东与筹码 | {X}/15 | {一句话} {来源标签} | {已验证/部分覆盖/需人工确认} |
| F3 生存能力与龙头 | {X}/20 | {一句话} {来源标签} | {已验证/部分覆盖/需人工确认} |
| F4 利润兑现路径 | {X}/15 | {一句话} {来源标签} | {已验证/部分覆盖/需人工确认} |
| F5 低位与困境反转 | {X}/20 | {一句话} {来源标签} | {已验证/部分覆盖/需人工确认} |

## F1 产业趋势与资本开支（{X}/30）
- 支撑证据：{产业、政策、供需、产能或订单证据。} {来源标签}
- 扣分与缺口：{反证或需人工确认的数据。} {来源标签或需人工确认}

## F2 股东与筹码（{X}/15）
- 支撑证据：{增减持、持股、股东户数、质押或解禁证据。} {来源标签}
- 扣分与缺口：{反证或需人工确认的数据。} {来源标签或需人工确认}

## F3 生存能力与龙头（{X}/20）
- 支撑证据：{营收、利润、现金、负债、审计或行业地位证据。} {来源标签}
- 扣分与缺口：{反证或需人工确认的数据。} {来源标签或需人工确认}

## F4 利润兑现路径（{X}/15）
- 支撑证据：{主营、订单、产能、收入、利润或公告兑现证据。} {来源标签}
- 扣分与缺口：{反证或需人工确认的数据。} {来源标签或需人工确认}

## F5 低位与困境反转（{X}/20）
- 支撑证据：{PE/PB、价格位置、同行估值、业绩拐点或技术结构证据。} {来源标签}
- 扣分与缺口：{反证或需人工确认的数据。} {来源标签或需人工确认}

## 修正项
- Alpha/技术结构：{本次 TDX 评分、趋势和信号。} [TDX]
- 情绪位置：{本次未采集则写“需人工确认，不计分”。} {来源标签或需人工确认}
- 风口催化：{公告中可验证的催化；没有则写“无已验证催化”。} {来源标签}
- 计分说明：{只有本次 scoring 报告明确计入的修正才进入总分；其余仅作参考。}

## Hard Cap 检查
| 条件 | 本次结果 | 对评级的影响 |
|---|---|---|
| 控股股东或实控人减持 | {未触发/已触发/需人工确认} | {无/最高学习仓/需人工确认} |
| ST 或退市风险 | {未触发/已触发/需人工确认} | {无/不碰/需人工确认} |
| F1 < 15 或 F3 < 8 | {未触发/已触发} | {无/最高学习仓} |

## 睡得着检查
- 不融资也能拿：{通过/不通过/需人工确认}。{依据} {来源标签}
- 不靠单一公告续命：{通过/不通过/需人工确认}。{依据} {来源标签}
- 财务不容易暴雷：{通过/不通过/需人工确认}。{依据} {来源标签}
- 股东不持续伤害小股东：{通过/不通过/需人工确认}。{依据} {来源标签}
- 产业逻辑至少 1-3 年不证伪：{通过/不通过/需人工确认}。{依据} {来源标签}
- 跌 20% 后仍能持有：{通过/不通过/需人工确认}。{依据} {来源标签}

## 动态纠错触发器
- 产业证伪：{可核验条件。}
- 公司证伪：{可核验条件。}
- 估值过热：{可核验条件。}
- 股东恶化：{可核验条件。}
- 同链高切低：{重新比较的条件。}

## 数据覆盖与待确认
- 已完成模块：{仅列本次 pipeline 状态为 ok 的模块。}
- 失败或缺失模块：{模块及影响；没有则写无。}
- 需人工确认：{海外收入、浏览器证据或其他缺失项；没有则写无。}

## 最终结论
{再次给出评级、主要依据和最重要风险，不新增前文没有的事实。}

免责声明：本分析仅供研究参考，不构成投资建议。
```

## Limits

- Browser UI, browser-based sentiment, login state, and the industry-chain database are intentionally excluded.
- Overseas revenue and browser-only evidence remain `需人工确认`.
- AxData is an optional enrichment adapter, disabled by default. Set `MODA_AXDATA=1` only for explicit validation; prefer bundled easy_tdx adapters for overlapping TDX/Sina capabilities.
- Optional proxy credentials are read only from environment variables. Do not print, store, or add them to files.
