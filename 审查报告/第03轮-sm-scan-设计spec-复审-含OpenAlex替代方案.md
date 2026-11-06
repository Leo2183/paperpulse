# sm-scan 设计 spec 复审(第 03 轮)+ OpenAlex 路径方案

评审对象:`docs/superpowers/specs/2026-11-06-sm-scan-design.md`(v3)
评审日期:2026-11-06
上游:`第01轮-sm-scan-设计spec-评审.md`、`第02轮-sm-scan-设计spec-评审.md`

**结论**:v3 把第 02 轮的 3 项阻断 + 4 项高优先全部修好,修订质量稳定。但本轮实证检验发现一个新问题:**OpenAlex 被纳入本项目的立项理由不成立**——它声称要补的三本刊本来就在 Europe PMC 里,覆盖率还很足。基于此,下文给出三套替代/优化方案,并推荐其中一套。

---

## 一、v3 核对

| 上轮编号 | 核对结果 |
|---|---|
| B1 OpenAlex key 必填 | ✓ §1 决策表、§3.2、§6 settings、§9 视图3(开关禁用)、§18 均改为必填。**但 §18 第 3 条仍残留"OpenAlex key 可选申请"**(见 N1) |
| B2 `matched_groups` | ✓ §6 加列 + 注明快照语义;§12 分档依据指向该列;§14.9 加"双命中只归一档"测试 |
| B3 60 天回看 | ✓ §3.2 增量窗口行、§5 例外条目、§17 风险表三处一致;`from_created_date` 实测 429 的结论写进文档,半年后不会误判为 bug |
| H1 「成像/空间」标签 | ✓ §7 换成 12 个无歧义复合词,并写明"不认裸 imaging/spatial/MSI/mapping"及理由——这句理由写得比我的建议更清楚 |
| H2 分块语义 | ✓ Europe PMC 改 POST 不分块;OpenAlex 只分块 terms、refine 全量拼入,并给出等价性论证 `(A∪B) ∧ R ≡ (A∧R) ∪ (B∧R)`;§17 加"refine 绝不参与分块并集" |
| H3 任务计划 | ✓ `pip install -e .` 前置 + `import sm_scan` 自检 + subprocess 列表调用 + 登录态影响说明(并正确论证了"空档会被下次窗口自动覆盖") |
| M1–M9 | ✓ refine 改 `Term`、短语统一双引号、植物词表共用常量、组2⊃组1 注明、N 默认 3、`/Run` 立即验收 |

---

## 二、v3 遗留与新发现

### N1. §18 第 3 条与全篇矛盾:仍写"OpenAlex key 可选申请"

§1、§3.2、§6、§9 四处都改成了"必填",§18 交付物第 3 条却是:

> README(安装、注册定时任务、**OpenAlex key 可选申请**、LLM 配置)

README 是用户唯一会照着做的文档,这里写"可选",前面写"必填",用户会按 README 不填,然后 OpenAlex 源跑不起来。**这一处必须改**,否则前面四处白改。

### N2. OpenAlex keyless 的真实限制比 spec 写的更严重(实测)

§3.2 现在的理由是"cursor 第 2 页起需 key"。实测(本机,2026-11-06)keyless 请求的返回是:

```json
{
  "error": "Rate limit exceeded",
  "message": "Insufficient budget. This request has no API key, so it counts against
   the free daily budget shared by everyone on your network's IP address,
   and that budget is used up ($0 remaining; resets at midnight UTC).",
  "retryAfter": 32791,
  "dailyRemainingUsd": 0
}
```

两个此前没写进文档的事实:

1. **keyless 额度是「同一网络 IP 共享」的公共池**,不是每台机器独立配额。在公司/学校网络或运营商 NAT 后面,额度可能被别人用光。
2. 额度耗尽时**连单次列表请求都会 429**,不只是翻页。本机当时剩余 $0,`retryAfter` 约 9 小时。

这**加强了 v3"key 必填"的结论**,但理由要换:不是"翻页需要",而是"keyless 通道本身不可靠"。建议:

- §3.2 补一句:keyless 额度按网络 IP 共享,随时可能耗尽 → 因此 key 必填
- §13 错误处理加一行:`429 Insufficient budget` → 提示"OpenAlex 需配置 API key(keyless 为 IP 共享额度)"
- §9 视图3 的"测试连接"要能识别这个错误并给出这条准确提示,而不是笼统的"连接失败"

### N3. §5 说 60 天回看"请求成本不变"是错的

§5:

> 60 天回看 + 去重是用请求成本(**不变**)换漏检风险的折中

这句源自我在第 02 轮的表述,我当时说的是"每组一次请求"(请求**数**不变),没有把翻页算进去,不够严谨,被原样采纳了。实际成本:

| | 3 天窗口 | 60 天窗口 |
|---|---|---|
| 组1(有 refine) | ~1 页 | 2–5 页 |
| 组2(裸 MALDI) | ~1 页 | **12–30 页** |
| 组3(有 refine) | ~1 页 | 2–5 页 |
| 合计/天 | ~3 次 | **约 16–40 次** |

请求量涨 5–13 倍。虽然在 $1/天 免费额度内(1000 次 search),但"成本不变"的说法要改成"成本可控但翻 5–13 倍"。

### N4. `matched_groups` 的快照语义没覆盖"后续才命中"的情况

§6 注明「快照语义,组改名不回溯」——只说了组**改名**,没说**新源后续命中**。

真实场景:一篇论文第 1 天由 Europe PMC 的组3 命中入库,`matched_groups = ["g3"]`,归三档。第 5 天 OpenAlex 的 60 天回看扫到它(它的出版日落在 60 天内),这次命中组1。按 §5"已见主键只更新元数据",`matched_groups` 不更新 → 它永远留在三档,而它其实符合一档条件。

**建议**:明确 `matched_groups` 为**并集累加**(每次扫描命中就 append,去重),而不是首次快照。只有「标签」保留快照语义(因为标签是规则产物,组改名后才需要冻结)。这两个字段的生命周期不同,文档现在混在一起了。

### N5. §14.3 测试项漏了 OpenAlex 的 60 天窗口

"增量窗口"测试只列了首跑回填、bioRxiv 7 天、`finished_date - 3d` 不扩张,没有 OpenAlex 例外。这个例外恰好是最容易被后来改动"顺手统一"掉的——建议加一条显式断言:`openalex_window() == today - 60d`,与 `scan_window()` 不一致。

---

## 三、OpenAlex 路径评估(本轮重点)

### 3.1 立项理由的实证检验

§3.2 的定位是:

> 补全:Anal Chem、JASMS、Talanta 等 Crossref 系期刊

我用 Europe PMC 实际 API 查了这三本刊(2026-01-01 → 11-06):

| 期刊 | Europe PMC `hitCount` | 结论 |
|---|---|---|
| Analytical Chemistry | **2631** | 完全在 MEDLINE,覆盖充分 |
| Talanta | **1753** | 完全在 MEDLINE |
| Journal of the American Society for Mass Spectrometry(ISSN 1044-0305) | **257** | 完全在 MEDLINE |
| International Journal of Mass Spectrometry | **9** | 整刊不在 MEDLINE(仅个别文章被收录) |

**三本被点名的"需要 OpenAlex 补全"的刊,本来就在 Europe PMC 里**,而且量很足(Anal Chem 一个月就有 326 条)。§3.2 的立项理由不成立。

顺带:ChemRxiv 也已在 Europe PMC 索引(官方 preprint 服务器清单里列明 `PUBLISHER:"ChemRxiv"`,元数据与摘要全量收录)。所以"化学预印本只有 OpenAlex 有"这个假设也不成立。

### 3.2 真实净增量有多大

拿唯一真正缺口的 International Journal of Mass Spectrometry(ISSN 1387-3806)做标定:

| 口径 | 数量 |
|---|---|
| Crossref `filter=issn:1387-3806,from-created-date:2026-01-01` | **84 条**(2026 年至今注册的 DOI) |
| Europe PMC `ISSN:"1387-3806"` 同期 | **9 条** |
| 净缺口 | **约 75 条 / 10 个月 ≈ 0.25 条/天** |

两点说明:
- Crossref 的 `created-date` 是 DOI 注册日,Europe PMC 的 `FIRST_IDATE` 是索引日,口径相差几天到几周,但 84 vs 9 的量级差距是真实的
- 这 0.25 条/天是**该刊全部内容**,涵盖仪器、理论、气相离子化学、团簇化学;再经"MSI + 植物 / 产物注释"筛选,预期年净增 **个位数**

也就是说:**OpenAlex 的全部独家价值,对本项目的三个主题,大约是个位数篇/年。**

而维持它的代价是:必须注册 key、keyless 额度不可靠、布尔语法与 URL 编码边界、每天重复扫 60 天数据(16–40 次请求)、检索语义已两次变更(2025-09 宣布改版、2026 年 filter search 弃用)。

这是很差的投入产出比。

### 3.3 三套方案

#### 方案 A(推荐):去掉 OpenAlex,改用「主题检索 + ISSN 定向订阅」双轨

职责重新划分:

| 轨道 | 数据源 | 作用 | 请求量 |
|---|---|---|---|
| 主题检索(广撒网) | Europe PMC 三组布尔检索 + `FIRST_IDATE` | 覆盖 PubMed 全量 + 24 个预印本服务器 | 3 次/天 |
| 期刊定向(精准补漏) | Europe PMC `ISSN:"xxxx-xxxx" AND FIRST_IDATE:[...]`,不在 PMC 的刊用 Crossref `filter=issn:xxx,from-created-date:...` | 保证用户在意的刊一条不漏 | 5–10 次/天 |
| 预印本(抢时间) | bioRxiv API | 上线即抓 | 2–6 次/天 |

两条已验证的事实:

- **Europe PMC 支持 ISSN + 索引日定向**:`ISSN:"0003-2700" AND FIRST_IDATE:[2026-10-01 TO 2026-11-06]` → 326 条,可用
- **Crossref 的 `from-created-date` 免费、无需 key**:`filter=issn:1387-3806,from-created-date:2026-01-01` → 84 条,直接返回 JSON。Crossref public pool 限速 1 req/s(列表),polite pool(带 mailto)3 req/s,对每天个位数请求完全够

为什么用 ISSN 订阅而不是全库检索补漏:**用户知道自己在乎哪几本刊**。订阅 10 本目标刊,每天 10 次请求就能保证零漏检;而全库布尔检索要扫几十页,还未必覆盖得准。前者是确定性,后者是概率。

代价与缓解:

| 代价 | 缓解 |
|---|---|
| 需要用户手工维护 ISSN 清单 | 首次给一份默认清单(Anal Chem、JASMS、Talanta、Anal Chim Acta、IJMS、RCM、J Mass Spectrom、Phytochem Anal、Phytochemistry、J Nat Prod、Food Chem),GUI 可增删 |
| 覆盖不到"没想到的刊" | 主题检索那条腿仍在跑(Europe PMC 覆盖 PubMed 全量),ISSN 订阅只补 MEDLINE 外的缺口,不是主力 |

#### 方案 B(保守):保留 OpenAlex,但降级为周扫

- 频率从每天降到每周(settings 加 `openalex_interval_days = 7`)
- 窗口维持 60 天(周扫也不漏)
- 结果在日报里单独标注「OpenAlex 补充」,不与主源混排
- key 仍然必填

代价:请求量降 7 倍(2–6 次/周),但仍然要维护 key、布尔语法和 60 天重复扫描。**保留了复杂度,只省了请求数**——而请求数本来就不是瓶颈。

#### 方案 C:Crossref 完全替代 OpenAlex

- 可行性:Crossref 做**定向拉取**很好(免费、`from-created-date` 可用、ISSN 过滤、cursor 翻页、rows 上限 1000)
- **不可行**:Crossref 做不了主题检索。`query.bibliographic` / `query.title` 是相关性排序的词袋匹配,不支持 `AND/OR`/引号短语的布尔组合,也没有字段级 scope。用它替 OpenAlex 的检索角色会大幅降低召回质量
- 所以 Crossref 只能替代**补充源**角色,不能替代**检索源**角色

### 3.4 方案对比

| | A:去 OpenAlex + ISSN 订阅 | B:OpenAlex 周扫 | C:Crossref 替代 |
|---|---|---|---|
| 需要 OpenAlex key | 否 | 是 | 否 |
| 需注册/付费 | 否 | 免费但要注册 | 否 |
| 布尔检索能力 | Europe PMC 完整 | OpenAlex 完整 | 无 |
| 跟踪入库时间 | Europe PMC 有 `FIRST_IDATE` | 无(60 天回看) | 有 `from-created-date` |
| 每天请求量 | 10–20 | 16–40(周扫摊薄) | 10–20 |
| 漏检风险 | 低(两条腿互补) | 低 | 中(无主题检索) |
| 实现复杂度 | 中(多一个 ISSN 订阅模块) | 高(维持 3 套语法) | 中 |
| 新增外部依赖 | 无 | 1 个 key + 额度不确定性 | 无 |

**推荐 A**。它把"补漏"从"概率性的全库检索"换成"确定性的期刊订阅",正好匹配用户的实际使用方式(精读目录里 10 篇来自 8 本不同期刊,说明他关注的是**特定几本刊 + 主题**,不是全库漫游)。

### 3.5 若采纳 A,spec 需要改的地方

1. §1 决策表:OpenAlex 行改为「本期不接入」
2. §2 目录:`sources/openalex.py` → `sources/crossref.py`(仅 ISSN 定向用,可选);新增 `journals.py`(ISSN 订阅清单与拉取)
3. §3.2 整节替换为「Crossref / Europe PMC ISSN 定向订阅」
4. §3.4 网络纪律:Crossref 请求带 `mailto`(polite pool,3 req/s)
5. §5 删掉 OpenAlex 60 天例外;ISSN 轨道用 `FIRST_IDATE` / `from-created-date` 精确增量
6. §6 `settings`:`openalex_api_key` → 删;新增 `journal_subscriptions`(JSON 数组)
7. §9 视图3:OpenAlex key 输入框 → ISSN 订阅清单编辑
8. §12 分档不变(`matched_groups` 仍由组命中决定;ISSN 轨道的命中归属需明确——建议单独标 `via: journal`)
9. §17 风险表:删 OpenAlex 三条(布尔语法、8KB 分块、无法跟踪入库时间),加「ISSN 清单可能不全」
10. §18 验收:三源 dry-run → 两源 + ISSN 轨道 dry-run

---

## 四、给 v4 的改动清单

**若不采纳新方案(维持 OpenAlex),至少改这 4 条:**

| # | 位置 | 改动 |
|---|---|---|
| 1 | §18 第 3 条 | "OpenAlex key 可选申请" → "必填申请",消除与 §1/§3.2 的矛盾 |
| 2 | §3.2、§13 | 补 keyless 的「IP 共享额度」事实 + `429 Insufficient budget` 的错误识别与提示 |
| 3 | §5 | "请求成本(不变)" → "请求量翻 5–13 倍但仍在免费额度内" |
| 4 | §6、§14.3 | `matched_groups` 改为并集累加语义;测试补 OpenAlex 60 天窗口断言 |

**若采纳方案 A,第 3.5 节的 10 条替换上面 4 条中的第 2、3 条。**

---

## 五、一句话结论

v3 已经把「照文档实现会坏」的问题清干净了。本轮真正的问题不在实现层,而在**选型层**:OpenAlex 声称要补的三本刊,实测 2631 / 1753 / 257 条都在 Europe PMC 里;它真正的独家增量约 0.25 条/天,过完主题筛选剩个位数篇/年。为这个增量维持一个需要 key、额度按 IP 共享、检索语法半年改两次的第三源,不划算。

建议改用「Europe PMC 主题检索 + ISSN 定向订阅 + bioRxiv」三轨,把补漏从概率换成确定性。
