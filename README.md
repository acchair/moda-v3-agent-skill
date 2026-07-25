# moda-v3 Agent Skill

用于 Agent 的 A 股五层分析 Skill。它包含核心采集、技术因子、评分规则和来源标注；不包含网页工作台、浏览器二进制、登录状态、Cookie、历史报告或个人配置。

## 安装

将本仓库目录放入 Agent 的 skills 目录，并保持目录名为 `moda-v3`。然后在该目录安装依赖：

```powershell
python -m pip install -r requirements.txt
```

## 使用

Agent 读取根目录 `SKILL.md` 后，可运行：

```powershell
python tools/run_pipeline.py --stock 000001 --name 平安银行
```

报告写入 `knowledge/research/`，最终答复可用 `tools/export_skill_output.py` 写入 `knowledge/output/`。设置 `MODA_OUTPUT_DIR` 可指定其他输出目录。

## 隐私与限制

不提交 `.env`、Token、Cookie、浏览器用户目录、本机日志和运行产物。可选代理仅从环境变量读取。浏览器增强和产业链网页工作台未随此核心包发布；相关结论应标记为 `需人工确认`。
