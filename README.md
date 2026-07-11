# Paper Radar

Paper Radar 是一个供个人使用的多出版社学术 RSS 聚合网站。Python 程序从期刊官方公开 RSS 获取论文元数据，写入 SQLite；浏览器通过本地 `sql.js` / WebAssembly 直接查询经过校验的数据库，因此不需要服务器端程序。静态页面可以部署到 GitHub Pages。

当前配置收录 20 本期刊：Nature Portfolio 11 本、Applied Physics Letters、IEEE 6 本，以及 Wiley 的 Advanced Materials 和 Advanced Functional Materials。完整名称和官方 RSS 地址以 [`feeds.yml`](feeds.yml) 为准。

> 本仓库只准备本地发布候选。尚未创建或推送 GitHub 仓库，也没有可填写的生产网站 URL。

## 1. Windows 首次安装

运行时需要 Python 3.11 或更高版本。可先用 `py -0p` 查看 Windows Python Launcher 已发现的解释器；下面的 `py -3` 会选择已安装的 Python 3，请再用虚拟环境中的 `python --version` 确认版本不低于 3.11。打开 PowerShell，在项目目录运行：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

如需查询开放获取状态，在 `.env` 中填写自己的联系邮箱：

```dotenv
UNPAYWALL_EMAIL=your-name@example.com
```

不填写时仍会采集 RSS，无法确认的 OA 状态显示为 `unknown`。`.env` 已被 Git 忽略，不要提交邮箱或其他私人配置。

## 2. 手动更新与安全发布

```powershell
.\scripts\update.ps1
```

命令依次执行：读取配置 → 抓取官方 RSS → 规范化、去重和打标签 → 写入本地 `data/papers.db` → 完整性校验 → 原子更新 `docs/data/papers.db`。只有 schema、外键、运行状态和文章数量安全闸门全部通过时，公开快照才会被替换。

终端最后输出一行 JSON。`command`、`publish_allowed`、`validation` 和 `publication` 位于顶层，抓取统计位于顶层 `result` 对象内：

- `result.status` 为 `ok`：所有启用来源均成功；
- `result.status` 为 `partial`：部分来源失败，但其余来源成功，失败来源保留旧数据；校验通过时允许发布；
- `result.status` 为 `error`：没有可安全发布的结果，命令退出且不会覆盖旧快照；
- `result.inserted`、`result.updated`、`result.skipped`、`result.failed`：新增、更新、无变化和失败条数；
- `result.successful_feeds`、`result.failed_feeds`：本轮来源结果；
- `publish_allowed`：顶层安全发布判定；只有 `true` 才表示本轮已通过发布闸门。

建议连续运行两轮。第一轮确认四类出版社至少各有一个来源成功；第二轮应以 `skipped` 或 HTTP 未修改为主，并确认没有重复 DOI。

## 3. 本地预览

```powershell
.\.venv\Scripts\python.exe -m http.server 8000 --directory docs
```

浏览器打开 `http://localhost:8000`。不要直接双击 `index.html`，因为 `file://` 页面不能可靠加载 SQLite 和 WASM。页面“数据状态”显示当前发布快照的可检索文章数量；数据时间取决于最近一次手动更新，不是实时全文数据库。

## 4. 运行全部测试

```powershell
.\.venv\Scripts\python.exe -m pytest -v
npm run test:web
.\.venv\Scripts\python.exe -m ruff check .
```

Web 测试不需要安装 npm 包，但需要 Node.js。若电脑只有 `node` 而没有 `npm`，可运行：

```powershell
node --test tests/web/*.test.mjs
```

浏览器测试优先使用已安装的 Microsoft Edge，再尝试 Playwright Chromium。正常验收不应跳过浏览器；只有明确设置 `PAPER_RADAR_ALLOW_BROWSER_SKIP=1` 时才允许跳过。

## 5. 添加或修改期刊

编辑 `feeds.yml` 的 `feeds` 列表，每项包含：

- 唯一的 `id`；
- 显示名称 `name`；
- 支持的 `publisher`：`nature`、`aip`、`ieee` 或 `wiley`；
- 出版商官方公开的 HTTPS `feed_url`；
- 可选的 `enabled` 与历史 `aliases`。

只使用出版商公开 RSS，不用网页抓取代替 RSS，也不绕过登录、订阅或机构访问控制。修改后先运行测试，再执行两轮 `update.ps1` 验收。

## 6. 添加主题标签

编辑 `topics.yml`，新增唯一 `id`、显示名 `label` 和至少一个 `keywords`。关键词在标题和摘要中进行不区分 ASCII 大小写的匹配。再次更新数据库后，标签自动出现在前端筛选栏。

## 7. 两轮本地验收清单

第一轮：

1. JSON 状态为 `ok` 或 `partial`；
2. Nature、AIP、IEEE、Wiley 至少各有一个成功来源；
3. `docs/data/papers.db` 通过 schema v3 和完整性检查；
4. 本地页面无控制台错误，桌面筛选与 390 px 手机抽屉可用。

第二轮：

1. 未修改来源或 `skipped` 条目占主要部分；
2. DOI、文章 UID 和规范化 URL 没有重复；
3. 刷新页面后 URL 中的搜索、期刊、日期、类型、OA 和标签状态可恢复；
4. 再次运行全部 Python、Node、Ruff 和浏览器测试。

## 8. 手动发布到 GitHub Pages

本地验收完成后，由仓库所有者自行执行外部发布：

1. 创建 GitHub 仓库并提交代码及 `docs/data/papers.db`；
2. 推送到 `main`；
3. GitHub 仓库 **Settings → Pages**；
4. **Deploy from a branch**，选择 `main` 和 `/docs`；
5. 等待 GitHub 显示实际 Pages URL，再重复桌面和手机验收。

不要在确认账户、仓库名称和公开可见性之前自动创建仓库、推送或启用 Pages。本 README 不填写尚不存在的 URL。

## 9. 隐私、版权与访问边界

本站仅保存并展示公开 RSS、Crossref 和可选 Unpaywall 提供的论文元数据，并链接到出版商原文。文章版权归作者和出版商所有。本项目不下载付费全文、不复制受版权保护的正文，也不规避登录、付费墙、验证码或机构授权。公开部署前请检查数据库中没有私人笔记；`.env`、工作数据库、WAL/SHM、备份和测试报告均不应进入 Git。

## 10. 常见故障

- **提示虚拟环境不存在**：重新执行第 1 节命令，确认使用项目内 `.venv`。
- **RSS 返回 403、404、超时或非 XML**：先核对 `failed_feeds`；在出版商官网确认公开 RSS 地址，不要改用网页抓取。
- **更新为 partial**：稍后重试；旧数据会保留。若长期失败，再检查官方 feed 是否迁移。
- **数据库拒绝发布**：不要删除旧快照；运行 `paper-radar validate` 查看 schema、完整性、空库或文章数量骤降原因。
- **页面一直加载或 WASM 404**：必须通过 HTTP 预览，并确认 `docs/sql-wasm.js`、`docs/sql-wasm.wasm` 和 `docs/data/papers.db` 都存在。
- **浏览器测试找不到浏览器**：安装 Edge，或运行 `.\.venv\Scripts\python.exe -m playwright install chromium`。
- **搜索百分号或下划线**：它们按普通字符处理，不是 SQL 通配符。

## 11. 目录结构

```text
feeds.yml                 官方 RSS 来源配置
topics.yml                主题标签规则
src/paper_radar/          抓取、标准化、数据库、校验与 CLI
scripts/update.ps1        Windows 手动更新入口
data/papers.db            本地工作数据库（不提交）
docs/                     GitHub Pages 静态网站
docs/data/papers.db       校验后、可提交的发布快照
tests/                    Python、真实浏览器与独立 E2E 测试
tests/web/                零构建 Node 测试
```

## Production site

- 网站：[Paper Radar](https://hsuanqi77.github.io/academic-rss-site/)
- 源代码：[Hsuanqi77/academic-rss-site](https://github.com/Hsuanqi77/academic-rss-site)

生产站点由 GitHub Pages 从 `main` 分支的 `/docs` 目录发布。首次上线验收已确认：764 篇论文可加载，筛选状态可通过 URL 恢复，390 px 移动筛选抽屉可用，WASM 与 SQLite 数据库资源均正常返回。
