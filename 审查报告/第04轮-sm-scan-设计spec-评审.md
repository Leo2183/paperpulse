# sm-scan 设计 spec 评审(第 04 轮)

评审对象:`docs/superpowers/specs/2026-11-06-sm-scan-design.md`(v4)
评审日期:2026-11-06
上游:`第03轮-sm-scan-设计spec-复审-含OpenAlex替代方案.md`

**结论**:v4 的架构转向是对的,第 03 轮 5 项全部消解,而且 `matched_groups` 改并集累加、订阅轨道窗口断言这两处比我建议的更彻底。但新引入的订阅轨道有一个**结构性偏差**:`full`/`filter` 二分的前提("专业刊每天几条")在清单里至少 5 本刊上不成立,按现在的归类,每天入库量会达到 **66 条左右**,反而比 v1 被否掉的"20-50 篇"更吵。

另有一项实测发现的硬限制:走 Crossref 路由的刊**没有摘要**。

---

## 一、第 03 轮问题核对

| 编号 | 结果 |
|---|---|
| N1 §18 残留"key 可选" | ✓ 随 OpenAlex 移除自然消解;并新增"全程无任何 API key 要求" |
| N2 keyless IP 共享额度 | ✓ 消解 |
| N3 60 天回看成本表述 | ✓ 消解(改固定窗口后不再重复扫描) |
| N4 `matched_groups` 快照语义 | ✓ 改**并集累加**,并明确写出"标签是规则产物需冻结,组命中是事实应累加"——这个区分比我提的更清晰 |
| N5 订阅轨道窗口断言 | ✓ §14.3 加显式断言,防"顺手统一" |

---

## 二、P0-1:`full` 模式的归类前提在 5 本刊上不成立

§3.2 对 `full` 的定义是:

> `mode=full`:整刊直收——**专业质谱/植化刊,每天几条且基本全相关**

我按 Europe PMC 实测了清单里几本被归为 `full` 的刊(2026-01-01 → 11-06,约 10.2 个月):

| 期刊 | 2026 年至今 | 日均 | 现归类 | 实际性质 |
|---|---|---|---|---|
| **Int J Molecular Sciences** | **10,335** | **33.1** | full | MDPI 综合分子生物学期刊,涵盖材料、药物、生信 |
| Analytical Chemistry | 2,631 | 8.5 | full | ACS 全分析化学(电化学、色谱、传感、光谱) |
| Talanta | 1,753 | 5.7 | full | Elsevier 全分析化学 |
| Food Chemistry | 979 | 3.2 | full | 食品科学综合刊 |
| JASMS | 257 | 0.8 | full | ✓ 符合"每天几条" |

IJMS 一家就 33 条/天。按现归类把 `full` 模式全加起来:

```
IJMS 33 + Anal Chem 8.5 + Talanta 5.7 + Metabolites ~5 + ABC ~3.3
+ Food Chem 3.2 + Anal Chim Acta ~2.6 + 窄刊合计 ~5  ≈ 66 条/天
```

一年约 2.4 万条,而用户真正关心的可能仍是 5-10 条/天。

这与项目立项时"日报 20-50 篇太吵、要靠筛选消化"的诉求直接冲突——**v4 把量从 20-50 提高到了 66**。

更麻烦的是它在日报层的表现:这 66 条里大部分 `matched_groups` 为空、`subscription` 非空,按 §12 规则会全部落进「订阅补漏」节。用户打开日报看到的第四节是 50 多条 IJMS 的无关论文。

### 修法:按发文量自动分档,而不是靠人判断

`full` 的适用条件不是"专业刊",而是**发文量小**。建议把模式选择变成可计算的:

```
加刊时探测近 90 天 hitCount:
  ≤ 300 条  → 建议 full
  > 300 条  → 建议 filter(并在 GUI 显示实测条数作为依据)
```

按这个规则重跑清单:

| 期刊 | 估 90 天条数 | 建议 |
|---|---|---|
| IJMS | ~3,040 | **filter**(甚至考虑移出清单——综合刊与三主题相关性低) |
| Analytical Chemistry | ~774 | **filter** |
| Talanta | ~516 | **filter** |
| Food Chemistry | ~288 | 边界,`filter` 更稳 |
| JASMS | ~75 | full ✓ |
| Phytochem Anal / Nat Prod Rep / Mass Spectrom Rev / Int J Mass Spectrom | ~25-90 | full ✓ |

改完后 `full` 轨道每天约 8-12 条,`filter` 轨道过滤后 3-5 条,合计 **12-17 条/天**,回到可读区间。

### 追加建议:给 `full` 加一道安全阀

`full` 模式没有主题过滤,一旦某刊发文量暴涨(MDPI 系常见),会静默灌库。建议加运行时保护:

- 单刊单日新增 > 30 条时,该刊本次降级为 `filter` 处理,并在 run 日志与日报标注
- 这样即使清单归类错了,也不会一夜之间把库灌满

---

## 三、P0-2:走 Crossref 路由的刊没有摘要(实测)

§3.2 把 MEDLINE 外的刊路由到 Crossref,§14.5 还写了测试项「Crossref abstract 剥 JATS」。实测结果:

```json
{"DOI":"10.1016/j.ijms.2014.02.011",
 "title":["Electron impact excitation from the initially excited cadmium atom"],
 "type":"journal-article"}
```

**没有 `abstract` 字段**。Elsevier 不向 Crossref 提交摘要,这不是个别现象。

连带影响是链式的:

| 依赖摘要的环节 | 后果 |
|---|---|
| `mode=filter` 的宽词表过滤 | 只剩标题可匹配 → 过滤形同虚设,只能降级 `full` |
| §7 标签判定 | 规则是"标题+摘要",降级为仅标题 → 「植物」「成像」标签大量漏标 |
| §12 日报 | 条目录入后摘要为空 |
| §8 LLM 导读 | 无摘要可喂,导读必然失败 |

**第二个实测问题**:Crossref 里混有非论文条目,且 `type` 无法区分。

```json
{"DOI":"10.1016/j.ijms.2021.116774",
 "title":["Graphical abstract Contents continued"],
 "type":"journal-article"}     ← 目录页也被标为 journal-article
```

不处理的话,它会作为一篇"论文"入库,出现在日报里。

### 修法

1. **加标题黑名单**(Crossref 路由必过):`Contents continued`、`Editorial Board`、`Graphical abstract`、`In this issue`、`Cover`、`Erratum`、`Corrigendum`、`Publisher's Note`
2. **`mode=filter` 对无摘要刊不适用**:加刊时若探测到该刊 Crossref 记录无 abstract,强制 `full` 并在 GUI 说明原因
3. **日报标注**:摘要为空的条目显示「摘要未提供(Crossref)」,不要留空白
4. **改 §14.5 的测试项**:「Crossref abstract 剥 JATS」应改为「Crossref 无 abstract 的降级行为」——现在的写法会让人以为摘要是有的
5. **§17 风险表补一条**:「Crossref 路由刊无摘要 → 过滤/标签/导读降级」

考虑到清单里真正走 Crossref 的刊只有 Int J Mass Spectrom 等少数几本,这个限制影响面小,但**必须写在文档里**,否则实现时才发现会推翻 `mode=filter` 的设计。

---

## 四、P1:新架构的其他缺口

### P1-1 路由探测用"近 90 天 hitCount>0"不可靠

§3.2:`刊在 MEDLINE(探测:近 90 天 ISSN:"0003-2700" hitCount>0)`

会误判的三种情况:

- **季刊/半年刊**:90 天内恰好无新文 → 误判为"不在 MEDLINE" → 走 Crossref → 拿到无摘要记录
- **停刊/更名刊**:永远 0 → 永远走 Crossref
- **新创刊**:刚进 MEDLINE 但还没索引 → 同上

而且探测是**初始化时一次性定死**的,误判后不会自愈。

**建议改成运行时 fallback + 缓存**:

```
查 Europe PMC(ISSN)
  ├─ 有结果 → 用 Europe PMC,缓存 route=europepmc
  └─ 0 条   → 查 Crossref
               ├─ 有结果 → 用 Crossref,缓存 route=crossref
               └─ 都无   → 记 warn,下次重试
```

这样季刊某次命中后会自动纠正路由,不需要人工干预。原则也从"猜一次"变成"试一次"。

### P1-2 `subscription` 字段的更新语义未定义

和 N4 同类的问题,但这次出现在 `subscription` 上:

- 一篇论文第 1 天被主题检索命中入库 → `subscription = NULL`
- 第 5 天该刊的订阅轨道也拉到它 → 要不要补写 `subscription`?

§12 说"订阅刊论文同时命中检索组时:按组分档,`subscription` 照记" → 说明要补写。
§5 说"已见主键:更新元数据(摘要可能从无到有)…但不重复出现在新论文报告" → 没明说 `subscription`。

**建议**:明确 `subscription` 采用**首次非空写入**(一旦有值不再覆盖,因为一篇论文只属于一本刊),与 `matched_groups` 的"并集累加"并列写在 §6 注释里。

### P1-3 宽词表的 `*` 语义与 IR 不兼容

§3.2 宽词表:`metabolom*, metabolite*, mass spectrometry, MALDI, DESI, natural product*, plant*, phytochem*`

但 §4.1 的 `Term` 只有 `phrase` + `scope`,匹配语义是"字段内不区分大小写**包含该短语**",没有通配概念。`metabolom*` 用 `Term` 表达不出来。

**建议**:明确宽词表**不是 IR**,而是一份独立常量 `BROAD_TERMS: list[str]`,匹配语义定义为**子串匹配**(`"metabolom" in text.lower()`),不参与 `queries.py` 的翻译流程。现在写"常量与 queries.py 共用"会让人误以为要复用 `Term`。

### P1-4 加刊后回填 90 天必须在 worker 线程

§9 视图3:"自动回填近 90 天并显示进度"

按实测数据,IJMS 90 天 = 3,040 条 = 31 页,按 1 req/s 约 31 秒;Nature Communications 90 天约 23 页。若用户一次加 5 本刊,可能 2-3 分钟。

在 GUI 主线程跑必然假死。**建议明确**:回填在 `QThread` 内执行、进度条逐页更新、可取消。

### P1-5 首次回填的请求量估算缺失

§3.2 写"≈ ≤40 次/天",这只覆盖日常。首次回填 32 刊 × 90 天:

```
IJMS 31 页 + Nat Commun 23 页 + Anal Chem 8 页 + 其余 29 刊各有 1-5 页
≈ 100-140 页 ≈ 3 分钟(1 req/s)
```

可接受,但应写进文档,否则用户以为卡死。建议在 §3.2 或 §17 补这一句。

### P1-6 Crossref 的 `created-date` 是注册日,可能批量涌入旧文

Crossref `from-created-date` 过滤的是 **DOI 注册时间**,不是出版时间。出版社回溯数字化、批量补注册 DOI 时,窗口内会突然出现大量陈年旧文。

**建议**:Crossref 路由的记录,入库时用 `published`/`issued` 填 `pub_date`,并加一道保护——`pub_date` 早于今天 12 个月的记录不进「新论文」,只入库不报告(或直接丢弃),避免历史文献洪水。

---

## 五、P2:文档一致性

| # | 位置 | 问题 |
|---|---|---|
| 1 | §12 首行 | `新增 23 篇(Europe PMC 12 / 订阅 7 / bioRxiv 2)` → 12+7+2=**21 ≠ 23** |
| 2 | §12 正文 | 首行写「方法学 12」,正文标题写「## 二、方法学:MALDI/MSI(**15**)」 |
| 3 | §12 | 三档计数 `4+12+4+3=23` ✓,但与来源数 21 不一致,两个口径要对齐 |
| 4 | §3 标题 | 「数据源(**三路**)」,实际是 Europe PMC(两种用法)+ Crossref + bioRxiv = 3 个 API / 2 条轨道,措辞可改为「数据源与订阅轨道」 |
| 5 | §13 | 「**三源**全失败 → failed」,应为「三轨全失败」或「全部数据源」 |
| 6 | §4.3 标题 | 「**三源**翻译规则」,表里只有两行(Europe PMC / bioRxiv),应改「检索式翻译规则」 |
| 7 | §3.2 默认清单 | 32 本刊"经 Crossref journals API 自动解析,**不手抄 ISSN**",但清单本身要硬编码在代码里 → 要么硬编码刊名在启动时解析(需处理 "Nature" 会匹配到 Nature Communications/Nature Plants 等同名前缀刊,须用 title 精确相等优先),要么允许手抄 ISSN。建议明确策略 |
| 8 | §3.3 / §5 | bioRxiv 预印本与正式版 DOI 不同(10.1101/xxx vs 期刊 DOI),去重不会合并 → 同一研究会以两条出现。v1 起就存在,订阅面扩大后更明显。建议列进 §16 非目标并说明已知 |

---

## 六、建议的 32 本清单重新分档

按"90 天 >300 条 → filter"重排:

| 模式 | 期刊 | 预估日增 |
|---|---|---|
| **filter**(高发文量) | Int J Molecular Sciences, Analytical Chemistry, Talanta, Food Chemistry, Metabolites, Analytica Chimica Acta, Analytical and Bioanalytical Chemistry | 过滤后 3-5 条/天 |
| **filter**(巨刊,仅标题级过滤) | Nature, Science, Cell, PNAS, Nature Methods, Nature Biotechnology, Nature Chemistry, Nature Plants, Nature Communications, Science Advances, Advanced Science, Angewandte Chemie | 过滤后 1-3 条/天 |
| **full**(窄刊,整刊直收) | JASMS, J Mass Spectrometry, Rapid Communications in Mass Spectrometry, Mass Spectrometry Reviews, Int J Mass Spectrometry, TrAC, Phytochemistry, Phytochemical Analysis, Journal of Natural Products, Natural Product Reports, Trends in Plant Science, Horticulture Research, Food Chemistry: X | 8-12 条/天 |
| **重新评估** | Int J Molecular Sciences | 发文量 3,040/90 天,综合刊,与三主题相关性低——建议移出或只在 filter 下保留 |

净效果:从约 66 条/天降到 **12-17 条/天**,与"日报可读"的目标一致。

---

## 七、给 v5 的改动清单

| # | 位置 | 改动 | 级别 |
|---|---|---|---|
| 1 | §3.2 | `full`/`filter` 改为按实测发文量自动建议(≤300/90 天 → full);重排 32 本清单;IJMS 移出或降 filter | P0 |
| 2 | §3.2、§14.5、§17 | 写明 Crossref 路由刊**无摘要**及四处降级(过滤/标签/日报/导读);加标题黑名单过滤目录页;修测试项 | P0 |
| 3 | §3.2 | 路由探测改运行时 fallback + 缓存,不再靠"90 天 hitCount>0"一次性定死 | P1 |
| 4 | §6、§12 | `subscription` 明确为首次非空写入 | P1 |
| 5 | §3.2 | 宽词表独立为 `BROAD_TERMS`,子串匹配,不复用 `Term` | P1 |
| 6 | §3.2 | 补 `full` 的运行时安全阀:单刊单日 >30 条自动降级 filter 并告警 | P1 |
| 7 | §9 视图3 | 加刊回填明确走 QThread + 进度 + 可取消 | P1 |
| 8 | §3.2 或 §17 | 补首次回填请求量估算(≈100-140 页 / 3 分钟) | P1 |
| 9 | §3.2 | Crossref `created-date` 是注册日,补"pub_date 早于 12 个月的记录不报告"保护 | P1 |
| 10 | §12、§3、§4.3、§13 | 修正示例数字(21 vs 23、12 vs 15)与"三源"措辞 | P2 |
| 11 | §16 | 预印本与正式版重复列入非目标 | P2 |

---

## 八、一句话结论

v4 的选型转向是这一轮里最正确的一步——用确定性订阅替代概率性检索,并且零 key 零注册,复杂度实打实降了。

但 `full` 模式的定义写的是"每天几条且基本全相关",而清单里 IJMS 是 **33 条/天**、Analytical Chemistry 是 8.5 条/天。这个前提不成立,合计会让日报回到 66 条/天,比被否掉的 v1 更吵。按发文量自动分档(阈值 300/90 天)并重排清单,量能压到 12-17 条/天。

另一条是 Crossref 路由的实测限制:Elsevier 不给 Crossref 摘要,目录页还会伪装成 journal-article。这两条不写进文档,`mode=filter` 的设计会在实现时被推翻。
