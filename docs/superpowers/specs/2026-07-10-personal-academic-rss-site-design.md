# 个人学术 RSS 聚合网站设计

- 日期：2026-07-10
- 状态：用户已于 2026-07-10 复核并批准
- 参考项目：`hehuifeng/Nature_task_generate` 与 `hehuifeng/rss_site`

## 1. 目标

建立一个供个人长期使用、公开部署在 GitHub Pages 上的学术论文聚合网站。系统聚合 Nature、AIP、IEEE、Wiley 等出版社的官方 RSS，将不同格式统一写入 SQLite，并通过纯静态网页完成检索和筛选。

首个版本采用手动更新，不依赖 GitHub Actions，也不使用 AI 翻译。手动流程稳定后，再增加每日定时更新。

成功标准：

1. 一个本地 Git 仓库同时包含数据采集程序、配置、测试和 GitHub Pages 静态网站。
2. 用户可通过配置文件添加新期刊，不需要修改前端代码。
3. 支持关键词、日期、期刊、出版社、文章类型、开放获取状态和主题标签的组合筛选。
4. 单个 RSS 或元数据服务失败时，其他来源继续更新，已发布数据库不被破坏。
5. 网站可在桌面和手机浏览器中使用，并可通过公开 GitHub Pages URL 访问。

## 2. 已确认的产品选择

| 项目 | 选择 |
| --- | --- |
| 初期运行方式 | 手动运行，第二阶段再添加 GitHub Actions |
| 翻译 | 第一版不翻译 |
| 部署可见性 | 公开 GitHub Pages |
| 数据格式 | SQLite，由浏览器中的 `sql.js` 读取 |
| 仓库结构 | 单仓库 |
| 桌面布局 | 左侧固定筛选栏，右侧文章列表 |
| 手机布局 | 筛选栏折叠为抽屉 |
| 筛选范围 | 关键词、日期、期刊、出版社、文章类型、OA 状态、主题标签 |
| 收藏与账号 | 第一版不做 |

## 3. 范围

### 3.1 第一版包含

- 官方 RSS 的增量抓取。
- RSS 1.0、RSS 2.0 和 Atom 解析。
- DOI、URL 和稳定哈希三级去重。
- Crossref 元数据补全。
- 可选的 Unpaywall 开放获取状态补全。
- 规则驱动的主题标签。
- SQLite 数据库、更新日志和发布前校验。
- 纯静态响应式网站。
- 手动抓取、测试、本地预览、提交和 GitHub Pages 发布流程。

### 3.2 第一版不包含

- AI 翻译、AI 摘要或 AI 分类。
- 用户账号、云端收藏或跨设备同步。
- 付费全文下载、机构登录自动化或绕过访问权限。
- 通用网页爬虫。出版社文章页不是成功更新的必要依赖。
- 历史全量回溯。初期只保存官方 RSS 当前提供的条目，后续增量积累。

## 4. 初始期刊与官方 RSS

RSS 地址在 2026-07-10 核对。`feeds.yml` 将保存规范期刊名、出版社、RSS 地址、启用状态和可选别名。

### 4.1 Nature Portfolio

| 期刊 | RSS |
| --- | --- |
| Nature Communications | `https://www.nature.com/ncomms.rss` |
| Nature Biotechnology | `https://www.nature.com/nbt.rss` |
| Nature Methods | `https://www.nature.com/nmeth.rss` |
| Nature | `https://www.nature.com/nature.rss` |
| Nature Cancer | `https://www.nature.com/natcancer.rss` |
| Nature Machine Intelligence | `https://www.nature.com/natmachintell.rss` |
| Nature Computational Science | `https://www.nature.com/natcomputsci.rss` |
| Nature Reviews Molecular Cell Biology | `https://www.nature.com/nrm.rss` |
| Nature Reviews Genetics | `https://www.nature.com/nrg.rss` |
| Nature Reviews Cancer | `https://www.nature.com/nrc.rss` |
| Microsystems & Nanoengineering | `https://www.nature.com/micronano.rss` |

### 4.2 AIP Publishing

| 期刊 | RSS |
| --- | --- |
| Applied Physics Letters | `https://pubs.aip.org/rss/site_1000017/1000011.xml` |

### 4.3 IEEE Xplore

| 期刊 | IEEE publication number | RSS |
| --- | ---: | --- |
| IEEE Transactions on Ultrasonics | 11073821 | `https://ieeexplore.ieee.org/rss/TOC11073821.XML` |
| IEEE Transactions on Microwave Theory and Techniques | 22 | `https://ieeexplore.ieee.org/rss/TOC22.XML` |
| IEEE Microwave and Wireless Technology Letters | 9944983 | `https://ieeexplore.ieee.org/rss/TOC9944983.XML` |
| IEEE Transactions on Electron Devices | 16 | `https://ieeexplore.ieee.org/rss/TOC16.XML` |
| IEEE Electron Device Letters | 55 | `https://ieeexplore.ieee.org/rss/TOC55.XML` |
| Journal of Microelectromechanical Systems | 84 | `https://ieeexplore.ieee.org/rss/TOC84.XML` |

### 4.4 Wiley Online Library

| 期刊 | RSS |
| --- | --- |
| Advanced Materials | `https://advanced.onlinelibrary.wiley.com/action/showFeed?type=etoc&feed=rss&jc=15214095` |
| Advanced Functional Materials | `https://advanced.onlinelibrary.wiley.com/action/showFeed?type=etoc&feed=rss&jc=16163028` |

## 5. 仓库结构

```text
academic-rss-site/
├─ feeds.yml
├─ topics.yml
├─ pyproject.toml
├─ README.md
├─ .env.example
├─ .gitignore
├─ scripts/
│  ├─ fetch.py
│  ├─ publish.py
│  └─ validate.py
├─ src/paper_radar/
│  ├─ config.py
│  ├─ feeds.py
│  ├─ normalize.py
│  ├─ enrich.py
│  ├─ classify.py
│  ├─ database.py
│  └─ report.py
├─ tests/
│  ├─ fixtures/
│  ├─ test_feeds.py
│  ├─ test_normalize.py
│  ├─ test_dedup.py
│  ├─ test_database.py
│  └─ test_publish.py
├─ data/
│  └─ papers.db
└─ docs/
   ├─ index.html
   ├─ app.js
   ├─ styles.css
   ├─ sql-wasm.js
   ├─ sql-wasm.wasm
   └─ data/papers.db
```

`data/papers.db` 是工作数据库。只有在测试与发布校验通过后，`publish.py` 才以安全替换方式更新 `docs/data/papers.db`。

## 6. 架构和数据流

```text
feeds.yml
   ↓
官方 RSS（Nature / AIP / IEEE / Wiley）
   ↓
解析与字段标准化
   ↓
DOI / Crossref 补全 ──→ Unpaywall OA 补全（可选）
   ↓
去重与关键词标签
   ↓
data/papers.db
   ↓ 测试与发布闸门
docs/data/papers.db
   ↓
sql.js + 静态前端
   ↓
GitHub Pages
```

RSS 是主数据源。Crossref 和 Unpaywall 只补全缺失字段；它们不可用时，已有 RSS 数据仍可入库。第一版不要求抓取出版商文章详情页，以降低 IEEE、Wiley 等站点限流或反自动化策略带来的脆弱性。

## 7. 组件边界

### 7.1 配置加载

`config.py` 负责解析并校验 `feeds.yml` 与 `topics.yml`。无效 URL、重复期刊 ID、重复标签 ID 或缺少必填字段时，在网络请求前终止。

### 7.2 RSS 解析

`feeds.py` 只负责 HTTP 条件请求和 RSS/Atom 解析，输出统一的原始条目对象。支持 ETag 与 Last-Modified，减少重复下载。

### 7.3 字段标准化

`normalize.py` 将出版社差异转换为统一字段，包括标题、作者、摘要、发布日期、DOI、文章类型、期刊名和文章 URL。文章类型归并为 `research`、`review`、`editorial`、`correction` 和 `other`。

### 7.4 元数据补全

`enrich.py` 按 DOI 查询 Crossref，并在配置了 `UNPAYWALL_EMAIL` 环境变量时查询 Unpaywall。OA 状态使用三值：`open`、`closed`、`unknown`。网络失败不会删除或拒绝 RSS 条目。

### 7.5 主题标签

`classify.py` 读取 `topics.yml`，在标题和摘要中进行不区分大小写的关键词和短语匹配。初始标签包括 BAW、SAW、FBAR、MEMS、AlN、AlScN、piezoelectric、ultrasound、acoustic resonator、microwave、RF、ferroelectric、semiconductor 和 electron device。用户可直接编辑配置。

### 7.6 数据库与发布

`database.py` 管理迁移、事务、增量写入和查询索引。`validate.py` 检查数据库结构、记录数量、日期范围和引用完整性。`publish.py` 仅在所有发布校验通过后更新 Pages 使用的数据库。

### 7.7 前端

`docs/` 是不需要构建步骤的纯静态站点。`app.js` 通过本地 vendored `sql.js` 加载数据库，构造参数化查询，渲染文章卡片和分页。前端不包含秘密或写入能力。

## 8. 数据模型

### 8.1 `journals`

- `id`：稳定的短 ID，主键。
- `name`：规范期刊名。
- `publisher`：`nature`、`aip`、`ieee` 或 `wiley`。
- `feed_url`：官方 RSS 地址，唯一。
- `enabled`：是否启用。
- `etag`、`last_modified`：条件请求状态。
- `last_checked_at`、`last_success_at`、`last_status`、`last_error`：运行状态。

### 8.2 `articles`

- `uid`：稳定主键。
- `doi`：标准化 DOI，可空并建立唯一索引。
- `journal_id`：关联 `journals`。
- `title`、`abstract`、`authors_json`。
- `published_at`、`article_type`。
- `article_url`、`normalized_url`。
- `oa_status`：`open`、`closed` 或 `unknown`。
- `source_feed_url`。
- `first_seen_at`、`last_updated_at`。
- `metadata_status`：`rss_only`、`enriched` 或 `partial`。

### 8.3 `tags` 与 `article_tags`

标签单独建表，并通过连接表表达一篇文章对应多个标签。这样可稳定支持标签组合查询，不依赖 SQLite JSON 扩展。

### 8.4 `runs_log`

记录每次运行的开始、结束、状态、新增数、更新数、跳过数、失败数和错误摘要。

## 9. 去重规则

按以下顺序生成 UID：

1. 标准化 DOI：去掉 `doi:` 与 `https://doi.org/` 前缀，转为小写。
2. 没有 DOI 时使用规范化文章 URL，移除常见跟踪参数和多余尾斜杠。
3. 仍缺失时使用 `journal_id + normalized_title + published_date` 的 SHA-256 稳定哈希。

后续抓取获得更完整元数据时执行 upsert，不创建重复文章。

## 10. 前端体验

### 10.1 桌面端

- 顶部导航：网站名称、最新论文、数据状态、关于。
- 左侧固定筛选栏：日期、期刊、出版社、文章类型、OA 状态和标签。
- 右侧顶部：全文搜索框、结果数量、排序和清除筛选。
- 右侧主体：文章卡片与分页。

### 10.2 手机端

- 文章列表保持单列。
- 筛选栏收起为“筛选”按钮打开的抽屉。
- 已启用筛选数量显示在按钮徽标中。

### 10.3 文章卡片

显示标题、作者、期刊、出版社、发布日期、文章类型、OA 状态、主题标签和摘要节选。点击标题在新标签页打开出版社原文。缺失字段不显示空占位。

### 10.4 URL 状态

搜索词、日期范围、期刊、出版社、文章类型、OA 状态、标签、排序和页码写入 URL 查询参数。刷新与分享链接后可恢复相同筛选结果。

## 11. 错误处理与发布安全

1. HTTP 请求设置超时、每域名限速、最多三次重试和指数退避。
2. 每个 RSS 独立处理。一个来源失败时记录错误并继续其他来源。
3. 失败来源保留上次成功数据，不清空旧记录。
4. Crossref 或 Unpaywall 失败时保留 RSS 数据，并标记 `metadata_status`。
5. 解析失败的单条文章记录到错误摘要，不将整个 feed 回滚。
6. 空数据库、结构错误、外键错误、异常的大幅记录下降或所有来源都失败时，禁止覆盖 `docs/data/papers.db`。
7. 工作数据库先备份再迁移；发布数据库通过临时文件校验后原子替换。

## 12. 安全与隐私

- `.env`、本地虚拟环境、临时数据库、备份和日志不提交。
- 仓库只提交 `.env.example`，不包含真实邮箱、令牌或 API Key。
- Unpaywall 联系邮箱通过 `UNPAYWALL_EMAIL` 环境变量提供。
- 前端只读取公开论文元数据，没有服务端密钥。
- 网站只链接合法出版社页面，不提供绕过付费墙或访问控制的功能。

## 13. 测试策略

### 13.1 单元测试

- RSS 1.0、RSS 2.0 和 Atom 固定样本。
- 四类出版社的日期、DOI、作者、类型和 URL 标准化。
- DOI、URL 与标题哈希三级去重。
- 标签大小写、短语和多标签匹配。
- OA 三值状态与文章类型归并。

### 13.2 集成测试

- 在临时 SQLite 中执行首次导入、重复导入、字段补全和迁移。
- 模拟一个 feed 超时，验证其他 feed 继续提交。
- 模拟 Crossref/Unpaywall 失败，验证 RSS 数据仍入库。
- 模拟空库和损坏库，验证发布闸门拒绝覆盖。

### 13.3 前端测试

- 数据库加载与加载失败提示。
- 关键词和每一种筛选条件。
- 多条件组合、清除筛选、分页和排序。
- URL 状态恢复。
- 桌面侧栏与手机筛选抽屉。

## 14. 手动更新的用户体验

最终提供一个 Windows 友好的单入口命令，依次执行抓取、校验和发布。运行结束输出：

- 成功与失败期刊列表。
- 新增、更新、跳过和异常数量。
- 最早与最新文章日期。
- 工作数据库和发布数据库大小。
- 是否允许提交发布。

用户确认本地网站正常后，再执行 Git 提交与推送。第二阶段的 GitHub Actions 复用同一个入口，不另写一套抓取逻辑。

## 15. 验收标准

1. 所有初始 RSS 均在配置文件中，并通过配置校验。
2. 至少用 Nature、AIP、IEEE 和 Wiley 各一个真实 feed 完成端到端导入。
3. 重复运行不会产生重复文章。
4. 人为让一个 feed 失败时，其余来源仍成功更新。
5. 本地网站能加载数据库并完成七类筛选。
6. 筛选状态可由 URL 恢复。
7. 手机宽度下筛选抽屉可用、卡片无横向溢出。
8. 发布闸门能阻止空库或损坏库覆盖有效数据库。
9. Git 历史和静态站点中不存在秘密或本地 `.env`。
10. GitHub Pages 从 `main/docs` 成功公开部署。

## 16. 分阶段实施

1. 建立仓库骨架、配置模型和测试框架。
2. 实现 RSS 解析、标准化和 SQLite 数据层。
3. 加入四类出版社配置、元数据补全和主题标签。
4. 实现发布校验和 Windows 手动运行入口。
5. 实现侧栏式响应前端和组合筛选。
6. 端到端验证并发布 GitHub Pages。
7. 稳定运行后添加定时 GitHub Actions。

## 17. 设计决策摘要

- 选择单仓库，降低个人维护和数据库同步成本。
- 保留 SQLite 与 `sql.js`，满足数据增长后的复杂筛选。
- RSS 优先，元数据服务补全，出版商文章页不作为硬依赖。
- 采用侧边筛选栏，因为增强筛选项较多。
- 手动运行先行，自动化后置，避免同时调试采集、部署和定时任务。
- 所有新增期刊通过配置扩展；只有出现无法归一化的新 RSS 格式时才增加解析适配代码。
