# 每日 RSS 自动更新设计

- 日期：2026-07-11
- 状态：设计已获用户口头批准，等待书面规范复核
- 适用仓库：`Hsuanqi77/academic-rss-site`
- 选择：方案 A — GitHub Actions 每日直接提交 `main`

## 1. 目标

每天北京时间 08:00 由 GitHub Actions 在云端检查全部 20 个已启用期刊 RSS，在现有数据库基础上增量更新，通过安全校验后提交新的 `docs/data/papers.db`，并刷新 GitHub Pages。

自动任务不依赖 Codex、本地电脑或浏览器保持在线。用户关闭 Codex、关机或断开家庭网络后，GitHub 仍可独立执行。

## 2. 非目标

- 不改变 RSS 解析、去重、分类、Crossref 或 Unpaywall 的现有业务规则。
- 不增加网页抓取，不绕过出版商登录、订阅或访问控制。
- 不创建个人访问令牌（PAT），不在仓库中保存邮箱或其他私人凭据。
- 不在无数据变化时创建空提交。
- 不把完整测试套件放进每日任务；完整测试继续用于开发和发布验收。
- 不改变当前 GitHub Pages 的 `main` + `/docs` 发布来源。

## 3. 运行时间与触发方式

工作流包含两种触发方式：

```yaml
on:
  schedule:
    - cron: "0 0 * * *"
  workflow_dispatch:
```

GitHub cron 使用 UTC，因此 `00:00 UTC` 对应北京时间 `08:00`。GitHub 对定时任务不保证精确到分钟，平台繁忙时允许延迟。

工作流只在默认分支的最新版本上运行。`workflow_dispatch` 允许用户在 GitHub Actions 页面手动测试或补跑，无需本地环境。

## 4. 工作流架构

新增单一工作流：

```text
.github/workflows/daily-rss-update.yml
```

工作流使用 GitHub 托管的 Ubuntu runner、Python 3.12、仓库现有 CLI 和 SQLite 发布机制。实现时，所有第三方 Actions 固定到完整 commit SHA，不使用浮动的 `@main` 或不受控标签。

工作流级安全与并发设置：

```yaml
permissions:
  contents: write
  pages: write

concurrency:
  group: daily-rss-update
  cancel-in-progress: false
```

- `contents: write` 仅用于提交更新后的公开数据库。
- `pages: write` 仅用于显式请求 GitHub Pages 构建。
- 同一时间最多执行一个 RSS 更新；新任务排队而不是取消正在写数据库的任务。
- 作业设置合理超时，避免外部服务长时间不可用时无限占用 runner。

## 5. 增量数据库流程

GitHub runner 每次都是临时干净环境，而本地工作数据库 `data/papers.db` 被 Git 忽略。因此工作流必须先从当前公开快照恢复工作数据库：

```text
docs/data/papers.db → data/papers.db
```

完整数据流：

```text
checkout main
  → 配置 Python
  → 安装项目运行依赖
  → 从 docs/data/papers.db 恢复 data/papers.db
  → paper-radar update
  → 现有 schema/外键/数量/运行状态安全闸门
  → 原子更新 docs/data/papers.db
  → 如有差异则提交并推送 main
  → 显式请求 GitHub Pages 构建
```

从已发布快照恢复工作数据库确保历史文章、ETag、Last-Modified、首次发现时间和更新日志被继承，不会每天从空数据库重建，也不会仅保留 RSS 当前窗口中的文章。

## 6. 更新、提交与并发处理

### 6.1 有数据变化

工作流只暂存：

```text
docs/data/papers.db
```

提交身份使用 GitHub Actions 机器人，提交消息固定为：

```text
chore(data): daily RSS update
```

提交前后都检查仓库状态；若出现数据库以外的 tracked 修改，工作流立即失败，不扩大自动提交范围。

推送前同步远程 `main`。如果用户在任务运行期间修改了同一数据库并导致 rebase 冲突，自动任务安全失败，不强制推送、不覆盖用户提交。

### 6.2 没有数据变化

不创建提交，也不推送空提交。工作流仍请求一次 Pages 构建，使此前“数据库已推送但 Pages 请求暂时失败”的情况能在下一次运行自动恢复。

## 7. GitHub Pages 刷新

GitHub 官方说明：由工作流使用仓库 `GITHUB_TOKEN` 推送的提交不会再次触发普通 push 工作流，也不会自动触发 GitHub Pages 构建。这能防止递归运行，但意味着数据库提交后必须显式刷新 Pages。

因此成功完成数据库更新阶段后，工作流调用：

```text
POST /repos/Hsuanqi77/academic-rss-site/pages/builds
```

该请求使用本次作业自动获得的 `GITHUB_TOKEN` 和最小 `pages: write` 权限，不需要 PAT。若 Pages 请求失败，工作流标记失败，但已提交的安全数据库不会回滚；下一次计划任务或手动补跑会再次请求构建。

## 8. 外部服务与 Secret

RSS 和 Crossref 不需要私人 Secret。

Unpaywall 邮箱仍为可选项：

```yaml
env:
  UNPAYWALL_EMAIL: ${{ secrets.UNPAYWALL_EMAIL }}
```

未配置该 Secret 时，其值为空，RSS 抓取、Crossref 补全和网站更新继续运行；OA 状态无法确认时保持 `unknown`。

如果用户以后需要 Unpaywall，可在仓库 **Settings → Secrets and variables → Actions** 中添加 `UNPAYWALL_EMAIL`。邮箱不会写入日志、数据库、工作流文件或提交历史。

## 9. 失败处理

| 情况 | 行为 |
| --- | --- |
| 所有 feed 均失败 | CLI 返回错误；不提交、不推送、不覆盖线上数据库 |
| 部分 feed 失败 | 沿用当前 `partial` 规则；成功来源增量写入，失败来源保留旧数据 |
| 数据库校验失败 | 不提交；现有 `docs/data/papers.db` 保持不变 |
| 没有数据变化 | 不提交；仍可请求 Pages 构建 |
| 并发远程提交冲突 | rebase/推送失败；不强推、不覆盖远程 |
| Git 推送失败 | 作业失败；本地 runner 随后销毁，远程保持不变 |
| Pages 请求失败 | 数据提交保留；任务失败并在下次运行重试 Pages 请求 |
| GitHub 定时任务被停用 | 用户通过 Actions 页面重新启用并手动运行 |

公开仓库连续 60 天没有仓库活动时，GitHub 可能自动停用定时工作流。由于本设计不创建空提交，长期没有任何数据变化时仍可能出现该情况；保留 `workflow_dispatch` 便于重新启用和补跑。

## 10. 可观测性

每次运行在 GitHub Actions 中保留：

- 触发类型和开始时间；
- CLI 的 JSON 更新摘要；
- 新增、更新、跳过和失败数量；
- 成功与失败 feed；
- 是否通过发布闸门；
- 是否产生数据库提交；
- Pages build API 的响应。

工作流把简短结果写入 GitHub Actions Job Summary，方便不查看完整日志也能判断本轮状态。日志不得输出 Secret。

## 11. 测试与验收

### 11.1 仓库测试

新增自动化合同测试，验证：

1. 工作流同时包含 `schedule` 和 `workflow_dispatch`。
2. cron 精确为 `0 0 * * *`。
3. 权限仅包含必需的 `contents: write` 与 `pages: write`。
4. 工作流具有串行 concurrency 和超时。
5. 更新前从 `docs/data/papers.db` 恢复 `data/papers.db`。
6. 使用仓库 CLI 执行 `paper-radar update`。
7. 自动提交范围仅为 `docs/data/papers.db`。
8. 无变化时不创建提交。
9. 不使用 force push。
10. 成功更新后显式请求 Pages build API。
11. 不包含 PAT、硬编码邮箱或其他 Secret 值。

现有 Python、Playwright、Node 和 Ruff 测试必须继续通过。

### 11.2 首次上线验收

1. 提交并推送工作流到 `main`。
2. 从 GitHub Actions 手动触发一次 `workflow_dispatch`。
3. 确认作业成功或在无数据变化时安全结束。
4. 如果数据库变化，确认机器人提交只包含 `docs/data/papers.db`。
5. 确认 Pages build 对应最新 `main`。
6. 确认生产网站可加载、文章数量合理、字体资源和筛选功能不回归。
7. 确认下一次计划运行显示为北京时间每日 08:00 对应的 UTC cron。

## 12. 回滚与停用

- 临时停用：GitHub **Actions → Daily RSS Update → Disable workflow**。
- 永久移除：删除工作流文件并提交。
- 数据回滚：恢复已验证的旧 `docs/data/papers.db` 提交；不修改抓取代码。
- 自动任务失败不会删除当前 GitHub Pages 网站或已发布数据库。

## 13. 官方依据

- GitHub Actions 定时触发：<https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows>
- `GITHUB_TOKEN` 行为：<https://docs.github.com/en/actions/concepts/security/github_token>
- 工作流权限：<https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax>
- GitHub Pages Build API：<https://docs.github.com/en/rest/pages/pages#request-a-github-pages-build>

