---
name: moda-release-updater
description: 自动检查、升级和管理 moda-v4 的 GitHub Release。Agent 会话启动时由 SessionStart Hook 每天后台检查一次；用户要求检查新版、立即升级、跳过某个版本、恢复被跳过版本或排查更新提示时使用。
---

# Moda Release Updater

使用 `scripts/release_updater.py` 管理 `acchair/moda-v4-agent-skill` 的正式 Release。

## 固定规则

- 会话启动检查必须在独立后台进程中运行，不阻塞当前 Agent。
- 每个本地自然日最多访问一次 GitHub Release API。
- 仅处理 GitHub `latest release`，不处理草稿和预发布版本。
- 发现新版时显示 Release 标签、发布日期和正文摘要，并提供三个按钮：`是`、`跳过本版`、`否`。
- `是`：立即升级。Git 仓库存在未提交修改时停止升级，不覆盖用户文件。
- `跳过本版`：记录该 Release 标签，以后不再提示该版本。
- `否`：本次不升级；下一自然日检查到同一版本时可再次提示。
- 不输出或保存密码、Token、Cookie、密钥和其他敏感信息。

## 常用命令

```powershell
python scripts/release_updater.py status
python scripts/release_updater.py check-now --target "C:\path\to\moda-v4"
python scripts/release_updater.py unskip --tag "v1.2.3"
python scripts/release_updater.py install --target "C:\path\to\moda-v4"
```

`install` 将本 Skill 安装到用户级 Skills 目录，并合并写入用户级 `SessionStart` Hook，不删除其他 Hook。
