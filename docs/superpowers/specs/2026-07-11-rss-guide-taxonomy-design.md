# Paper Radar 说明区与自动分类升级设计

- 日期：2026-07-11
- 状态：用户已批准
- 参考页面：<https://hehuifeng.github.io/rss_site/>

## 1. 目标

在现有单页 Paper Radar 网站中，用“说明”取代“关于”，并增加两个与仓库配置保持同步的内容区：

1. 每天检查的 RSS 列表；
2. 标签与关键词。

同时升级自动分类，使说明区展示的精细标签与英文关键词成为实际运行规则，而不是只用于展示。每天更新完成后，系统重新标注全部已存论文，保证旧论文也采用最新词表。

## 2. 已批准决策

1. 使用方案 A：保留现有单页结构，将顶部“关于”导航和底部“关于”区改为“说明”。
2. 保留原有项目介绍、RSS 元数据边界和文章版权声明。
3. RSS 按出版社分组折叠；当前配置为 Nature Portfolio 11 个、IEEE 6 个、AIP Publishing 1 个、Wiley 2 个，共 20 个。
4. 标签按 8 个一级研究方向分组折叠。
5. 一级方向只用于说明页组织，不成为数据库标签或筛选条件。
6. 精细标签仍是实际自动标签和筛选单位；一篇论文可以匹配多个精细标签。
7. 自动分类与说明区共用 `topics.yml`，RSS 说明与抓取共用 `feeds.yml`。
8. 每次自动更新对全部论文重新分类并清理失效标签。
9. 精细标签字号为 12px，英文关键词字号为 11px；桌面和移动端一致。一级方向标题为 13px。
10. 桌面使用双列折叠卡片，移动端改为单列，不增加横向滚动。

## 3. 范围

### 3.1 包含

- 扩展主题配置模型，表达 8 个一级方向和精细标签所属关系。
- 扩充精细标签和英文关键词。
- 改进连字符、Unicode 破折号、空白和完整词边界匹配。
- 对数据库全部论文进行事务式重新分类。
- 从配置生成并校验说明区 HTML。
- 更新导航、说明区样式、可访问性和响应式布局。
- 更新 README 中的标签维护说明。
- 在部署后触发一次真实云端更新并核验数据库和 Pages。

### 3.2 不包含

- 不增加一级方向筛选器。
- 不把一级方向写入 SQLite `tags` 表。
- 不改变 RSS 来源、抓取频率、文章去重、OA 补全或文章类型识别。
- 不引入前端框架或运行时 YAML 解析器。
- 不增加人工智能翻译功能。

## 4. 配置模型

`topics.yml` 是主题说明与自动分类的唯一来源，结构扩展为：

```yaml
topic_groups:
  - id: acoustic-rf
    label: 声学与射频器件
    order: 1

topics:
  - id: baw
    label: BAW
    group: acoustic-rf
    keywords:
      - bulk acoustic wave
      - bulk acoustic resonator
      - BAW resonator
      - BAW filter

  - id: xray-characterization
    label: X-ray characterization
    group: characterization-reliability
    requires_any_group:
      - acoustic-rf
      - piezo-ferroelectric
      - ultrasound-sensing
      - mems-nems
      - electronic-semiconductor
      - ai-computational
      - emerging-cross-disciplinary
    keywords:
      - x-ray diffraction
      - XRD
```

新增配置对象：

- `TopicGroupConfig(id, label, order)`；
- `TopicConfig(id, label, group, keywords, requires_any_group=())`；
- `TopicCatalog(groups, topics)`，供说明生成和分类共同使用。

`load_topic_catalog()` 负责完整校验；现有 `load_topics()` 保留为兼容包装，返回 catalog 中的 topics。

校验要求：

- group id、topic id、group order 和显示标签均唯一；
- `order` 必须是正整数且连续为 1–8；
- 每个 topic 必须引用已存在的 group；
- 每个 group 至少包含一个 topic；
- 每个 topic 至少包含一个非空关键词；
- `requires_any_group` 可省略；如存在，必须是非空且不重复的有效 group id 列表，不得包含 topic 自己所属的 group；
- 同一 topic 内不允许规范化后重复的关键词；
- 不允许未知字段、重复 YAML key 或空字符串。

## 5. 一级方向、精细标签与关键词

所有关键词匹配标题和摘要。以下词表是第一版生产词表。

### 5.1 声学与射频器件

| 精细标签 | 英文关键词 |
|---|---|
| BAW | `bulk acoustic wave`; `bulk acoustic resonator`; `BAW resonator`; `BAW filter` |
| SAW | `surface acoustic wave`; `surface acoustic resonator`; `SAW resonator`; `SAW filter` |
| FBAR | `film bulk acoustic resonator`; `thin-film bulk acoustic resonator`; `FBAR`; `FBAR filter` |
| Lamb wave | `Lamb wave`; `Lamb-wave resonator`; `Lamb wave resonator`; `LWR`; `contour-mode resonator` |
| Acoustic resonator | `acoustic resonator`; `piezoelectric resonator`; `acoustic filter`; `resonator filter` |
| RF & Microwave | `radio frequency`; `RF front-end`; `RF filter`; `microwave`; `millimeter wave`; `millimetre wave`; `mmWave` |
| Multiplexer | `duplexer`; `multiplexer`; `diplexer`; `filter bank`; `frequency multiplexer` |

`quality factor`、`insertion loss`、`electromechanical coupling` 和 `TCF` 不作为独立触发词。

### 5.2 压电与铁电薄膜

| 精细标签 | 英文关键词 |
|---|---|
| Piezoelectric | `piezoelectric`; `piezoelectricity`; `piezoelectric coefficient`; `piezoelectric thin film` |
| Ferroelectric | `ferroelectric`; `ferroelectricity`; `ferroelectric thin film`; `ferroelectric polarization` |
| AlN | `aluminum nitride`; `aluminium nitride`; `AlN thin film`; `AlN piezoelectric` |
| AlScN | `aluminum scandium nitride`; `aluminium scandium nitride`; `scandium-doped AlN`; `ScAlN`; `AlScN` |
| PZT | `lead zirconate titanate`; `PZT thin film`; `PZT piezoelectric` |
| LiNbO3 | `lithium niobate`; `LiNbO3`; `thin-film lithium niobate`; `LNOI` |
| HfO2/HZO | `ferroelectric hafnium oxide`; `hafnium zirconium oxide`; `HfO2 ferroelectric`; `HZO ferroelectric` |
| Lead-free piezoelectrics | `lead-free piezoelectric`; `potassium sodium niobate`; `KNN`; `barium titanate`; `BaTiO3` |
| Film growth | `reactive sputtering`; `magnetron sputtering`; `MOCVD`; `atomic layer deposition`; `pulsed laser deposition`; `sol-gel deposition`; `epitaxial growth` |

普通的 `sputtering`、`annealing` 和 `thin film` 不作为独立触发词。

### 5.3 超声换能器与声学传感

| 精细标签 | 英文关键词 |
|---|---|
| PMUT | `piezoelectric micromachined ultrasonic transducer`; `piezoelectric micromachined ultrasound transducer`; `PMUT`; `PMUT array` |
| CMUT | `capacitive micromachined ultrasonic transducer`; `capacitive micromachined ultrasound transducer`; `CMUT`; `CMUT array` |
| Ultrasonic transducer | `ultrasonic transducer`; `ultrasound transducer`; `piezoelectric transducer`; `high-frequency transducer` |
| Ultrasound imaging | `ultrasound imaging`; `ultrasonic imaging`; `medical ultrasound`; `high-frequency ultrasound`; `photoacoustic imaging` |
| Therapeutic ultrasound | `therapeutic ultrasound`; `focused ultrasound`; `high-intensity focused ultrasound`; `HIFU` |
| Acoustic sensing | `acoustic sensor`; `ultrasonic sensor`; `acoustic sensing`; `ultrasonic sensing`; `non-destructive testing`; `nondestructive evaluation` |

### 5.4 MEMS/NEMS 与微纳制造

| 精细标签 | 英文关键词 |
|---|---|
| MEMS | `microelectromechanical system`; `micro-electromechanical system`; `MEMS device`; `MEMS resonator`; `MEMS sensor` |
| NEMS | `nanoelectromechanical system`; `nano-electromechanical system`; `NEMS device`; `NEMS resonator` |
| Microfabrication | `microfabrication`; `micromachining`; `surface micromachining`; `bulk micromachining`; `deep reactive ion etching` |
| Wafer integration | `wafer bonding`; `wafer-level packaging`; `through-silicon via`; `TSV integration`; `heterogeneous integration` |
| CMOS integration | `CMOS-compatible`; `CMOS integration`; `monolithic integration`; `back-end-of-line`; `BEOL integration` |
| Packaging | `MEMS packaging`; `hermetic packaging`; `vacuum packaging`; `chip-scale packaging` |

普通的 `etching`、`lithography` 和 `packaging` 不作为独立触发词。

### 5.5 电子与半导体器件

| 精细标签 | 英文关键词 |
|---|---|
| Transistor | `field-effect transistor`; `thin-film transistor`; `MOSFET`; `FinFET`; `GAAFET`; `TFET`; `HEMT` |
| Ferroelectric transistor | `ferroelectric field-effect transistor`; `FeFET`; `negative capacitance transistor`; `NCFET` |
| Memory & Memristor | `nonvolatile memory`; `resistive random-access memory`; `RRAM`; `memristor`; `ferroelectric memory`; `neuromorphic device` |
| Power electronics | `power semiconductor`; `power transistor`; `power diode`; `high-voltage device`; `power electronic device` |
| Wide-bandgap devices | `gallium nitride device`; `GaN transistor`; `GaN HEMT`; `silicon carbide device`; `SiC MOSFET`; `ultra-wide-bandgap semiconductor` |
| 2D electronics | `two-dimensional transistor`; `2D semiconductor`; `MoS2 transistor`; `transition metal dichalcogenide`; `van der Waals device` |
| Sensors | `electronic sensor`; `chemical sensor`; `gas sensor`; `pressure sensor`; `biosensor`; `strain sensor` |

### 5.6 人工智能与计算设计

| 精细标签 | 英文关键词 |
|---|---|
| Machine learning | `machine learning`; `deep learning`; `neural network`; `convolutional neural network`; `recurrent neural network` |
| Transformer & LLM | `transformer model`; `large language model`; `foundation model`; `generative artificial intelligence`; `generative AI` |
| Inverse design | `inverse design`; `topology optimization`; `generative design`; `computational design optimization` |
| Surrogate modelling | `surrogate model`; `reduced-order model`; `Bayesian optimization`; `Gaussian process regression` |
| Physics-informed AI | `physics-informed neural network`; `physics-informed machine learning`; `PINN`; `neural operator` |
| Materials informatics | `materials informatics`; `materials discovery`; `machine-learning interatomic potential`; `property prediction` |
| Autonomous research | `autonomous experiment`; `self-driving laboratory`; `automated experimentation`; `active learning`; `robotic laboratory` |
| Digital twin | `digital twin`; `virtual sensor`; `data-driven modelling`; `predictive maintenance` |

不使用独立的 `AI`、`ML` 或 `DL` 作为关键词。

### 5.7 材料表征与器件可靠性

| 精细标签 | 英文关键词 |
|---|---|
| X-ray characterization | `x-ray diffraction`; `XRD`; `reciprocal space mapping`; `RSM`; `rocking curve`; `omega scan` |
| Electron microscopy | `scanning electron microscopy`; `SEM`; `transmission electron microscopy`; `TEM`; `STEM` |
| Probe microscopy | `atomic force microscopy`; `AFM`; `piezoresponse force microscopy`; `PFM`; `Kelvin probe force microscopy` |
| Spectroscopy | `x-ray photoelectron spectroscopy`; `XPS`; `Raman spectroscopy`; `secondary ion mass spectrometry`; `SIMS` |
| Crystal quality | `crystal orientation`; `c-axis texture`; `mosaicity`; `residual stress`; `dislocation density`; `full width at half maximum` |
| Reliability | `device reliability`; `fatigue endurance`; `breakdown field`; `time-dependent dielectric breakdown`; `aging`; `thermal stability`; `frequency drift` |

表征类标签实施上下文门槛：至少出现一个表征关键词，同时出现任一非表征方向的材料或器件关键词。这样避免所有仅提及 SEM/XRD 的论文都被标成用户关注主题。该门槛在配置中以明确的匹配模式表达，不在代码中硬编码特定标签 id。

### 5.8 新兴交叉方向

| 精细标签 | 英文关键词 |
|---|---|
| Phononics | `phononics`; `phononic crystal`; `phononic bandgap`; `acoustic metamaterial`; `topological acoustics` |
| Quantum acoustics | `quantum acoustics`; `quantum acoustic`; `phonon qubit`; `single phonon`; `microwave-to-acoustic conversion` |
| Optomechanics | `cavity optomechanics`; `optomechanical resonator`; `acousto-optic interaction`; `microwave-to-optical conversion` |
| Acoustofluidics | `acoustofluidics`; `acoustic microfluidics`; `surface acoustic wave microfluidics`; `acoustic particle manipulation` |
| Energy harvesting | `piezoelectric energy harvesting`; `piezoelectric energy harvester`; `vibration energy harvesting`; `triboelectric generator` |
| Flexible devices | `flexible electronics`; `stretchable electronics`; `wearable sensor`; `flexible piezoelectric`; `bio-integrated electronics` |
| Nonreciprocal acoustics | `nonreciprocal acoustics`; `non-reciprocal acoustic`; `acoustic isolator`; `acoustic circulator` |

## 6. 匹配语义

1. 搜索字段仅为规范化后的标题和摘要。
2. 英文大小写不敏感，并继续使用 Unicode `casefold()`。
3. 关键词两端必须满足完整 token 边界；字母、数字、组合标记和连接符均视为 token 组成部分。
4. 所有 Unicode `Pd` 破折号、ASCII 连字符和连续空白在匹配视图中归一化为单个空格。
5. 因此 `surface acoustic wave` 可以匹配 `surface-acoustic-wave`、`surface–acoustic–wave` 和多空格写法。
6. `SAW` 不匹配 `seesaw`，`RF` 不匹配其他单词内部的 `rf`，`SEM` 不匹配更长标识符。
7. 化学式和材料缩写采用相同完整 token 边界规则。
8. 默认匹配模式是 topic 任一关键词命中；需要上下文门槛的 topic 可声明 `requires_any_group`，要求列表中任一方向至少有一个 topic 同时产生基础关键词命中。
9. 分类分两步执行：先计算所有 topic 的基础关键词命中，再应用 `requires_any_group` 门槛；上下文 topic 不反向激活其他 topic，避免循环依赖。
10. 一篇论文可以匹配任意数量的 topic；输出顺序遵循配置顺序且不重复。

## 7. 全量重新分类

现有 pipeline 只会重新标注本次 RSS 返回的文章，因此不足以传播词表修改。新增数据库级全量重新分类阶段：

1. 完成全部 RSS 抓取、规范化和文章 upsert；
2. 按稳定 UID 顺序读取全部文章；
3. 在内存中计算每篇文章的目标 topic 集合；
4. 在一个数据库事务中替换全部 `article_tags`；
5. upsert 所有实际使用的 tags，并删除无文章引用的旧 tags；
6. 返回 `articles_scanned`、`articles_tagged`、`tag_assignments` 和 `active_tags`；
7. 随后运行现有数据库验证与原子发布。

任何配置、分类、数据库写入或验证失败都必须回滚重新分类，并阻止公开数据库替换。现有已发布数据库保持可用。

当前约 777 篇文章，56 个精细标签，计算规模足以在每次每日更新中全量执行，无需增量缓存。

## 8. 说明区信息架构

### 8.1 导航与位置

- 顶部导航 `关于` 改为 `说明`，锚点从 `#about` 改为 `#guide`。
- 说明区仍位于数据状态之后、页脚之前。
- eyebrow 使用 `GUIDE / 03`，主标题为 `说明`。

### 8.2 介绍与状态摘要

开头说明：

- GitHub 云端每天北京时间 08:00 计划触发，繁忙时可能延迟；
- 不需要打开 Codex 或保持本地电脑开机；
- 聚合公开 RSS 元数据；
- 自动标签只用于初步筛选，不能替代人工分类。

紧随其后显示四个简短状态：更新时间、RSS 总数、一级方向数、云端运行。

### 8.3 RSS 列表

- 标题：`每天检查的 RSS 列表`；
- 显示由 `feeds.yml` 计算的启用源总数；
- 按 Nature Portfolio、IEEE、AIP Publishing、Wiley 分组；
- 每个出版社使用原生 `<details>/<summary>`；
- 每行显示期刊名和完整可点击 HTTPS RSS URL；
- 外链使用安全的 `rel` 属性；
- 禁用源不计入“每天检查”，但生成检查可报告其存在。

### 8.4 标签与关键词

- 标题：`标签与关键词`；
- 显示 8 个一级方向；
- 每个方向使用原生 `<details>/<summary>`；
- summary 显示顺序号、方向中文名和精细标签数；
- 展开内容按配置顺序显示精细标签与全部英文关键词；
- 精细标签为 12px/800，关键词为 11px/1.65 行高和等宽字体；
- 第一方向可默认展开，其余默认折叠；
- 桌面双列，窄屏单列。

### 8.5 版权与数据边界

说明区底部保留并扩展现有文字：本站只聚合公开 RSS 元数据，文章版权归原出版商所有；OA 状态和自动标签可能不完整或误判。

## 9. 静态生成与同步

新增 `scripts/render_site_guide.py`：

- 读取并严格验证 `feeds.yml` 与 `topics.yml`；
- HTML escape 所有配置文本；
- 只接受经验证的 HTTPS feed URL；
- 在 `docs/index.html` 的稳定起止 marker 之间生成说明内容；
- 默认模式更新 marker 内容；
- `--check` 模式不写文件，若输出不一致则非零退出；
- 输出确定性固定，不包含当前时间或机器相关路径。

说明 HTML 在提交时生成，浏览器运行时不加载 YAML，也不新增 JavaScript 依赖。配置修改流程为：编辑 YAML → 运行 renderer → 运行测试 → 同一提交包含配置和生成 HTML。

每日 RSS workflow 只修改 `docs/data/papers.db`，不会修改配置或生成 HTML，因此现有 tracked-change 白名单保持不变。

## 10. 错误处理

- 配置不合法：在网络请求和数据库写入前失败。
- 说明输出过期：测试和 `--check` 失败，阻止提交。
- HTML 中出现未转义配置：安全测试失败。
- 重新分类失败：事务回滚，CLI 返回非零，公开 DB 不替换。
- RSS 部分失败：沿用现有 partial 状态；对数据库中全部现存文章仍进行确定性重新分类，但只有通过现有发布闸门时才发布。
- 没有匹配文章的 topic：不出现在侧栏实际筛选项，但仍出现在说明词表中。
- 没有启用 RSS：配置校验失败。

## 11. 测试与验收

### 11.1 配置

- 8 个 group、顺序、唯一性和非空校验；
- 56 个 topic 均指向有效 group；
- 重复/未知/空字段和规范化重复关键词被拒绝；
- 生产词表与本规范一致。

### 11.2 分类

- 标题和摘要匹配；
- 大小写、Unicode、连字符、Unicode 破折号和连续空格；
- SAW/seesaw、RF/长单词、缩写+数字/下划线等负面边界；
- 材料式、缩写和多标签；
- 表征类上下文门槛；
- 不使用 `AI`、`ML`、`DL` 等过短触发词。

### 11.3 全量重新分类

- 不依赖 RSS 是否返回旧文章，所有现存文章都重新标注；
- 新增、修改和删除 topic 后关系正确替换；
- 不再使用的 tags 被删除；
- 事务中途失败后所有旧关系保留；
- 统计计数准确；
- 重复运行幂等。

### 11.4 说明生成

- `--check` 检测 drift；
- 20 个启用 RSS、完整 URL、出版社分组和数量一致；
- 8 个方向、56 个精细标签和全部关键词一致；
- HTML escape 和外链安全属性；
- 不出现禁用 RSS。

### 11.5 前端

- 导航和锚点为“说明”/`#guide`；
- 原生 details 可鼠标、键盘和屏幕阅读器操作；
- 桌面双列、390px 移动端单列；
- 精细标签 12px、关键词 11px；
- 无横向溢出、重叠或可见编码错误；
- 现有搜索、筛选、分页、抽屉、字体和数据库加载不回归。

### 11.6 发布

- Python、Node、Playwright、Ruff、生成检查和 `git diff --check` 全部通过；
- 重新生成本地工作数据库并通过现有验证闸门；
- 推送后手动触发一次 Daily RSS Update；
- 机器人提交只包含 `docs/data/papers.db`；
- Pages 构建成功；
- 线上说明与配置一致，线上 SQLite 完整性通过。

## 12. 维护约定

- 添加期刊：更新 `feeds.yml`，重新生成说明并运行测试。
- 添加或修改标签：更新 `topics.yml`，重新生成说明并运行测试。
- 关键词以高精度优先，不为提高召回率加入明显宽泛的单词。
- 任何宽泛方法词必须通过短语限定或上下文门槛。
- 自动标签只作发现与筛选工具，不作为学术结论或人工标注替代品。
