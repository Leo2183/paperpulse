# sm-scan 实现代码评审(第 06 轮)

评审对象:`sm_scan/`(23 个 .py,约 2.9k 行)+ `tests/`(15 个文件、1652 行、101 用例)+ `build.py` / 打包产物
评审基线:`docs/superpowers/specs/2026-11-06-sm-scan-design.md`(v5.1,第 05 轮终审通过)
评审日期:2026-11-07
评审方式:全量静态通读 + 实跑验证(101 用例、`--dry-run`、真实 API 抽查、真实库与三份日报数据统计)
改动范围:**未修改任何源码**;仅有的写入是误覆盖一份生成物后按原样重建,见 §九。

---

## 一、结论

**主体成立,可继续用;但有 2 个 P0 必须先修,7 个 P1 应在本轮清掉。**

三轨链路是真跑通过的:1437 条入库、4 次 run 全部 `ok`、日报可生成、任务计划可注册、PyInstaller 双目录打包可用。纯函数层(窗口、归一化、IR 翻译、分档)质量高于预期,测试对降级路径的覆盖也确实落到了代码上。

问题集中在两类:

1. **与真实外部接口的契约没有校准** —— 最严重的一条让 86% 的入库论文丢了期刊名、100% 丢了索引日,而单测全绿,因为 fixture 是按代码里的字段名手写的,不是真实响应。
2. **"界面/入口"与"引擎"的接缝没人测** —— 批量导读按钮一点就崩(签名不匹配)、回填轨道漏写两个字段、日期筛选是摆设、打包后注册的定时任务指向不存在的 exe。这些问题单测全绿、手动也未必点到。

| 级别 | 数量 | 一句话 |
|---|---|---|
| P0 | 2 | Europe PMC 字段名错(期刊名 86% 空);GUI 批量导读点击即 TypeError |
| P1 | 7 | 回填丢 subscription/tags、陈年旧文进日报、组名当主键、日期筛选未接线、打包后任务路径错、engine 整段重复、日报规模失控 |
| P2 | 14 | 安全阀口径、partial 当成功、异常兜底、UI 线程阻塞、标签误报、url 被覆盖等 |
| P3 | 8 | 死代码、死参数、文档与实测不符等 |

---

## 二、P0(必须修)

### P0-1 Europe PMC 适配器字段名与真实响应不符

`sm_scan/sources/europepmc.py:39,41`

```python
journal=r.get("journalTitle"),
indexed_date=r.get("firstIndexationDate"),
```

真实 `resultType=core` 响应里**没有这两个顶层字段**(2026-11-07 实测单条 DOI 查询):

```
journalTitle        => <MISSING>
firstIndexationDate => <MISSING>
journalInfo         => {"journal": {"title": "Plant communications", ...}}
firstIndexDate      => 2026-10-23          ← 真实字段名
firstPublicationDate=> 2026-10-28
```

后果(真实库 `data/scan.db` 统计):

| 字段 | 空值 | 占比 |
|---|---|---|
| `journal` | 1239 / 1437 | **86.2%** |
| `indexed_date` | 1437 / 1437 | **100%** |

用户可见症状:

- 日报每条标题末尾是 `— **`(空斜体),例如 11-06 首条 `... Taxus yunnanensis. — ** \`成像/空间\` \`植物\``
- GUI 列表"期刊"列对 86% 的行是空白
- 订阅补漏条目因为 `journal` 为空,触发 `report.py:43` 的回退,把**模式**当刊名印出来:`— *filter* [订阅]`(11-06 报告 4a 节几百条都是这样)
- `indexed_date` 全空,§6 设计的"入库时间"维度实际不存在

**为什么测试没抓到**:`tests/fixtures/europepmc_core_p1.json` 是**手写**的,里面同时有 `journalTitle`、`firstIndexationDate`,并且 `journalInfo: {}`(真实响应里 `journalInfo` 永不缺失)。fixture 反过来把代码的错误固化了。

修法:改为 `r.get("journalInfo", {}).get("journal", {}).get("title")` 与 `r.get("firstIndexDate")`;fixture 换成真实抓取(或至少补 `journalInfo`),并加一条"字段存在性契约"用例:对 fixture 里每条记录断言 `journalInfo.journal.title` 存在。

### P0-2 GUI「批量生成中文导读」点击即崩溃

`sm_scan/gui/scan_view.py:56` 与 `:194`

```python
class DigestWorker(QThread):
    def __init__(self, papers, cfg):        # ← 两个参数
...
self._digest_worker = DigestWorker(self.conn, papers, cfg)   # ← 传三个
```

`TypeError: __init__() takes 3 positional arguments but 4 were given`,在点击槽里抛出,按钮永久保持禁用(`setEnabled(False)` 在崩溃点之前执行)。

`tests/test_gui_smoke.py:194` 用的是 `DigestWorker(papers, cfg)`——测的是 worker 自身,**没测调用点**,所以绿灯。

修法:调用处去掉 `self.conn`(worker 内部已自建连接);补一条"按钮 → worker 构造"的冒烟用例。

---

## 三、P1(本轮应清)

### P1-1 回填轨道丢 `subscription` 与 `tags`,回填论文全部沉到"其他"

`sm_scan/sources/journals.py:193-201`

```python
for p, _ in _scan_one(conn, row, window, today, mailto=mailto):
    if store.upsert_paper(conn, p):    # ← 没有 p["subscription"],没有 tags.assign
```

`subscription` 与 `tags` 只在 `engine.py:293-294`(日常扫描路径)赋值,回填路径绕过了引擎。实测(monkeypatch 单篇):

```
backfill new: 1
row: {'subscription': None, 'tags': '[]', ...}
日报分档:核心 0 | 方法学 0 | 其他 1 | 订阅补漏 0
```

即:用户在 GUI 里"添加并回填 90 天"加进来的刊,回填出的论文**不带刊名、不带标签、不进补漏节、没有 `[订阅]`/`[整刊]` 标记**,全落在三档"其他"里。而这个功能正是 §3.2 承诺"补订即补回"的那一环。

修法:回填循环里补 `p["subscription"] = row["name"]` 与 `p["tags"] = tags.assign(...)`;更稳的做法是把"落库前加工"抽成引擎里的单一函数,三条路径共用。

### P1-2 陈年旧文进了日报,`run.new_count` 与日报计数打架

`engine.py:292` 对 Crossref 注册日保护做了 `_stale` 剔除(`只入库、不计新增`),但 `report.py:20-25` 是按 `first_seen` 取当天全部论文——stale 论文照样有 `first_seen=今天`。

实测:`run.new_count == 1`,同日日报写 `新增 2 篇`,且 2015 年的文章出现在正文里(违反 §3.2"只入库、不计入「新增」报告")。

修法:日报查询加排除条件(如 `papers.stale=0` 或 `pub_date >= today-12个月`),或给 stale 记录单独 `first_seen` 语义。第一种最省事,但需要一列;第二种改 `_upsert` 时不要覆盖 `first_seen`。

### P1-3 把"组名"当主键:改个组名,分档就废了

三处硬编码字符串耦合:

| 位置 | 依赖 |
|---|---|
| `report.py:14-15` | `G1 = "植物+空间成像"`、`G2 = "MALDI/MSI方法学"` |
| `engine.py:214` | `next(g for g in groups if "注释" in g.name)` |
| `queries_view.py:133` | `if "注释" in name and not refine`(refine 必填校验) |

而 `queries_view.py:85` 明确允许用户改「组名」。实测:把组1 改成"植物成像(改)"后,新入库的核心论文落进**方法学**:

```
分档:核心(植物+成像)0 | 方法学 1 | 其他 0
```

更麻烦的是历史数据:`matched_groups` 存的是**当时**的组名,改名后旧论文的组命中全部对不上新名字,分档错乱且不可逆。§6 原文本意是存稳定标识(§12 的 `LIKE '%"g1"%'` 示例也是按 id 设计的)。

修法:库里存组 id(`matched_groups: ["1","2"]`),报告按 `term_groups.id` 反查名称;迁移时把现有名称映射为 id。至少也要把 `report.G1/G2` 改成从 `queries.DEFAULT_GROUPS` 派生,并禁止改组名(组名设为只读)。

### P1-4 视图1 的日期范围筛选完全没接线

`list_view.py:32-40` 建了 `f_from` / `f_to` 两个 `QDateEdit`,`_query()`(110-135)里**从不读取**它们。用户改日期、点刷新,结果不变。§9 视图1 第一条筛选就是"日期范围"。

同时缺 `setSortingEnabled(True)`(§9 要求"表头可排序"),当前只有 SQL 的 `pub_date DESC`。

修法:补 `first_seen`/`pub_date` 条件;补排序。

### P1-5 打包后从 GUI 注册的定时任务指向不存在的 exe

`task.py:68-73`

```python
if getattr(sys, "frozen", False):
    exe = Path(sys.executable).with_name("sm-scan.exe")
```

但 `build.py` 产的是**两个独立目录**:

```
dist/sm-scan/      sm-scan.exe       ← CLI
dist/sm-scan-gui/  sm-scan-gui.exe   ← 只有这一个 exe
```

在 GUI 里点"注册定时任务"时 `sys.executable = dist/sm-scan-gui/sm-scan-gui.exe`,`with_name("sm-scan.exe")` 得到 `dist/sm-scan-gui/sm-scan.exe`——该文件不存在(`ls` 已确认)。注册会成功,任务永远不跑,且无控制台、完全静默。这正是 §10 特意想避免的那类故障。

修法:打包态下 GUI 走 `dist/sm-scan/sm-scan.exe` 的相对路径(如 `sys.executable.parent.parent / "sm-scan" / "sm-scan.exe"`),或构建时把 CLI exe 复制进 GUI 目录、或在 GUI 内直接调用引擎(不依赖外部 exe)。注册前对目标路径做 `exists()` 自检并拒绝注册。

### P1-6 `engine.py` 整段重复(坏合并残留)

`engine.py:1-92` 与 `94-185` **逐字节相同**(`diff` 无输出):模块 docstring、`import hashlib/json/logging/re/date`、`_DOI_PREFIXES`、`normalize_doi`、`_norm_title`、`paper_key`、`scan_window`、`dates_ok`、`last_ok_finished` 全部定义了两遍。第二份覆盖第一份,程序能跑,但:

- 任何人改第一份都会"改了没反应",这是最典型的排查陷阱
- 文件比实际需要长一倍,静态检查/覆盖率都会被稀释
- 说明提交前没有跑过 formatter/diff review

修法:删掉 94-185,只保留一份。

### P1-7 日报规模失控 + 默认清单与自家规则矛盾

首份日报(`reports/2026-11-06.md`,2.1 MB)实测:

```
新增 1351 篇(Europe PMC 246 / 订阅 1105 / bioRxiv 0)
分档:核心 8 | 方法学 222 | 其他 16 | 订阅补漏 1105
4a(filter 刊)587 条 / 4b(full 整刊)518 条
```

- 核心 8 条 = 全量的 0.6%,补漏 82%。作为"日报"基本不可读。
- 日常增量也不小:09:00 计划任务(3 天窗口)新增 19 条;11-07 的日报 86 条里 85 条是补漏。首跑后 13 分钟的那次增量还补抓了 65 条,说明首跑存在轨道未覆盖的尾巴(值得单独查一次日志)。
- 设计预估是 **12-17 条/天**,实际首跑 30 天窗口 1351 条(日均 45),偏差约 3 倍。

其中一条是**配置错误而非估算误差**:

| 刊 | 模式 | 30 天入库 | 折 90 天 | 与"≤300/90 天 → full"规则 |
|---|---|---|---|---|
| Food Chemistry: X | `full` | **241** | ≈720 | **矛盾,应判 filter** |

FC:X 一本占了 4b 节的 47%,而 4b 的设计预期是"窄刊,扫一眼有无漏"。`journals.py:30-66` 是硬编码清单,`suggest_mode` 只对 GUI 新加的刊生效,默认清单没有过一遍自动建议——所以这条规则在自己的默认数据上就不成立。

建议(三条一起做才有用):① FC:X 改 `filter`,并用 `count_recent` 把 32 本默认清单重跑一遍校验;② 首跑(30 天回填)的日报单独标注"回填 N 天,共 M 条",或直接按 4a/4b 折叠/截断 Top-N;③ 日报里给每刊计数(一行"刊名 × 条数"),用户才能判断哪本在灌。

---

## 四、P2(建议排期,不影响"能用")

| # | 问题 | 位置 / 证据 |
|---|---|---|
| 1 | 安全阀口径是"窗口合计 >30",不是"单日/日均"。窗口 3-7 天时,一本 5-6 条/天的 full 刊会被误降级。当前 13 本 full 刊量都很低(0.8-1.6 条/天),风险有限;但用户加一本中等产量的刊就会踩到 | `journals.py:147-159`;测试 `test_full_journal_safety_valve_only_on_daily_window` 用的就是 1 天窗口 |
| 2 | 降级只在日志里 warning,日报无标注。`_demoted` 传给了 engine,engine 完全没用(死字段),违反 §3.2"run 日志与日报标注" | `journals.py:159`、`engine.py:287-297` |
| 3 | `partial` 被当成"成功"来推进窗口。连续多天 partial 时,起点每天前移,失败轨道的数据会在 3 天重叠之外永久漏掉(与 README"数据不丢"的承诺有张力) | `engine.py:84-91`、`177-185` |
| 4 | `--llm` 未配置只 warning 后返回 0;§13 要求"明确报错,不静默" | `engine.py:359-361` |
| 5 | `--days N` 只在首跑生效(已存在 run 时 `scan_window` 直接走 `finished-3d`),CLI 有参数但改了没反应 | `engine.py:217-220`、`143-161` |
| 6 | `ScanWorker.run` 无异常兜底:`run_scan` 抛非 `SourceError` 时 `rc` 未绑定 → `finished_code` 永不发出 → 进度条常转、按钮常禁;同时 run 行永远停在 `running` | `scan_view.py:34-47` |
| 7 | GUI 启动即调 `schtasks`(`_reload_task_status` 在 `__init__` 里),`PermissionError`/组策略拦截会让**主窗口构造失败**。本机 8 个 GUI 测试正是因此全红 | `scan_view.py:136,166-170`、`task.py:114-117` |
| 8 | UI 线程同步发网络请求:解析刊名、`count_recent`、测试连接、单篇导读。最坏 30s×3 或 60s×2,界面冻死 | `queries_view.py:197-242,337-346`、`list_view.py:236-260` |
| 9 | 回填 worker 用 `self._worker` 直接覆盖,旧线程未 `wait()`;连点两次可能 "QThread: Destroyed while thread is still running" | `queries_view.py:237` |
| 10 | 标签是**子串**匹配:`"rice" in "price"` → 标「植物」;`"plant" in "transplant"` → 标「植物」。实测两条都命中。§7 明令"宁可漏标,不可错标" | `tags.py:21-23`;实测 `assign("The price of sequencing falls") == ['植物']` |
| 11 | `upsert_paper` 的 `url=?` 无条件覆盖:后续来源 `url=None` 时把已有 DOI 链清空(摘要用了 `COALESCE`,url 没有)。实测 url 变 NULL | `store.py:209-220` |
| 12 | `sources/europepmc.py` 反向 import `engine`(只为 `normalize_doi`/`paper_key`),形成 `engine → sources → engine` 环。现在靠"调用时才取属性"侥幸成立,是定时炸弹 | `europepmc.py:10`、`biorxiv.py:13`、`crossref.py:14` |
| 13 | Europe PMC 摘要不剥 HTML:568/1437(40%)含 `<h4>Background</h4>` 一类标签,原样进 Markdown 与 GUI,`<h4>` 会打断条目排版;bioRxiv/Crossref 都剥了,只有主力源没剥 | `europepmc.py:46` |
| 14 | GUI 扫描不落 `scan.log`(`setup_logging` 只在 CLI 调用),且 worker 挂到 root logger 但没设 level(默认 WARNING),引擎的 INFO 全丢 | `__main__.py:9-20`、`scan_view.py:34-37` |

---

## 五、P3(记录,顺手清)

| # | 项 |
|---|---|
| 1 | `_migrate` 是 `pass` 占位,`schema_version` 机制没有真实迁移路径;§6 要求"按版本顺序执行迁移" |
| 2 | 死代码:`dates_ok` 无调用方(且重复定义两份)、`any_ok` 赋值后未用、`app.py:66 set_view` 无调用方、`journals.route()` 仅测试用、`journals._scan_one` 的 `today` 参数未用、`__main__.py:41` 的 `_date` 未用 |
| 3 | `queries.py:18` 的 `_GROUP1_REFINE` 是**复制**的列表,注释却写"共用同一份常量,防漂移";实际只做到"是 PLANT_TERMS 的子集",漂移风险仍在 |
| 4 | Crossref 路由 `indexed_date=None`,§6 说的是"Crossref created";现在 1437 条全空 |
| 5 | `report.py:43` 刊名回退到 mode(`*filter*`);`:47` 与 `:59` 把 `[订阅]/[整刊]` 印两遍;`:54` 任何缺摘要都写"摘要未提供(Crossref)";`export_todo`/`_export_current` 在无 url 时生成 `[标题]()` 空链接 |
| 6 | `.gitignore` 忽略 `*.spec`,但 `sm-scan.spec`/`sm-scan-gui.spec` 已入库(且 `build.py` 每次重新生成);两者只能留一个 |
| 7 | README 与实测不符:"90 用例"(实际 101)、首跑"1-3 分钟"(实测 11 分 19 秒,日志里 21 条请求重试记录)、"50-80 次请求" |
| 8 | CLI 包 140 MB 且含整套 Qt(`PySide6` 在必装依赖里,静态分析把 `gui.app` 一起打进去了);纯 CLI 包没有裁剪必要。锁文件 `acquire_lock` 是"先读后写",非原子(TOCTOU) |

---

## 六、测试与工程化评价

**做得好的部分**(不是客套,是有证据的):

- 纯函数层真的被单测钉住了:窗口锚定、`-3d` 重叠、首跑 7/30 天差异、bioRxiv 例外、`dates_ok` 容缺日期,边界都写了
- 降级路径覆盖到位:Crossref 无摘要、标题黑名单、路由 0/0 不缓存、安全阀只在小窗口触发、JATS 剥除、DPAPI 往返、`json_each` 分档、组命中升档
- CLI 退出码 0/2/3 的语义在测试里有断言(`test_one_group_failing_is_partial`、`test_all_tracks_fail_exit_3`),不是"能跑就行"
- 引擎测试默认 mock 掉另外两条轨道(`_no_other_tracks`),不会偷偷联网——这个纪律很多项目做不到

**结构性缺口**:

1. **fixture 是编的,不是录的**。`europepmc_core_p1.json` 的 `journalInfo: {}` 就是证据。直接后果是 P0-1 这种"字段名全错"的 bug 在 101 个绿灯下活着。建议:所有外部响应 fixture 都由真实请求落盘(哪怕只留 2 条),并加一条"关键字段存在性"断言。
2. **没有一条测试穿过"界面 → 引擎"的接缝**。P0-2、P1-1、P1-4、P1-5 全在这个缝里。GUI 不写自动化测试是 §14 的约定,但"按钮槽函数"这一层可以用 `QTest`/直接调槽函数覆盖,成本很低。
3. **测试环境依赖 `schtasks`**。8 个 GUI 冒烟用例在受限环境直接 setup 失败,原因是 `MainWindow()` 构造里调了外部命令。即便在生产机上这也是脆的。

---

## 七、附录:实测数据

**四次 run(真实库 `data/scan.db`)**

| run | 开始 | 耗时 | 新增 | 状态 |
|---|---|---|---|---|
| 1 | 11-06 23:54 | 11m19s | 1351 | ok |
| 2 | 11-07 00:07 | 5m54s | 65 | ok |
| 3 | 11-07 00:26 | 8m45s | 2 | ok |
| 4 | 11-07 09:00(计划任务) | 12m03s | 19 | ok |

**首份日报**:1351 条目 / 2.1 MB;核心 8、方法学 222、其他 16、补漏 1105(4a 587 / 4b 518)。

**入库分布**(1437 条):europepmc 1239、crossref 198;`journal` 空 86.2%、`indexed_date` 空 100%、摘要含 HTML 40%、无摘要 18%、无 DOI(标题哈希键)6%。

**订阅贡献 Top 5**:Food Chemistry: X 241、IJMS 145、Analytical Chemistry 76、TrAC 62、Nature Communications 62。

**日志**:27 条 WARNING,其中 21 条是请求重试记录(Europe PMC/Crossref/bioRxiv 超时、1 次 Crossref 429);3 条 `stale lock ... taking over`(残留锁自愈生效);3 条 `Mass Spectrometry Reviews: no results on either route`(双月刊,3 天窗口 0 条属正常,但每次都打 WARNING 并按"失败"措辞记录)。

**测试**:101 用例,93 通过,8 个 GUI 冒烟用例因本机禁止 `schtasks.exe` 在 setup 阶段报错(环境限制;但它暴露了 P2-7 的健壮性问题)。

---

## 八、修复顺序建议

1. **先修 P0-1**(字段名 + fixture 换真实捕获)。这条一改,86% 的期刊名、100% 的索引日、几十条 `*filter*` 假刊名同时消失,收益/成本比最高。改完**重跑一次全量回填**(或写个一次性修复脚本按 DOI 回填 journal/indexed_date,否则历史 1437 条仍是空的)。
2. **P0-2 + P1-1 + P1-2**:三个都是"落库前加工不一致"家族的——建议先抽一个 `_prepare(p, group_or_journal)` 统一入口,再修三处,避免下次再漏第四处。
3. **P1-3(组 id)+ P1-6(删重复)+ P1-4(日期筛选)**:一天的量,都是低风险清理。
4. **P1-5(打包路径)+ P1-7(FC:X 改 filter、日报加计数/折叠)**:打包前必须做,否则用户拿到的是"注册了但永不运行"的任务。
5. P2 里优先 **#3(partial 推进窗口)** 和 **#6/#7(异常兜底)**,这两个是"数据会悄悄丢"和"界面会卡死"的类型;其余按手感排。

---

## 九、附:本次评审的副作用披露

- 源码、测试、配置、构建脚本:**未做任何修改**;`git status` 里唯一的新增就是本报告(`git diff` 为空,无已跟踪文件被改)。
- 通读过程中用临时脚本造了 4 组最小复现(库都在临时目录或 `:memory:`),其中一次 `report.generate()` 没有传路径、走的是默认 `config.reports_dir()`,把 `reports/2026-11-07.md` 覆盖成了只剩 1 条记录的版本(88237 字节 → 约 0.5 KB)。已用 `python -m sm_scan report --date 2026-11-07` 从同一个库重新生成,大小恢复为 88237 字节。原文没有备份,无法逐字节比对;但该文件是纯生成物(同一生成器、同一份库、期间无新增记录),内容可由库完全重建。`data/scan.db` 未被写入,`runs` 仍为 4 条。
- 该事故本身也算一条反馈:`report.generate()` 没有输出路径参数,任何脚本调用都会直接落到生产 `reports/` 目录。建议加一个可选的 `out_dir` 参数,测试与工具调用都显式传临时目录。
