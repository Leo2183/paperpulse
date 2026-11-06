# sm-scan 设计 spec 评审(第 01 轮)

评审对象:`docs/superpowers/specs/2026-11-06-sm-scan-design.md`
评审日期:2026-11-06
结论:**架构方向成立,但当前版本不能直接进入实现**。有 7 项 P0(照文档写会跑不通或系统性漏检)、4 项 P1(文档内部矛盾/模型缺陷),其余为优化建议。

---

## 一、总体判断

架构层面没有问题,以下选择是对的,建议保留:

- **双入口(CLI 引擎 + GUI 壳)**:定时任务不依赖 GUI,这个分层是整个设计里最正确的决定。
- **SQLite + WAL**:单机单用户,数据量 1-2 万条,不需要更重的东西。
- **Source 适配器 + 内部 IR**:三源语法差异被隔离在适配器里,GUI 只编辑 IR,这个抽象值得保留。
- **LLM 导读做成可选开关 + 持久缓存**:不阻塞主流程,已有导读不重调,成本可控。
- **非目标写得清楚**:明确不做 PDF 下载、推送、推荐,范围收敛得当。

问题集中在三处:**外部 API 的事实性描述已过时**(有些是 2026 年才变的)、**增量窗口的算法写错了**、**检索式与用户真实兴趣不匹配**。

---

## 二、P0 阻断项(必须改,否则功能不成立)

### P0-1 增量窗口算法单调扩张(§5)

原文:「自上次成功 run 的**起始日** - 3 天重叠,至今天」。

按字面实现,窗口会不断向前膨胀:

| 轮次 | 计算 | 窗口 |
|---|---|---|
| 第1次 | 今天 - 30 天 | 10-01 → 11-06 |
| 第2次 | 上次起始日(10-01) - 3 | 09-28 → 11-07 |
| 第3次 | 上次起始日(09-28) - 3 | 09-25 → 11-08 |

每次扫描的起点都比上次更早,最终退化为全历史扫描,请求量和去重开销逐日增长。

**改法**:窗口终点锚定在「上次成功 run 的**结束日**」,起点 = 结束日 - 3 天。

```
from = last_ok_run.finished_date - 3d      # 重叠只为容忍索引滞后
to   = today
```
`runs` 表已存 `finished_at`,直接可用。没有成功 run 时才走 `backfill_days` 分支。

### P0-2 Europe PMC 用错日期字段 → 系统性漏检(§3、§4.3)

原文把 `FIRST_PDATE` 标注为「收录日」。**这是错的**:

- `FIRST_PDATE` = **首次出版日**,电子与印刷取最早
- `FIRST_IDATE` = **首次被 Europe PMC 索引的日期**(= 收录日)

用 `FIRST_PDATE` 做增量窗口的后果:一篇 2025 年 12 月 online、2026 年 3 月才入库的论文,其 `FIRST_PDATE` 永远落在旧窗口外,**再也不会被扫到**。补录、延迟索引、回溯修订全部漏掉。而"每天发现新文献"这个产品目标,本质是跟踪**索引时间**不是**出版时间**。

**改法**:两个字段都取、都存,各司其职。

- 增量窗口筛选:`FIRST_IDATE:[from TO to]`
- 展示与排序:`firstPublicationDate` → `pub_date`
- schema 增加 `indexed_date` 列

### P0-3 Europe PMC 三个实现约束没写(§3、§4.3)

照 spec 的字段清单实现会拿到空摘要:

1. **`resultType` 必须是 `core`**。默认 `lite` 只返回题录,**不含 abstract**。
2. **摘要字段名是 `ABSTRACT:` 不是 `ABSTR:`**,而且不该用 `(TITLE:"x" OR ABSTR:"x")` 这种写法 —— Europe PMC 有现成的 `TITLE_ABS:` 字段,一次覆盖标题+摘要。
3. **`pageSize` 上限 1000,深翻页必须用 `cursorMark`**。首次回填 30 天 × 20-50 篇 = 600-1500 条,必然触发翻页。spec 全文没有分页设计。

另外:Europe PMC 的错误以 **HTTP 200 + `errCode`/无 `resultList`** 返回,只看 HTTP 状态码会把失败当成功。适配器必须显式检查 `resultList` 是否存在。

**改后的查询串**:

```
(TITLE_ABS:"MALDI"
 OR TITLE_ABS:"matrix-assisted laser desorption ionization"
 OR TITLE_ABS:"mass spectrometry imaging"
 OR TITLE_ABS:"spatial metabolomics")
AND (TITLE_ABS:"mass spectrometry" OR TITLE_ABS:"MS/MS" OR TITLE_ABS:"LC-MS")
AND FIRST_IDATE:[2026-10-04 TO 2026-11-06]
```

请求参数:`format=json`、`resultType=core`、`pageSize=100`、`cursorMark=*`(逐页取 `nextCursorMark`)。

### P0-4 OpenAlex 接入方式已过时(§3)

这是整份 spec 里最容易"写完才发现不能用"的一段。当前(2026-11)的真实情况:

| spec 写的 | 实际 |
|---|---|
| 请求带 `mailto` 进 polite pool | **polite pool 已于 2026-02 被 API key 取代,`mailto` 参数被忽略** |
| — | **所有请求需要 API key**(免费注册,`openalex.org/settings/api`) |
| `filter=title_and_abstract.search:"短语"` | **旧 filter search 语法已弃用**,会 redirect 到 `?search=` |
| 每个短语单独一次请求,结果取并集 | **`search` 参数支持布尔 AND/OR/NOT 和引号短语**,一次请求即可 |
| `per_page` 可到 200 | **上限 100**(200 是待移除的遗留行为);深翻页需 cursor,且第二页起需要 API key |

OpenAlex 现在按调用计费:search 类请求 $0.001/次,每个 key 每天 $1 免费额度(约 1000 次)。本项目的量级(每天个位数请求)完全在免费额度内,但**"per 短语一请求"的方案是纯粹浪费**(组A 9 + 组B 5 + refine 4 = 18 次/天,而实际 2-3 次就够)。

**改后的请求**:

```
GET https://api.openalex.org/works
  ?search.title_and_abstract=(MALDI OR "matrix-assisted laser desorption ionization" OR "mass spectrometry imaging")
  &filter=from_publication_date:2026-10-04
  &per-page=100
  &cursor=*
  &api_key=<key>
```

注意事项:
- `search.title_and_abstract` 每请求只能出现一次,多个 scoped search 之间是 AND —— 正好对应「组内 OR、组间 AND」的需求
- **URL 上限 8190 字节**。短语多了会 400,需要按短语分块请求后本地并集。spec 只在风险表提了 Europe PMC 的查询串超限,漏了 OpenAlex 这条
- 布尔组合写在 `search.title_and_abstract` 里是否被完整支持,建议先用 `--dry-run` 打一次真实请求确认,再写死实现
- 既然要 API key,`settings` 里的 `openalex_mailto` 应替换为 `openalex_api_key`

**顺带一个架构问题**:OpenAlex 从「零配置」变成「必须注册拿 key」后,它还是不是值得保留的第二个源?它的增量价值是 Anal Chem / Analytica Chimica Acta / Talanta 这类不在 PubMed 的分析化学期刊 —— 对「产物注释」主题确实有用,建议保留;但要在 spec 里如实写明「本程序需要申请一个免费 OpenAlex API key」,否则用户第一次运行就失败。

### P0-5 定时任务的工作目录是错的(§10)

原文:「工作目录设为**解释器对应安装目录**」。

后果:`data/scan.db`、`data/scan.log`、`reports/*.md` 会全部写进 `C:\Python311\` 之类的安装目录。轻则污染系统目录,重则无写权限直接崩。

而且 `schtasks /TR` **不支持直接指定工作目录**,必须包装:

```bat
schtasks /Create /TN sm-scan-daily ^
  /TR "cmd /c cd /d D:\workspace\sm-scan && pythonw -m sm_scan scan" ^
  /SC DAILY /ST 09:00 /F
```

更稳的做法:**不要依赖 cwd**。在 `config.py` 里用绝对路径定位数据目录:

- 开发态:`Path(__file__).resolve().parent.parent`
- 打包态(`sys.frozen`):`Path(sys.executable).parent`
- 或统一放 `%LOCALAPPDATA%\sm-scan\`

只有这样才能同时满足「拷走整个 dist 目录即可用」和「任务计划在任意 cwd 下能跑」。

另外 §10 写 `pythonw -m sm_scan scan`,§15 又说打包后指向 `sm-scan.exe scan` —— 两套命令没有落地判定逻辑。明确:`sys.frozen` 为真时注册 exe,否则注册 `pythonw -m`,这一条要写进 `task.py` 的接口说明。

### P0-6 组间语义在文档内自相矛盾(§4.3)

表格列头是「**组间 AND**」,Europe PMC 那一行填的是 `AND`;但同一张表的 OpenAlex 行写「组间取**并集**」,下方引用块又强调「组间语义:**并集**……写死在引擎语义里」。

同一节里 AND 和并集并存,实现者无法判断以哪个为准。从产品逻辑看并集才是对的(A、B 是并列兴趣,不是交集)。**改法**:列头改成「组间关系」,三行统一填「并集」,Europe PMC 单元格里的 `AND` 移进「细节」列,注明那是「与日期过滤的 AND」。

### P0-7 裸 `MSI` 短语会引入大量假阳性(§4.2、§17)

`MSI` 在生物医学文献里最主流的含义是 **microsatellite instability**(微卫星不稳定,肿瘤学高频词),其次是主板型号。把它作为组A 的独立短语,每天会拉进成批的结直肠癌论文。

风险表里写「缩写只在标题字段匹配,不做全文」—— 但 §4.2 的短语列表和 §4.3 的翻译规则**都没有实现这个约束**,Europe PMC 用的是 `TITLE_ABS`。约束只存在于风险表,等于没有。

**改法**:删掉裸 `MSI`,只保留 `MALDI-MSI`、`MSI imaging` 这类无歧义短语。若一定要留歧义词,在适配器里单独走「仅标题」分支,并在 IR 里给短语加 `scope: title|title_abs` 字段。

### P0-8 「植物」标签规则基本失效(§7)

规则是命中 `plant, plants, tissue, root, leaf, seed, flower, natural product, biosynthesis` 之一。

问题在后五个词:
- `tissue` —— 医学论文里遍地都是(「tissue sample」「soft tissue」)
- `root` —— CS/数学里的「root node」、牙科「tooth root」
- `seed` —— 随机种子、晶种、播种数据
- `flower` —— 少见但会命中「flowering time」以外的花店/生态学

结果:「植物」这个**用户唯一真正关心的标签**,会把大量动物/临床研究标成植物学论文,失去筛选价值。

**改法**:去掉 `tissue/root/seed/flower` 的单词级匹配,换成有判别力的词:

```
plant, plants, Arabidopsis, Oryza, rice, maize, wheat, soybean, Medicago,
Nicotiana, Paeonia, medicinal plant, phytochemistry, root exudate,
leaf tissue, plant tissue, secondary metabolite, natural product
```

并且建议:标签判定加一条「与 MSI/成像类词共现」的要求,而不是单独命中就贴。

---

## 三、P1 文档内部矛盾与数据模型缺陷

### P1-1 产品定位与实际兴趣不匹配(§1、§4.2)

这是我认为**最需要跟用户确认**的一条,不是实现问题。

从 `spatial-metabolomics-papers/` 的 10 篇文献看,真实兴趣画像非常集中:植物 + 空间代谢组学 + 天然产物(9/10 篇),而且**包含 DESI**(不是纯 MALDI)。

而 spec 的组A 定义为「MALDI 全领域」,并按每天 20-50 篇、回填 30 天 = 600-1500 篇设计,靠标签和 GUI 筛选消化噪音。两个问题叠加(P0-8 的标签失效 + 组A 极宽),日报里真正相关的可能只有 2-4 篇/天,可用率低于 10%。

**建议**(三选一,需用户拍板):

| 方案 | 组A 定义 | 日报量 | 漏检风险 |
|---|---|---|---|
| A. 保持现状 | MALDI 全领域 | 20-50/天 | 低,但淹没 |
| B. 加 refine | MALDI 全领域 **AND** (plant OR "natural product" OR tissue) | 3-8/天 | 中,漏非植物成像方法学 |
| C. 分三组 | ①植物+MSI ②MSI/MALDI 方法学 ③产物注释 | 10-15/天 | 低,且可分档阅读 |

我倾向 **C**:核心组放最前,方法学次之,既不错过方法进展,也不让日报被医学论文灌满。同时组A 的短语应补 `DESI`、`SIMS`(用户已读 DESI 论文,说明技术栈不是纯 MALDI)。

### P1-2 `status` 单字段四态互斥(§6)

`status: new | read | starred | to_read` 是**一个字段**,意味着「收藏」和「待精读」不能同时存在,后设置的会覆盖先设置的。而「已读且收藏」更是必然场景。

**改法**:拆成两个维度。

```sql
status     TEXT    -- unread | read | archived
starred    INTEGER DEFAULT 0
to_read    INTEGER DEFAULT 0
```

### P1-3 `source` 单字段会丢失多源命中信息(§6)

同一篇论文常在 Europe PMC 和 OpenAlex 同时出现(DOI 相同 → 主键相同)。`INSERT OR IGNORE` 会保留先到的 `source`,后面的源信息直接丢弃。跑一段时间后无法回答「OpenAlex 到底补了多少独家文献」,而这恰恰是评估「三源是否值得」的唯一依据。

**改法**:`sources TEXT DEFAULT '[]'`(JSON 数组,记录所有命中源),另加 `first_source` 记首次发现源。

### P1-4 视图2 是需求降级,应显式告知(§9)

需求确认时用户要的是「标记管理」独立页面,spec 实现为「状态过滤快捷页」。文档里写「这是实现层的复用决策,用户看到的行为仍是四个导航项」—— 这句话把降级包装成了等价物。实际上独立页面能做的事(批量改状态、按多标签交叉筛选、批量导出)快捷页做不了。

**改法**:明确标注为「降级实现,理由是复用表格组件降低复杂度」,让用户在审阅时决定是否接受。诚实标注比含糊带过好。

---

## 四、P2 实现细节与遗漏

### 必须补进 spec 的解析细节

1. **OpenAlex 的摘要是倒排索引**。API 返回 `abstract_inverted_index: {"word": [pos...]}`,必须按位置重建文本才能入库。spec §3 说字段有 `ABSTRACT`,但没说这个转换 —— 这是实现时必踩的坑。
2. **bioRxiv 的标题/摘要含 JATS 标签**(`<p>`, `<i>`, `<sub>`),入库前要剥标签、解 HTML 实体。
3. **bioRxiv 分页**:`/details/{server}/{from}/{to}/{cursor}`,cursor 是**整数页码**不是 opaque token,每页 100 条。回填 30 天 = 数千条 = 数十次请求 × 1s 限速,首次扫描可能要十几分钟。spec 的「首次回填 30 天」验收标准没给耗时预期,容易让用户以为卡死。
   - 建议:回填时 bioRxiv 只拉 7 天(历史预印本 Europe PMC 已经有了,`SRC:PPR` 可检索),避免首次扫描过慢。
4. **RFC/时区**:`first_seen`、`started_at` 用本地时间还是 UTC 没定义,而报告文件名按「今天」—— 跨零点的扫描会归到哪一天?建议统一用本地时区并在 spec 注明。
5. **`data/last_run.lock` 的 stale 处理**:进程崩溃会留下锁文件,下次扫描会被永久阻塞。需要「pid 不存在 / 文件超过 N 小时」自动清理。

### 低成本高价值的 schema 补充

6. **加 `pmcid` 字段**。Europe PMC 的 `core` 结果直接返回 `pmcid`,入库成本≈0。用户的 `spatial-metabolomics-papers/fetch_pmc.py` 已经能按 PMCID 下载 PDF,现在留一列,以后要做「一键下载全文」就不用改表结构、重建数据。
7. **加 `schema_version`**(放 settings 表即可)。三源 API 会变,检索式会调,schema 迟早要迁移。
8. **加 `papers.updated_at`**。§5 说「已见主键更新元数据」,但没有时间戳就无法审计更新了什么。

### 报告格式

9. §12 的 `## 待精读(2)` 与前两个分组是**重复计数**(同一篇既在「成像」组又在「待精读」组),而首行又说「新增 23 篇」。读者会算不平。注明「待精读为累积状态,与前文有重叠」,或改成只在当日新增里标注。
10. 摘要「前 300 字」对英文摘要太短(约 50 词,读不出结论)。建议 300 **词**或 1200 字符。
11. 日报只有「成像/空间」和「其他」两档,和 P1-1 的问题同源。建议按「核心(植物+成像)→ 成像 → 其他」三档,让用户 30 秒内判断今天要不要细看。

### 表述与安全

12. §1「程序**不依赖任何第三方在线服务**」与它同时使用三个在线 API + LLM 直接矛盾。应改为「不依赖需要注册或付费的商业数据库(WoS/Scopus),全部走免费公开 API」。
13. LLM `api_key` 明文存 SQLite(§8)。`data/` 在 `.gitignore` 里只挡住了 git,挡不住整目录备份/分享。建议用 Windows DPAPI 加密,或至少在 README 里明确风险。GUI 的密码框也要注明「不回显、不写入日志」。
14. §4.2 的 `refine` 是可选字段。若用户清空,组B 的噪音会瞬间爆炸。建议 refine 在组B 上设为**必填**,GUI 清空时给警告。
15. §14 测试策略里建议补两条:「`resultType=core` 缺失导致空摘要」和「Europe PMC HTTP 200 + errCode 的失败识别」—— 这两条正是最容易写出静默 bug 的地方。

---

## 五、建议的最小修改清单

按优先级,可直接落到 spec 上的改动:

| # | 位置 | 改动 |
|---|---|---|
| 1 | §5 | 窗口起点改为「上次成功 run 的**结束日** - 3 天」 |
| 2 | §3、§4.3 | `FIRST_PDATE` → 窗口用 `FIRST_IDATE`,展示用 `FIRST_PDATE`,两字段都存 |
| 3 | §3、§4.3 | Europe PMC 补 `resultType=core`、`TITLE_ABS:`、`cursorMark` 分页、errCode 检查 |
| 4 | §3、§4.3、§6 | OpenAlex 改为 `?search.title_and_abstract=` + API key,删除 `mailto`,说明计费与 8KB 限制 |
| 5 | §10 | 工作目录改为项目根;数据路径不依赖 cwd;`sys.frozen` 分支注册 exe |
| 6 | §4.3 | 表头「组间 AND」→「组间关系」,三行统一「并集」 |
| 7 | §4.2 | 删除裸 `MSI`;补 `DESI`、`SIMS` |
| 8 | §7 | 重写「植物」标签词表,去掉 tissue/root/seed/flower |
| 9 | §6 | `status` 拆为 status + starred + to_read;`source` → `sources` 数组;补 `pmcid`、`indexed_date`、`updated_at` |
| 10 | §1、§4.2 | 确认组A 范围(P1-1 的 A/B/C 三方案);补 bioRxiv 回填策略与耗时预期 |

---

## 六、一句话结论

**骨架可以照此实现,细节不能照抄。** 三源适配、增量入库、GUI 分层这些结构性判断是对的;但日期字段、API 版本、分页、任务计划工作目录这四处属于「写完才发现不能用」,而增量窗口的算法错误会让程序跑一个月后自己变慢。建议先把上文 P0 全部改进 spec,再开工。
