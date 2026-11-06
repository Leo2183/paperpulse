# sm-scan 设计文档 — 空间代谢组学论文定期扫描器

日期:2026-11-06
状态:待用户审阅
路径:Architectural(全新桌面应用项目)

## 1. 背景与目标

用户需要一个**独立运行的 Windows 桌面程序**,按主题定期扫描已公开论文,聚焦:

- **MALDI 全领域**(基质辅助激光解吸电离,含成像与非成像方向)
- **产物注释**(代谢物/天然产物质谱注释方法)

程序不依赖任何第三方在线服务,数据落在本机,每天自动扫描一次,产出 Markdown 日报;带功能完整的 GUI 管理、阅读、标记论文,并与用户现有的文献精读工作流(`D:\workspace\spatial-metabolomics-papers\`)衔接。

### 已确认的需求决策

| 决策点 | 结论 | 确认轮次 |
|---|---|---|
| 主题范围 | MALDI 全领域 + 产物注释两组检索式并行 | 需求问答 |
| 运行方式 | Windows 任务计划程序定时,每天一次,完全独立于 ZCode | 需求问答 |
| 报告内容 | 元数据 + 英文摘要;LLM 中文导读为可选开关 | 需求问答 |
| GUI | 四视图全要(列表筛选/标记管理/检索式配置/扫描中心) | 需求问答 |
| 技术栈 | Python + PySide6(由助手决定) | 需求问答 |
| LLM 接口 | **通用 OpenAI 兼容格式**(base_url + api_key + model 三项可配) | 设计评审 |

## 2. 总体架构

```
D:\workspace\sm-scan\
├── sm_scan/                  # Python 包
│   ├── __init__.py
│   ├── __main__.py           # CLI 入口:python -m sm_scan scan|gui|report|task
│   ├── config.py             # 设置读写(settings 表 + 默认值)
│   ├── queries.py            # 检索式 IR 与各源语法翻译
│   ├── sources/
│   │   ├── __init__.py
│   │   ├── base.py           # Source 适配器基类(限速/重试/日志)
│   │   ├── europepmc.py
│   │   ├── openalex.py
│   │   └── biorxiv.py
│   ├── engine.py             # 扫描编排:窗口计算、三源并发收集、去重入库
│   ├── store.py              # SQLite 访问层(WAL)
│   ├── tags.py               # 标签判定(成像/植物/注释)
│   ├── llm.py                # OpenAI 兼容 chat/completions 客户端
│   ├── report.py             # Markdown 日报生成
│   ├── task.py               # Windows 任务计划注册/删除(schtasks 封装)
│   └── gui/
│       ├── __init__.py
│       ├── app.py            # QMainWindow、左侧导航、视图切换
│       ├── list_view.py      # 视图1:论文列表+筛选+摘要阅读
│       ├── mark_actions.py   # 标记动作与导出(嵌入视图1)
│       ├── queries_view.py   # 视图3:检索式配置
│       └── scan_view.py      # 视图4:扫描中心
├── data/                     # scan.db(运行时生成,git ignore)
├── reports/                  # 日报 md(生成物,git ignore)
├── tests/                    # pytest
├── docs/superpowers/specs/   # 本文档
└── pyproject.toml
```

**双入口,核心与壳分离**:

- `python -m sm_scan scan` — 纯 CLI 扫描引擎。任务计划每天调用的就是它,GUI 不启动也能完整工作。
- `python -m sm_scan gui` — PySide6 界面,只读写同一个 SQLite 并调用引擎函数,不含独立业务逻辑。

## 3. 数据源(三路)

| 源 | 覆盖 | 端点 | 说明 |
|---|---|---|---|
| Europe PMC | PubMed 全部期刊 + bioRxiv/medRxiv 预印本 | `https://www.ebi.ac.uk/europepmc/webservices/rest/search` | 主力;字段:`FIRST_PDATE`(收录日)、`TITLE`、`ABSTRACT`、`DOI`、`AUTHORS`、`JOURNAL` |
| OpenAlex | 全学科期刊(补 Anal Chem、JASMS 等 Crossref 系) | `https://api.openalex.org/works` | `title_and_abstract.search` + `from_publication_date/to_publication_date` 过滤;请求带 `mailto` 参数进 polite pool |
| bioRxiv/medRxiv | 预印本上线即抓 | `https://api.biorxiv.org/details/{server}/{from}/{to}/{cursor}` | 弥补 Europe PMC 索引预印本 1-3 天的滞后;按日期拉取后**本地关键词过滤**(API 不支持检索式) |

**网络纪律**(三源统一,`sources/base.py` 实现):

- 每源串行,请求间隔 ≥1s;超时 30s;失败重试 2 次(指数退避)
- 单源最终失败:记录到 run 日志、继续其他源,**不中断整体扫描**
- User-Agent 标识 `sm-scan/0.1 (local research tool)`

## 4. 检索式设计

### 4.1 内部 IR

检索式在引擎内部表示为**关键词组(term group)**,每个组 = 一组同义短语 + 开关:

```python
TermGroup(id, name, enabled, phrases: list[str])
```

各源适配器负责把 IR 翻译成本源语法;GUI 直接编辑 IR,不碰各源语法。短语的匹配语义统一为**标题/摘要字段包含该短语**(不区分大小写)。

### 4.2 默认两组(用户可增删改)

**组A "MALDI 全领域"**(默认开):

```
MALDI, matrix-assisted laser desorption ionization, matrix assisted laser desorption ionization, MALDI-MSI, MALDI imaging, mass spectrometry imaging, imaging mass spectrometry, spatial metabolomics
```

**组B "产物注释"**(默认开):

```
metabolite annotation, metabolite identification, natural product annotation, spectral annotation, metabolite structure elucidation
```

组B 短语噪音风险高("metabolite identification" 会命中大量非质谱文献),入库前再 AND 一个 `mass spectrometry OR MS/MS OR LC-MS OR MALDI` 限定——组级支持可选的 `refine` 短语列表,翻译时 AND 进去。

**⚠️ 已向用户明示的取舍**:组A 宽检索每天预计 20-50 篇(含微生物鉴定、蛋白质分析等非成像方向)。靠标签 + GUI 筛选消化噪音,跑一两周后按实际噪音在 GUI 里调窄,不改代码。

### 4.3 三源翻译规则

| 源 | 组内 OR | 组间 AND | 细节 |
|---|---|---|---|
| Europe PMC | `(ph1 OR ph2 ...)` 限定 `TITLE/ABSTRACT` 字段族 | `AND` | 形如 `(TITLE:"MALDI" OR ABSTR:"MALDI" OR ...) AND FIRST_PDATE:[d1 TO d2]` |
| OpenAlex | **每个短语单独一次请求**:`filter=title_and_abstract.search:"短语" AND from_publication_date:...`,短语加引号避免拆词;组内结果取并集;含 refine 时 refine 短语同样逐条请求,与组内并集做本地 AND | 组间取并集(两组是并列兴趣主题),去重靠 DOI | 每次扫描的请求数 = 短语总数 × 1,受 1 req/s 限速 |
| bioRxiv | API 不支持检索:按日期窗拉取全量条目,标题+摘要本地不区分大小写匹配任一短语;refine 同样本地 AND | — | 本地过滤结果与组绑定记标签 |

> 组间语义:**并集**(两组是并列的兴趣主题,不是取交集)。这一条是明确决策,写死在引擎语义里。

## 5. 去重与增量

- **主键**:DOI 归一化 —— 小写、剥离 `https://doi.org/`/`doi:`/`info:doi/` 前缀。无 DOI 条目用标题规范化哈希(小写、去标点、压空白、SHA1 前 16 位)兜底,键格式 `titlehash:<hash>`。
- **扫描窗口**:自上次成功 run 的起始日 - 3 天重叠(容索引修订/滞后),至今天。首次运行回填 30 天(可用 `--days N` 覆盖)。
- 已见主键:更新元数据(摘要可能从无到有)但不重复出现在"新论文"报告里;新主键入库、状态 `new`、记 `first_seen`。

## 6. 数据库 schema(SQLite,WAL 模式)

```sql
CREATE TABLE papers (
  id          TEXT PRIMARY KEY,      -- 归一化 DOI 或 titlehash:<hash>
  doi         TEXT,
  title       TEXT NOT NULL,
  authors     TEXT,                  -- " et al." 截断的展示串
  journal     TEXT,
  pub_date    TEXT,                  -- YYYY-MM-DD(缺失存年月/年)
  source      TEXT NOT NULL,         -- europepmc | openalex | biorxiv
  url         TEXT,                  -- 优先 DOI 跳转链接
  abstract    TEXT,
  tags        TEXT DEFAULT '[]',     -- JSON 数组
  status      TEXT DEFAULT 'new',    -- new | read | starred | to_read
  llm_digest  TEXT,                  -- 中文导读缓存,空=未生成
  first_seen  TEXT NOT NULL          -- ISO 时间戳
);
CREATE INDEX idx_papers_first_seen ON papers(first_seen);
CREATE INDEX idx_papers_pub_date ON papers(pub_date);
CREATE INDEX idx_papers_status ON papers(status);

CREATE TABLE runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT, finished_at TEXT,
  status TEXT,              -- running | ok | partial | failed
  new_count INTEGER,
  per_source TEXT,          -- JSON: 各源抓取/新增/错误计数
  error TEXT
);

CREATE TABLE term_groups (   -- 检索式 IR 持久化,GUI 直接编辑此表
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  enabled INTEGER DEFAULT 1,
  phrases TEXT NOT NULL,     -- JSON 数组
  refine TEXT DEFAULT '[]'   -- JSON 数组,组内 AND 限定
);

CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
-- 已知键:llm_base_url, llm_api_key, llm_model, src_europepmc_enabled,
--        src_openalex_enabled, src_biorxiv_enabled, openalex_mailto, backfill_days
```

**并发**:GUI 读列表与后台扫描线程并发,SQLite 开 WAL;写操作短事务。GUI 手动触发扫描在 QThread 中跑引擎,信号回传进度。

## 7. 标签判定(`tags.py`,入库时执行)

| 标签 | 规则(标题+摘要,不区分大小写) |
|---|---|
| `成像/空间` | 命中 `imaging, spatial, MSI, mass spectrometry imaging, mapping` 之一 |
| `植物` | 命中 `plant, plants, tissue, root, leaf, seed, flower, natural product, biosynthesis` 之一 |
| `注释` | 命中组B 任一短语 |

规则表硬编码于 `tags.py` 顶部常量(够用;进 GUI 配置是过度设计)。「注释」标签按**入库当时**组B 的短语判定,是其快照;事后改组B 不回溯已入库论文的标签。

## 8. LLM 中文导读(OpenAI 兼容接口)

**默认关**。开启路径:GUI 逐篇/批量按钮,或 CLI `--llm`。

**配置三项**(GUI 检索式配置视图内填写,存 settings 表):

- `llm_base_url` — 如 `https://open.bigmodel.cn/api/paas/v4` 或自建/本地端点
- `llm_api_key`
- `llm_model`

**请求**:标准 OpenAI chat completions 格式——

```
POST {base_url}/chat/completions
{
  "model": "<llm_model>",
  "messages": [
    {"role": "system", "content": "你是科研文献导读助手。用一句中文(60字内)概括这篇论文做了什么、关键发现是什么。只输出这一句话。"},
    {"role": "user", "content": "标题: ...\n摘要: ..."}
  ],
  "temperature": 0.2
}
```

- base_url 归一化:若不以 `/chat/completions` 结尾则自动拼接,用户填到 `/v4` 或 `/v4/chat/completions` 均可
- 超时 60s,重试 1 次;逐篇串行(不并发轰炸);单篇失败跳过并计数,不中断批量
- 结果写入 `papers.llm_digest` 持久缓存,已有导读的不再调用
- key 只存本机 SQLite(`data/` 在 .gitignore),不进 git

## 9. GUI 设计(PySide6)

主窗口 = 左侧固定导航栏(4 项)+ 右侧视图区,单个 `QStackedWidget` 切换。

### 视图1:论文列表(默认视图)
- 顶部筛选条:日期范围(QDateEdit ×2)、来源下拉、状态下拉、标签下拉、关键词搜索框(QLineEdit,匹配标题)
- 表格列:日期 | 标题 | 期刊 | 来源 | 标签 | 状态;默认按 pub_date 降序,点表头可排序
- 单击行 → 底部/右侧详情面板:完整标题、作者、期刊、摘要原文(可滚动)、DOI 链接(点击调系统浏览器)、LLM 导读(有则显示,无则显示"生成导读"按钮)
- 右键菜单:标记已读/收藏/待精读/清除标记、复制 DOI、浏览器打开、生成导读
- 工具栏按钮:**导出待精读清单**——把 `status=to_read` 的论文导出为 `reports/todo-reading-YYYYMMDD.md`(标题+DOI+一句话),衔接精读工作流

### 视图2:标记管理
不设独立页面——标记动作在视图1 的右键/快捷键完成,导航栏第 2 项为**状态过滤快捷页**(已读/收藏/待精读三个预设过滤的列表,复用视图1 表格组件)。这是实现层的复用决策,用户看到的行为仍是四个导航项。

### 视图3:检索式配置
- 关键词组列表(组名、开关、短语数),选中组编辑:短语 QTextEdit(每行一条)、refine 短语、启用开关
- 组的增删改,改动即存 `term_groups` 表,下次扫描生效
- 数据源开关(三个复选框)、OpenAlex mailto、回填天数
- LLM 配置区:base_url / api_key(密码框)/ model + "测试连接"按钮(发一条 ping 消息验证)

### 视图4:扫描中心
- 当前配置摘要 + **立即扫描**按钮(QThread,进度条 + 滚动日志区,完成后刷新)
- 定时任务区:显示当前任务计划状态(**注册**/删除按钮,封装 `schtasks`,任务名 `sm-scan-daily`,每日 09:00,`pythonw -m sm_scan scan` 无窗口执行,工作目录设为解释器对应安装目录)
- 历史报告列表:`reports/*.md` 按日期列出,点击用系统默认程序打开
- 批量导读:对"最新 N 天、状态 new"论文批量生成(调用同引擎,进度显示)

## 10. Windows 任务计划集成(`task.py`)

- `register`:以编程方式调用 `schtasks /Create /TN sm-scan-daily /TR "\"<pythonw 路径>\" -m sm_scan scan" /SC DAILY /ST 09:00 /F`,pythonw 路径取 `sys.executable` 同目录的 pythonw.exe
- `remove`:`schtasks /Delete /TN sm-scan-daily /F`
- `status`:查询 `/Query`,返回注册与否
- 扫描运行时写 `data/last_run.lock`(pid + 时间戳)防重入:任务计划触发与 GUI 手动触发撞车时,后到者直接退出并记日志

## 11. CLI 接口

```
python -m sm_scan scan [--days N] [--llm] [--dry-run]   # 扫描(dry-run 只打印将请求的窗口与检索式)
python -m sm_scan gui                                    # 启动 GUI
python -m sm_scan report [--date YYYY-MM-DD]             # 手动重新生成某日报告
python -m sm_scan task register|remove|status            # 定时任务管理
```

`scan` 退出码:0=ok,2=partial(有源失败),3=failed(全失败/异常)。日志落 `data/scan.log`(滚动,1MB×3)。

## 12. 日报格式(`reports/YYYY-MM-DD.md`)

```markdown
# sm-scan 日报 2026-11-06
新增 23 篇(Europe PMC 15 / OpenAlex 6 / bioRxiv 2)| 检索式组A✓ 组B✓

## 带「成像/空间」标签(14)
### [2026-11-05] Paper title — *Journal* [标签]
作者串 | [DOI](https://doi.org/...) | 来源 europepmc
> 摘要前 300 字…(有 LLM 导读时改为:> **导读**:…)
...
## 其他(9)
...
## 待精读(2)
...
```

## 13. 错误处理

| 场景 | 行为 |
|---|---|
| 单源 API 失败 | 重试 2 次后放弃该源,run 记 partial,报告注明 |
| 网络 DNS/断网 | 三源全失败 → run 记 failed,退出码 3;GUI 弹提示 |
| LLM 未配置就触发导读 | 明确报错"请先在检索式配置中填写 LLM 三项",不静默跳过 |
| LLM 单篇失败 | 跳过 + 计数,批量继续 |
| DB 被锁 | WAL 下罕见;写事务重试 3 次(间隔 200ms)后报错 |
| 任务计划已注册再注册 | `/F` 覆盖并提示 |

## 14. 测试策略(pytest)

引擎层单测(全部离线,mock HTTP):

1. **DOI/标题归一化**:前缀剥离、大小写、无 DOI 标题哈希稳定性
2. **检索式翻译**:IR → Europe PMC 查询串 / OpenAlex 参数 / 本地过滤函数,含 refine 的 AND 语义、组间并集
3. **增量窗口**:首跑回填、重叠 -3 天、已见 ID 不重复计数
4. **解析器**:三源各一份真实响应样本 fixture → 统一 Paper 记录
5. **标签判定**:边界(空摘要、MSI 歧义词在非质谱语境)
6. **LLM 客户端**:base_url 归一化两种填法、失败重试、缓存不重调
7. **报告生成**:固定输入 → 固定 md 快照

GUI 不写自动化测试(PySide6 自动化成本 > 收益),交付前按验收清单手动过一遍。

## 15. 打包

- PyInstaller **onedir** 模式(启动快于 onefile):`sm-scan-gui.exe`(含 GUI)与 `sm-scan.exe`(纯 CLI,供任务计划)
- 产物目录 `dist/sm-scan/`,整目录拷走即可用,免 Python 环境
- `pythonw -m sm_scan scan` 定时任务在打包版下指向 `sm-scan.exe scan`

## 16. 非目标(本期不做)

- 自动下载 PDF / 与 `spatial-metabolomics-papers` 目录的导入联动
- 邮件/IM 推送
- 相似论文推荐、引用图
- 多用户/网络服务化
- WoS / Scopus(无免费 API)

## 17. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 组A 宽检索噪音大(每日 20-50 篇) | 标签 + GUI 筛选消化;GUI 改检索式即可调窄,已向用户明示 |
| "MSI" 等缩写歧义 | 默认检索式用全短语为主;缩写只在标题字段匹配,不做全文 |
| OpenAlex `search` 召回与字段语义随版本变化 | 适配器单测 + 真实 fixture 锁定行为;失败不阻塞 |
| bioRxiv API 分页游标变化 | 按官方 cursor 协议实现,fixture 锁定 |
| Europe PMC 检索式过长(短语多) | 查询串超限时按短语分批拆为多次请求,结果并集 |
| PySide6 + PyInstaller 体积/插件坑 | onedir + 常规 Qt 插件排除,打包脚本进仓库 |

## 18. 交付物清单

1. 完整源码 + 测试(上述结构)
2. `pyproject.toml`(依赖:`pyside6, requests, pytest`;Python ≥3.10)
3. 打包脚本与说明(README:安装、注册定时任务、LLM 配置)
4. 首次回填 30 天并生成首份日报作为验收
