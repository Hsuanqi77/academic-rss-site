# Noto Sans SC 字体系统改造设计

- 日期：2026-07-11
- 状态：设计说明已获用户口头批准，等待规范复核
- 适用网站：Academic Paper Radar
- 方案：A — 自托管 Noto Sans SC Variable

## 1. 背景与问题

当前网页使用两组主要字体变量：

```css
--ui: "Microsoft YaHei UI", "PingFang SC", "Noto Sans CJK SC", sans-serif;
--editorial: "Songti SC", SimSun, "Noto Serif CJK SC", Georgia, serif;
```

在 Windows 上，标题类文字通常会落到 `SimSun`。这会让文章标题中的中文和拉丁字符呈现较旧的宋体风格，与页面现代、克制的视觉语言不一致；不同操作系统还会选择不同的本机字体，导致版式和字重不稳定。

## 2. 目标

1. 所有中文界面、标题和正文统一使用现代、清晰的 Noto Sans SC。
2. 文章英文标题与混排标题也使用相同的无衬线体系，避免 `SimSun` 的拉丁字形。
3. 字体由本站静态资源提供，不依赖 Google Fonts、CDN 或第三方运行时请求。
4. 保持当前页面布局、颜色、内容和交互不变。
5. 字体加载失败时，页面仍可使用系统字体正常阅读和操作。

## 3. 非目标

- 不修改文章数据、RSS 抓取或数据库结构。
- 不改变页面的间距、颜色、卡片结构、响应式断点或交互逻辑。
- 不替换技术标记使用的等宽字体。
- 不替换品牌缩写 `PR` 使用的 Georgia。
- 本次不引入深色模式、字号设置器或用户自选字体功能。

## 4. 当前字体位置清单

| 位置 | 当前字体来源 | 改造后 |
| --- | --- | --- |
| 页面正文、导航、搜索框、筛选控件 | `--ui` | Noto Sans SC Variable |
| 筛选标签、状态文字、作者、摘要、元数据、分页 | `--ui` 或继承正文 | Noto Sans SC Variable |
| 品牌全称、筛选栏标题、页面主标题 | `--editorial` | Noto Sans SC Variable |
| 结果数量、文章标题、说明区标题 | `--editorial` | Noto Sans SC Variable |
| 眉题、筛选序号、年份、页脚等技术标记 | `ui-monospace, Consolas, monospace` | 保持不变 |
| 品牌缩写 `PR` | Georgia | 保持不变 |

## 5. 已批准的字体映射

两个语义变量统一指向同一个自托管字体族：

```css
--ui: "Noto Sans SC Variable", "Microsoft YaHei UI", "PingFang SC", sans-serif;
--editorial: "Noto Sans SC Variable", "Microsoft YaHei UI", "PingFang SC", sans-serif;
```

保留 `--ui` 和 `--editorial` 两个变量名称，避免扩大改动范围，也为未来重新区分正文与编辑性标题字体保留接口。

字重分工：

| 用途 | 字重 |
| --- | --- |
| 正文、作者、摘要、元数据 | 400 |
| 导航、控件、标签、按钮和强调性界面文字 | 500 |
| 页面标题、区块标题、文章标题、结果数字 | 700 |

Noto Sans SC Variable 的可变字重轴覆盖上述三个字重。现有采用 700 的标题保持视觉层级；实施时只在界面控件缺少明确层级的位置补充 500，不改变字号和行高。

## 6. 字体资源与加载架构

### 6.1 资源来源

- 使用 Fontsource 提供的 Noto Sans SC Variable WOFF2 文件和 `unicode-range` 声明。
- 实施时固定一个明确的 Fontsource 包版本，不使用 `latest` URL。
- 在仓库中记录包名、版本、原始来源和每个字体文件的 SHA-256。
- 随字体资源保留对应的 SIL Open Font License 文本。

### 6.2 仓库位置

```text
docs/
  fonts/
    noto-sans-sc/
      FONT-METADATA.md
      LICENSE.txt
      *.woff2
  styles.css
```

字体文件直接随 GitHub Pages 发布。`styles.css` 内加入本地 `@font-face` 声明，路径相对于 `docs/styles.css` 解析。

### 6.3 子集策略

- 只引入页面所需的拉丁字符和简体中文子集。
- 不提交西里尔文、越南文等无关子集。
- 使用 Fontsource 生成的 `unicode-range`，让浏览器仅下载当前页面实际出现字符所对应的 WOFF2 分片。
- 不把所有 CJK 字形合并成一个阻塞首屏的大文件。

### 6.4 加载行为

- 每个 `@font-face` 使用 `font-display: swap`。
- 首次渲染可先显示系统回退字体，字体可用后再切换。
- 不增加任何 `fonts.googleapis.com`、`fonts.gstatic.com`、jsDelivr 或 unpkg 请求。
- 第一版不预加载字体文件；先通过实际网络瀑布确认常用分片，再决定是否需要针对首屏增加精确 preload。

## 7. 回退与故障处理

字体加载失败时按以下顺序回退：

1. Windows：`Microsoft YaHei UI`
2. macOS/iOS：`PingFang SC`
3. 浏览器默认 `sans-serif`

回退只影响视觉一致性，不影响内容、筛选、搜索、数据库读取或页面导航。字体资源缺失应由自动测试和生产部署检查阻止进入正式发布。

## 8. 实施范围

预计修改或新增：

- `docs/styles.css`：加入本地 `@font-face`，更新字体变量和界面字重。
- `docs/fonts/noto-sans-sc/`：加入 WOFF2、许可和来源/校验信息。
- 静态页面测试：验证字体声明、资源存在、版本与哈希、无外部字体依赖。
- 浏览器测试：验证计算字体、关键页面区域和移动端布局。

原则上不需要修改 `docs/index.html`。只有在测量证明字体预加载能显著改善首屏且不会下载无用分片时，才另行增加 preload；该优化不属于本次基本验收条件。

## 9. 验收标准

### 9.1 静态与自动化检查

1. `--ui` 和 `--editorial` 的首选字体均为 `Noto Sans SC Variable`。
2. 所有声明引用的 WOFF2 文件真实存在并返回正确路径。
3. `@font-face` 包含可变字重范围、正确格式和 `font-display: swap`。
4. 字体包版本、许可证和 SHA-256 被记录；自动测试验证文件哈希。
5. `docs/` 中不存在远程字体或字体 CDN URL。
6. 现有 Python 与 Node 测试继续通过，Ruff 检查无回归。

### 9.2 浏览器检查

1. Windows Edge/Chrome 中，正文、中文标题和文章标题的计算字体族以 `Noto Sans SC Variable` 开头。
2. 品牌缩写 `PR` 继续使用 Georgia；技术标记继续使用等宽字体。
3. 中英混排文章标题不再显示 SimSun 拉丁字形。
4. 390 px 宽移动视图无新增横向滚动、文字遮挡或控件溢出。
5. 字体下载失败的模拟场景下，页面使用系统回退字体且功能完整。

### 9.3 发布检查

1. 推送后 GitHub Pages 构建成功。
2. 正式站点上的 CSS、许可证和每个被引用的 WOFF2 均返回 HTTP 200。
3. 正式站点不向第三方字体服务发送请求。
4. 用桌面与移动视口各完成一次视觉复核。

## 10. 发布与回滚

发布沿用当前 `main` 分支与 GitHub Pages 流程。字体改造独立成提交，便于审查和回滚。

若发现不可接受的加载体积、字形问题或版式回归，回滚该字体提交即可恢复原系统字体栈；文章数据和站点功能不受影响。

## 11. 参考

- Fontsource Noto Sans SC 安装说明：<https://fontsource.org/fonts/noto-sans-sc/install>
- Fontsource 子集说明：<https://fontsource.org/docs/getting-started/subsets>

