# sm-scan 设计文档 — 空间代谢组学论文定期扫描器

日期:2026-11-06
状态:待用户审阅(v2,已按第 01 轮评审修订)
路径:Architectural(全新桌面应用项目)

## 1. 背景与目标

用户需要一个**独立运行的 Windows 桌面程序**,按主题定期扫描已公开论文,聚焦植物与空间代谢组学、MALDI/MSI 方法学、产物注释。

程序不依赖需要注册或付费的商业数据库(WoS/Scopus),全部走免费公开 API(Europe PMC、OpenAlex、bioRxiv;OpenAlex 免费注册 key 可选);数据落本机,每天自动扫描一次,产出 Markdown 日报;带功能完整的 GUI 管理、阅读、标记论文,并与用户现有的文献精读工作流(`D:\workspace\spatial-metabolomics-papers\`)衔接。

### 已确认的需求决策

| 决策点 | 结论 | 确认轮次 |
|---|---|---|
| 主题范围 | 三组检索式:①植物+空间成像(核心)②MALDI/MSI 方法学 ③产物注释 | 第 01 轮评审 P1-1 方案C |
| 运行方式 | Windows 任务计划程序定时,每天一次,完全独立于 ZCode | 需求问答 |
| 报告内容 | 元数据 + 英文摘要;LLM 中文导读为可选开关 | 需求问答 |
| GUI | 四视图(列表筛选/标记管理/检索式配置/扫描中心) | 需求问答 |
| 技术栈 | Python + PySide6(助手决定) | 需求问答 |
| LLM 接口 | 通用 OpenAI 兼容格式(base_url + api_key + model 三项可配) | 设计评审 |
| OpenAlex | 使用 `search.title_and_abstract` 新语法;API key 可选(keyless 10 req/s 可跑,免费 key 额度 $1/天、限速 30 req/s) | 2026-11-06 官方文档核实 |

## 2. 总体架构

```
D:\workspace\sm-scan\
├── sm_scan/                  # Python 包
│   ├── __init__.py
│   ├── __main__.py           # CLI 入口:python -m sm_scan scan|gui|report|task
│   ├── config.py             # 设置读写 + 数据目录绝对定位(不依赖 cwd)
│   ├── queries.py            # 检索式 IR(短语+scope+refine)与各源语法翻译
│   ├── sources/
│   │   ├── __init__.py
│   │   ├── base.py           # Source 适配器基类(限速/重试/日志)
│   │   ├── europepmc.py
│   │   ├── openalex.py
│   │   └── biorxiv.py
│   ├── engine.py             # 扫描编排:窗口计算、三源收集、去重入库
│   ├── store.py              # SQLite 访问层(WAL)
│   ├── tags.py               # 标签判定(成像/植物/注释)
│   ├── llm.py                # OpenAI 兼容 chat/completions 客户端
│   ├── report.py             # Markdown 日报生成
│   ├── task.py               # Windows 任务计划注册/删除(schtasks 封装)
│   └── gui/
│       ├── __init__.py
│       ├── app.py            # QMainWindow、左侧导航、视图切换
│       ├── list_view.py      # 视图1:论文列表+筛选+摘要阅读
│       ├── mark_view.py      # 视图2:标记管理(批量操作+交叉筛选+导出)
│       ├── queries_view.py   # 视图3:检索式配置
│       └── scan_view.py      # 视图4:扫描中心
├── data/                     # scan.db 等(运行时生成,git ignore)
├── reports/                  # 日报 md(生成物,git ignore)
├── tests/                    # pytest + 真实 API 响应 fixtures
├── docs/superpowers/specs/   # 本文档
├── 审查报告/                  # 评审记录
└── pyproject.toml
```

**双入口,核心与壳分离**:

- `python -m sm_scan scan` — 纯 CLI 扫描引擎。任务计划每天调用的就是它,GUI 不启动也能完整工作。
- `python -m sm_scan gui` — PySide6 界面,只读写同一个 SQLite 并调用引擎函数,不含独立业务逻辑。

**数据目录绝对定位**(`config.py`,不依赖进程 cwd):

- 开发态:`Path(__file__).resolve().parent.parent`(即仓库根下的 `data/`、`reports/`)
- 打包态(`sys.frozen` 为真):`Path(sys.executable).parent` 下同名目录——满足「拷走整个 dist 目录即可用」与「任务计划在任意 cwd 下能跑」

## 3. 数据源(三路)

### 3.1 Europe PMC(主力:PubMed 期刊 + bioRxiv/medRxiv 预印本)

端点:`https://www.ebi.ac.uk/europepmc/webservices/rest/search`

| 要点 | 约定 |
|---|---|
| 摘要 | **`resultType=core` 必须显式传**(默认 `lite` 不含 abstract) |
| 字段匹配 | 用 `TITLE_ABS:"短语"` 一次覆盖标题+摘要;`TITLE:` 仅标题(歧义缩写专用,见 §4.2 scope) |
| 增量窗口 | **`FIRST_IDATE`**(首次被索引日期)——跟踪"新入库"而非"新出版";补录/延迟索引都能扫到 |
| 展示日期 | `FIRST_PDATE` / `firstPublicationDate` → `pub_date`(电子与印刷取最早) |
| 分页 | `pageSize=100`(上限 1000),深翻页用 `cursorMark=*` 起步、逐页取 `nextCursorMark` |
| 错误识别 | 失败可能是 **HTTP 200 + `errCode`/无 `resultList`**——适配器必须显式检查 `resultList` 存在,不能只看状态码 |
| 其他参数 | `format=json` |

请求示例(组1,窗口 2026-10-04→11-06):

```
(TITLE_ABS:"mass spectrometry imaging" OR TITLE_ABS:"MALDI-MSI" OR TITLE_ABS:"spatial metabolomics")
AND (TITLE_ABS:"plant" OR TITLE_ABS:"natural product" OR ...)
AND FIRST_IDATE:[2026-10-04 TO 2026-11-06]
```

### 3.2 OpenAlex(补全:Anal Chem、JASMS、Talanta 等 Crossref 系期刊)

端点:`https://api.openalex.org/works`

| 要点 | 约定 |
|---|---|
| 检索语法 | `?search.title_and_abstract=<布尔表达式>`(2026-11 官方文档确认;支持大写 `AND/OR/NOT` 与 `"引号短语"`)。旧 `filter=title_and_abstract.search:` 已标记 Deprecated,不使用 |
| 组内 → 单请求 | 每组拼成一条布尔表达式(短语加引号 OR 连接,refine 以 AND 拼入),**一组一请求**,不逐短语轰炸 |
| API key | `settings.openalex_api_key` **可空**;有则 `api_key` 查询参数附带(免费注册,额度 $1/天、限速 30 req/s);无则 keyless(10 req/s)运行,本项目日请求量 3-6 次两者都远够 |
| 分页 | `per-page=100`(上限 100)+ `cursor=*` 游标 |
| 摘要 | 返回 `abstract_inverted_index`(倒排索引),入库前按位置重建文本 |
| URL 上限 | 8190 字节;超限按短语分块拆为多次请求、结果本地并集(与 Europe PMC 拆分策略同构,见 §17) |

请求示例:

```
GET https://api.openalex.org/works
  ?search.title_and_abstract=("mass spectrometry imaging" OR "MALDI-MSI" OR "spatial metabolomics") AND (plant OR "natural product")
  &filter=from_publication_date:2026-10-04
  &per-page=100&cursor=*
```

### 3.3 bioRxiv/medRxiv(预印本上线即抓,补 Europe PMC 索引滞后)

端点:`https://api.biorxiv.org/details/{server}/{from}/{to}/{cursor}`

- cursor 是**整数页码**(每页 100 条),非 opaque token
- 标题/摘要含 JATS 标签(`<p>` `<i>` `<sub>` 等):入库前剥标签 + 解 HTML 实体
- API 不支持检索式:按日期窗拉取后,标题+摘要**本地匹配**短语组(含 refine AND 语义)
- **回填策略**:首次运行只回填 **7 天**(更早的预印本已由 Europe PMC `SRC:PPR` 覆盖),避免首次扫描拉数千条拖十几分钟;此后每次窗口见 §5

### 3.4 网络纪律(`sources/base.py` 统一实现)

- 每源串行,请求间隔 ≥1s;超时 30s;失败重试 2 次(指数退避)
- 单源最终失败:记 run 日志、继续其他源,**不中断整体扫描**
- User-Agent:`sm-scan/0.1 (local research tool)`
- 实现顺序注记:三个适配器的请求串先以 `--dry-run` 打真实验证(尤其 OpenAlex 布尔表达式的 URL 编码),再固化进 fixture(§14)

## 4. 检索式设计

### 4.1 内部 IR

```python
Term(
  phrase: str,          # 小写短语
  scope: str = "title_abs",   # title_abs | title(歧义缩写限定仅标题)
)
TermGroup(id, name, enabled, terms: list[Term], refine: list[str] /*组内 AND 短语*/)
```

GUI 直接编辑 IR,不碰各源语法。短语的匹配语义统一为**字段(scope)内不区分大小写包含该短语**。

### 4.2 默认三组(方案C,GUI 可增删改)

**组1「植物+空间成像」(核心,默认开)** —— 日报置顶档:

```
terms(scope=title_abs): mass spectrometry imaging, MALDI imaging, MALDI-MSI,
  MALDI mass spectrometry imaging, imaging mass spectrometry, spatial metabolomics,
  spatially resolved metabolomics, DESI-MS, desorption electrospray,
  TOF-SIMS, secondary ion mass spectrometry
refine(AND): plant, plants, Arabidopsis, Oryza, rice, maize, wheat, soybean,
  medicinal plant, plant tissue, leaf tissue, root exudate, phytochemistry,
  natural product, secondary metabolite
```

歧义缩写(`MSI`、裸 `DESI`、裸 `SIMS`)**不作为独立短语**——只能以无歧义复合词或全称入组;若未来确需歧义缩写,置 `scope="title"` 走仅标题分支。

**组2「MALDI/MSI 方法学」(默认开)** —— 全领域方法与仪器进展:

```
terms(scope=title_abs): MALDI, matrix-assisted laser desorption ionization,
  matrix assisted laser desorption ionization, MALDI imaging, MALDI-MSI,
  mass spectrometry imaging, imaging mass spectrometry, spatial metabolomics,
  MALDI-TOF imaging
refine: (无)
```

⚠️ 已向用户明示:裸 `MALDI` 使本组每日约 20-50 篇(含微生物鉴定、蛋白质等非成像方向),靠标签+日报分档消化;跑一两周后可在 GUI 调窄(如给组2 也加 refine),不改代码。

**组3「产物注释」(默认开)**:

```
terms(scope=title_abs): metabolite annotation, metabolite identification,
  natural product annotation, spectral annotation, metabolite structure elucidation
refine(AND,必填): mass spectrometry, MS/MS, LC-MS, MALDI, tandem mass spectrometry
```

refine 在组3 为**必填**:GUI 清空组3 的 refine 时弹警告阻止保存(清空即噪音爆炸)。

### 4.3 三源翻译规则

| 源 | 组内 | 组间关系 | 细节 |
|---|---|---|---|
| Europe PMC | 短语→`(TITLE_ABS:"p1" OR TITLE_ABS:"p2" ...)`,scope=title 的短语用 `TITLE:"p"`;refine 短语括号 AND;再 AND `FIRST_IDATE:[from TO to]` | **并集**(组是并列兴趣主题),去重靠 DOI | 查询串超长按短语分批拆多次请求,结果并集 |
| OpenAlex | 每组一条 `search.title_and_abstract=("p1" OR "p2" ...) AND (r1 OR r2 ...)`;日期用 `filter=from_publication_date:...` | **并集**,去重靠 DOI | 一组一请求;8190 字节超限分块 |
| bioRxiv | 日期窗全量拉取,标题+摘要本地匹配 terms(OR);refine 本地 AND | **并集**,去重靠 DOI | 页码游标翻页 |

## 5. 去重与增量

- **主键**:DOI 归一化(小写、剥离 `https://doi.org/` `doi:` `info:doi/` 前缀)。无 DOI 条目用标题规范化哈希(小写、去标点、压空白、SHA1 前 16 位)兜底,键格式 `titlehash:<hash>`。
- **扫描窗口**:

```
from = last_ok_run.finished_date - 3d      # 锚定上次成功 run 的结束日,重叠只容索引滞后
to   = today
```

  无成功 run 时:`from = today - backfill_days`(默认 30,bioRxiv 源例外只回填 7 天,见 §3.3)。窗口**不会**随轮次向前扩张。
- 已见主键:更新元数据(摘要可能从无到有)并刷新 `updated_at`,但不重复出现在"新论文"报告;新主键入库、`status='unread'`、记 `first_seen`。
- **时区**:全部时间戳用**本地时区**,`first_seen`/`started_at`/`finished_at` 均为本地 ISO 格式;日报文件名取扫描开始时刻的本地日期,跨零点 run 归入起始日。

## 6. 数据库 schema(SQLite,WAL 模式)

```sql
CREATE TABLE papers (
  id            TEXT PRIMARY KEY,     -- 归一化 DOI 或 titlehash:<hash>
  doi           TEXT,
  pmcid         TEXT,                 -- Europe PMC core 直接返回,为将来一键下载全文留位
  title         TEXT NOT NULL,
  authors       TEXT,                 -- 展示串("et al." 截断)
  journal       TEXT,
  pub_date      TEXT,                 -- YYYY-MM-DD(缺失存年月/年);取 FIRST_PDATE
  indexed_date  TEXT,                 -- Europe PMC FIRST_IDATE(OpenAlex/bioRxiv 可空)
  source        TEXT NOT NULL,        -- first_source:首次发现源
  sources       TEXT DEFAULT '[]',    -- JSON 数组:历次命中的所有源(评估各源增量价值)
  url           TEXT,
  abstract      TEXT,
  tags          TEXT DEFAULT '[]',    -- JSON 数组
  status        TEXT DEFAULT 'unread',-- unread | read | archived
  starred       INTEGER DEFAULT 0,
  to_read       INTEGER DEFAULT 0,
  llm_digest    TEXT,
  first_seen    TEXT NOT NULL,        -- 本地时区 ISO
  updated_at    TEXT
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

CREATE TABLE term_groups (   -- 检索式 IR 持久化,GUI 直接编辑
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  enabled INTEGER DEFAULT 1,
  terms TEXT NOT NULL,       -- JSON: [{phrase, scope}]
  refine TEXT NOT NULL       -- JSON 数组;组3 语义必填(GUI 校验)
);

CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
-- 已知键:schema_version, llm_base_url, llm_api_key(DPAPI 密文), llm_model,
--        src_europepmc_enabled, src_openalex_enabled, src_biorxiv_enabled,
--        openalex_api_key(DPAPI 密文,可空), backfill_days
```

**标记模型**:`status`(阅读进度)与 `starred`/`to_read`(两个独立旗标)分维度——「已读且收藏且待精读」可并存,互不覆盖。

**并发**:GUI 读列表与后台扫描线程并发,SQLite 开 WAL;写操作短事务,锁则重试。

**迁移**:`schema_version` 存 settings;启动时比对、按版本顺序执行迁移(为三源 API 与检索式演化预留)。

## 7. 标签判定(`tags.py`,入库时执行)

| 标签 | 规则(标题+摘要,不区分大小写) |
|---|---|
| `成像/空间` | 命中 `imaging, spatial, MSI, mass spectrometry imaging, mapping` 之一 |
| `植物` | 命中 `plant, plants, Arabidopsis, Oryza, rice, maize, wheat, soybean, Medicago, Nicotiana, Paeonia, medicinal plant, phytochemistry, root exudate, leaf tissue, plant tissue, secondary metabolite, natural product` 之一。**不做** `tissue/root/seed/flower` 等无判别力单词匹配(医学语境误报过多) |
| `注释` | 命中**入库当时**组3 任一短语(快照语义:事后改组3 不回溯已入库论文标签) |

规则表硬编码于 `tags.py` 顶部常量(进 GUI 配置是过度设计)。

## 8. LLM 中文导读(OpenAI 兼容接口)

**默认关**。开启路径:GUI 逐篇/批量按钮,或 CLI `--llm`。

**配置三项**(GUI 视图3 填写,存 settings 表):

- `llm_base_url` — 如 `https://open.bigmodel.cn/api/paas/v4` 或自建/本地端点
- `llm_api_key` — **Windows DPAPI 加密存储**(ctypes 调 crypt32.dll `CryptProtectData`,绑定当前 Windows 用户;密文落库,拷走 db 文件到他机不可解)
- `llm_model`

**请求**:标准 OpenAI chat completions——

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

- base_url 归一化:不以 `/chat/completions` 结尾则自动拼接(填到 `/v4` 或 `/v4/chat/completions` 均可)
- 超时 60s,重试 1 次;逐篇串行;单篇失败跳过并计数,不中断批量
- 结果写 `papers.llm_digest` 持久缓存,已有导读不再调用
- GUI 密码框不回显明文,任何日志不落 key 明文

## 9. GUI 设计(PySide6)

主窗口 = 左侧固定导航栏(4 项)+ 右侧 `QStackedWidget` 视图区。

### 视图1:论文列表(默认视图)
- 顶部筛选条:日期范围(QDateEdit ×2)、来源下拉、状态下拉、星标/待精读过滤、标签下拉、关键词搜索框(匹配标题)
- 表格列:日期 | 标题 | 期刊 | 来源 | 标签 | 状态旗标(已读/★/待);默认 pub_date 降序,表头可排序
- 单击行 → 详情面板:完整题录、摘要原文(可滚动)、DOI 链接(系统浏览器)、LLM 导读(有则显示,无则"生成导读"按钮)
- 右键菜单:标记已读/未读/星标/待精读(独立开关)、复制 DOI、浏览器打开、生成导读

### 视图2:标记管理(独立页,非过滤快捷页)
- 复用视图1 表格组件,提供**交叉筛选**:状态 × 星标 × 待精读 × 标签 × 日期 的任意组合
- **批量操作**:选中多行后批量切换已读/星标/待精读/归档
- **批量导出**:当前筛选结果或"待精读"全集导出 `reports/todo-reading-YYYYMMDD.md`(标题+DOI+导读),衔接精读工作流

### 视图3:检索式配置
- 关键词组列表(组名、开关、短语数);选中组编辑:短语列表(每行一条,可逐条设 scope)、refine 短语、启用开关;增删改即时存 `term_groups`
- 组3 的 refine 清空时弹警告阻止保存(§4.2)
- 数据源开关(三个复选框)、OpenAlex API key(密码框,DPAPI)、回填天数
- LLM 配置区:base_url / api_key(密码框)/ model + "测试连接"(发一条 ping 验证)

### 视图4:扫描中心
- 当前配置摘要 + **立即扫描**(QThread,进度条+滚动日志,完成刷新)
- 定时任务区:任务计划状态 + 注册/删除按钮(§10)
- 历史报告列表:`reports/*.md` 按日期列出,点击用系统默认程序打开
- 批量导读:对最新 N 天未读论文批量生成(进度显示)

## 10. Windows 任务计划集成(`task.py`)

- 数据路径已绝对化(§2),**不依赖 cwd**;注册命令:

```bat
schtasks /Create /TN sm-scan-daily ^
  /TR "\"<pythonw.exe 完整路径>\" -m sm_scan scan" ^
  /SC DAILY /ST 09:00 /F
```

- `sys.frozen` 为真(打包版)→ /TR 指向 `sm-scan.exe scan`;否则指向 `pythonw -m sm_scan scan`(pythonw 取 `sys.executable` 同目录)——判定逻辑封装在 `task.py` 内,两种形态都可注册
- `remove`:`schtasks /Delete /TN sm-scan-daily /F`;`status`:`schtasks /Query`
- **防重入锁** `data/last_run.lock`(pid+时间戳):已持锁时后到者直接退出记日志;**stale 自动清理**——锁文件 pid 不存在或超过 6 小时即视为残留,覆盖并继续

## 11. CLI 接口

```
python -m sm_scan scan [--days N] [--llm] [--dry-run]   # 扫描;dry-run 打印将请求的 URL/查询串与窗口,不落库
python -m sm_scan gui                                    # 启动 GUI
python -m sm_scan report [--date YYYY-MM-DD]             # 重新生成某日报告
python -m sm_scan task register|remove|status            # 定时任务管理
```

`scan` 退出码:0=ok,2=partial(有源失败),3=failed。日志 `data/scan.log`(滚动 1MB×3)。

## 12. 日报格式(`reports/YYYY-MM-DD.md`,三档分档)

```markdown
# sm-scan 日报 2026-11-06
新增 23 篇(Europe PMC 15 / OpenAlex 6 / bioRxiv 2)| 组1✓ 组2✓ 组3✓
分档:核心(植物+成像)4 | 方法学 15 | 其他 4

## 一、核心:植物+空间成像(4)
### [2026-11-05] Title — *Journal* `植物` `成像`
作者串 | [DOI](https://doi.org/...) | 来源 europepmc
> **导读**:…(无导读时显示摘要前 300 词)

## 二、方法学:MALDI/MSI(15)
...

## 三、其他(4)
...

---
## 待精读(2,累积状态,与前文分档有重叠)
...
```

- 分档依据:命中组1(refine 通过)→ 一档;未入一档但命中组2/「成像/空间」标签 → 二档;其余 → 三档
- 摘要截取**前 300 词**(约 1200-1500 字符,英文摘要可读出结论;原 300 字太短)
- 「待精读」为跨日累积状态,首行计数与分档不对应,已注明防误读

## 13. 错误处理

| 场景 | 行为 |
|---|---|
| 单源 API 失败(含 Europe PMC HTTP 200 + errCode) | 重试 2 次后放弃该源,run 记 partial,报告注明;`resultList` 缺失显式识别为失败 |
| 网络断网 | 三源全失败 → run 记 failed,退出码 3;GUI 弹提示 |
| LLM 未配置就触发 | 明确报错"请先在检索式配置中填写 LLM 三项",不静默 |
| LLM 单篇失败 | 跳过+计数,批量继续 |
| DB 被锁 | WAL 下罕见;写事务重试 3 次(间隔 200ms)后报错 |
| 任务计划重复注册 | `/F` 覆盖并提示 |
| 锁文件残留(进程崩溃) | §10 stale 清理(pid 不存在 / >6h) |

## 14. 测试策略(pytest)

引擎层单测(离线,mock HTTP + 真实响应 fixture):

1. **DOI/标题归一化**:前缀剥离、大小写、无 DOI 标题哈希稳定
2. **检索式翻译**:IR → Europe PMC 查询串(TITLE_ABS/TITLE scope、FIRST_IDATE、refine AND)/ OpenAlex 布尔串(URL 编码、8190 分块)/ bioRxiv 本地过滤;组间并集
3. **增量窗口**:首跑回填(30 天,bioRxiv 7 天)、锚定 finished_date -3d 不随轮次扩张、已见 ID 不重复计数
4. **Europe PMC 解析**:`resultType=core` 缺失 → 无摘要(防回归);HTTP 200 + errCode 识别为失败;cursorMark 翻页
5. **OpenAlex 解析**:倒排索引重建摘要;cursor 翻页
6. **bioRxiv 解析**:JATS 剥标签 + HTML 实体;整数页码翻页
7. **标签判定**:植物词表边界(soft tissue 不标植物、Arabidopsis 标)、空摘要
8. **LLM 客户端**:base_url 两种填法归一化、失败重试、缓存不重调、DPAPI 加解密往返
9. **报告生成**:固定输入 → md 快照(分档、待精读重叠注明)

GUI 不写自动化测试(PySide6 自动化成本>收益),交付前按验收清单手动过。

## 15. 打包

- PyInstaller **onedir**(启动快):产 `sm-scan-gui.exe`(含 GUI)与 `sm-scan.exe`(纯 CLI,供任务计划)
- 产物 `dist/sm-scan/` 整目录拷走即用;数据目录按 §2 打包态定位
- 打包脚本进仓库

## 16. 非目标(本期不做)

- 自动下载 PDF / 与 `spatial-metabolomics-papers` 的导入联动(`pmcid` 列已留位)
- 邮件/IM 推送;相似论文推荐、引用图;多用户/网络服务化;WoS/Scopus

## 17. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 裸 `MALDI`(组2)噪音 20-50 篇/天 | 日报三档分档置底;GUI 调 refine;已明示用户 |
| 歧义缩写(MSI=微卫星不稳定等) | IR 强制 scope 机制;歧义缩写仅可 `scope=title` 或以复合词入组(§4.2) |
| OpenAlex 布尔语法/URL 编码边界 | 实现首日用 `--dry-run` 实测,响应固化进 fixture;失败不阻塞其他源 |
| Europe PMC / OpenAlex 查询串超长 | 均按短语分块拆分请求,结果并集(§4.3) |
| bioRxiv 首次回填过慢 | 回填窗口只 7 天 + 页码游标 + 1 req/s 限速下预计 ≤2 分钟;UI/日志显示进度 |
| PySide6 + PyInstaller 体积/插件坑 | onedir + 常规 Qt 插件排除,打包脚本进仓库 |
| DPAPI 密文跨机不可解 | 属预期(绑定用户);README 注明迁移需在 GUI 重新输入 key |

## 18. 交付物清单

1. 完整源码 + 测试(§2 结构)
2. `pyproject.toml`(依赖:`pyside6, requests, pytest`;Python ≥3.10;DPAPI 走 ctypes 零额外依赖)
3. 打包脚本 + README(安装、注册定时任务、OpenAlex key 可选申请、LLM 配置)
4. **验收**:三源 `--dry-run` 实测请求串 → 首次回填入库 → 生成首份三档日报 → GUI 四视图手动过单 → 注册任务计划次日自动跑通

## 19. 修订记录

| 日期 | 版本 | 摘要 |
|---|---|---|
| 2026-11-06 | v1 | 初稿 |
| 2026-11-06 | v2 | 按第 01 轮评审修订:P0-1 窗口锚定结束日;P0-2 FIRST_IDATE/FIRST_PDATE 分工;P0-3 core/TITLE_ABS/cursorMark/errCode;P0-4 OpenAlex 新语法+key 可选(官方文档已核实);P0-5 数据路径绝对化+sys.frozen;P0-6 组间并集统一;P0-7 scope 机制(裸 MSI 本就不在短语表,评审所指与原文有出入,约束未落地属实);P0-8 植物词表重写;P1-1 方案C 三组;P1-2 status 拆三维;P1-3 sources 数组;P1-4 视图2 恢复完整功能;P2×15 全部采纳(倒排索引/JATS/回填 7 天/时区/锁清理/pmcid/schema_version/updated_at/日报三档与 300 词/表述/DPAPI/refine 必填/测试补条) |
