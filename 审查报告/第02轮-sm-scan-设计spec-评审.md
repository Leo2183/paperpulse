# sm-scan 设计 spec 评审(第 02 轮)

评审对象:`docs/superpowers/specs/2026-11-06-sm-scan-design.md`(v2)
评审日期:2026-11-06
上一轮:`第01轮-sm-scan-设计spec-评审.md`

**结论**:v1 的 8 项 P0 修好 7 项,修订质量高(不是打补丁,是把原则重新对齐了:增量跟踪"入库"而非"出版"、IR 显式 scope、数据路径绝对化)。但 v2 引入 2 项新缺陷,另有 2 项只修了一半,其中 1 项会导致首次回填必然失败。

同时,**上一轮我有 2 处判断需要更正**,见 §五。

---

## 一、v2 已确认修好的(逐条核对)

| 上轮编号 | 核对结果 |
|---|---|
| P0-1 窗口扩张 | ✓ `from = last_ok_run.finished_date - 3d`,并显式声明"不会随轮次向前扩张";§14.3 有回归测试 |
| P0-2 日期字段 | ✓ `FIRST_IDATE` 做窗口 / `FIRST_PDATE` 做展示,schema 加 `indexed_date`,职责分工写进 §3.1 表 |
| P0-3 Europe PMC 约束 | ✓ `resultType=core`、`TITLE_ABS`、`cursorMark`、`errCode/resultList` 四项全落,§14.4 有防回归测试 |
| P0-4 OpenAlex 语法 | ✓ 新 `search.title_and_abstract`、一组一请求、倒排索引重建、8190 字节分块 |
| P0-5 工作目录 | ✓ 改为数据路径绝对化(开发态/打包态双分支),比"设 cwd"更稳——这是比我的建议更好的解法 |
| P0-6 组间语义 | ✓ 表头改"组间关系",三行统一"并集" |
| P1-1 三组 | ✓ 方案C 落地,组1 带植物 refine,组3 refine 必填 + GUI 阻止清空 |
| P1-2 标记模型 | ✓ `status` + `starred` + `to_read` 三字段,「已读且收藏且待精读」可并存 |
| P1-3 源信息 | ✓ `source`(首见)+ `sources`(JSON 数组) |
| P1-4 视图2 | ✓ 恢复独立页,补批量操作/交叉筛选/批量导出 |
| P2 × 15 | ✓ 倒排索引、JATS 清洗、bioRxiv 7 天回填、时区、锁 stale 清理、pmcid、schema_version、updated_at、300 词、三档、表述、DPAPI、refine 必填、测试补条——全部落地 |

§19 修订记录这一节本身值得肯定:逐条对应评审编号,可追溯。

---

## 二、新增的阻断级缺陷

### B1. OpenAlex 的 key「可选」与 cursor 分页互斥 → 首次回填必然失败

这是 v2 最危险的一处,因为它在配置正确的情况下失败。

§3.2 同时写了三件事:

1. `openalex_api_key` **可空**,无 key 时 keyless 运行
2. 分页用 `per-page=100` + `cursor=*` 游标
3. 首次回填 30 天

官方文档(2026-11-03 更新)对第 2 点的限定是:

> a free API key ... is **required for cursor paging past the first page**
>
> To retrieve more than 10,000 results, use cursor paging (**with a free key: without one, only the first page works**).

而组2 是裸 `MALDI`,按 spec 自己给的量级 20-50 篇/天 × 30 天回填 = **600-1500 条**,`per-page=100` 必然要翻第 2 页以后。

**后果**:用户在 GUI 里不填 key(因为 spec 说可选),首次回填时 OpenAlex 源在第一页之后 401,run 记 partial。而 `partial` 是"可接受"状态,用户不会意识到少了一大半数据。

**修法(二选一)**:

| 方案 | 做法 | 代价 |
|---|---|---|
| a. key 改必填(推荐) | README 写明"需免费注册 OpenAlex key(30 秒)",GUI 不填则 OpenAlex 源默认关闭并提示 | 多一步注册 |
| b. 保留可选但显式降级 | keyless 时只取单页,并在 GUI/日报明确标注"OpenAlex 本次未完整回填";`backfill` 时强制要求 key | 语义复杂,易误读 |

顺带更正:§3.2 的「keyless 10 req/s / free key 30 req/s」数字是**准确的**(官方 rate limits 表确认)。我在上一轮说"所有请求需要 API key"过于绝对,见 §五 E2。

### B2. schema 缺 `matched_groups`,§12 三档分档没有数据依据

§12 的分档规则:

> 命中组1(refine 通过)→ 一档;未入一档但命中组2/「成像/空间」标签 → 二档;其余 → 三档

但 §6 的 `papers` 表**没有任何字段记录"这篇论文命中了哪个检索组"**。

现有的 `tags` 是 `tags.py` 里硬编码的三条规则(成像/植物/注释),和"命中组1/组2"不是一回事:

- 组2 命中 ≠ 有「成像/空间」标签(裸 MALDI 蛋白质组学论文两组都不沾)
- 组1 命中 ≠ 有「植物」+「成像」标签(refine 词表和标签词表已经不一致了,见 M6)
- 组3 的短语虽然驱动「注释」标签,但反过来不成立

**后果**:`report.py` 生成三档时拿不到依据,只能退化为按标签猜,三档分档名存实亡。

**修法**:加一列,入库时由引擎写入。

```sql
matched_groups TEXT DEFAULT '[]'   -- JSON:["g1","g2"],组 id 或组名
```

同时 §14.9 的报告快照测试要覆盖"一篇论文同时命中 g1/g2 时归入一档"。

### B3. OpenAlex 仍用出版日做增量窗口,与 P0-2 确立的原则不一致

这一条**不要**照直觉改成 `from_created_date` —— 官方明确那四个 sync filter(`from_created_date` / `from_updated_date` / `to_created_date` / `to_updated_date`)是 **Member+/Partner 付费功能**,免费调用返回 `429` `"Plan upgrade required"`。

所以 v2 用 `from_publication_date` 是免费方案里唯一能跑的选择,**但代价必须写进文档**:

> 一篇 2025 年出版、2026 年才进 OpenAlex 的论文,`from_publication_date:2026-10-04` 抓不到,且以后也不会再被抓到。

这与 §3.1 为 Europe PMC 立的原则(跟踪"新入库"而非"新出版")直接冲突,只是 OpenAlex 没有免费的等价手段。

**建议的折中**:OpenAlex 不跟随 3 天重叠窗口,而用**更宽的固定回看**(如 60 天),靠 DOI 去重消化重复。

理由:OpenAlex 按**请求**计费(search $0.001/次)而非按结果计费,把 3 天窗口拉到 60 天,请求数不变、返回条数增多、成本几乎不变;而漏检是**永久损失**。这个取舍应该写进 §17 风险表,否则半年后用户发现漏检会以为是 bug。

---

## 三、只修了一半的

### H1. §7「成像/空间」标签仍用裸 `MSI` / `imaging` / `spatial`

P0-7 在 §4.2 建立了 scope 机制(正确),但 §7 这一行**一个字都没改**:

```
| 成像/空间 | 命中 imaging, spatial, MSI, mass spectrometry imaging, mapping 之一 |
```

问题比检索式更严重,因为这个标签直接决定日报二档归类(§12):

- `MSI` → **microsatellite instability**(微卫星不稳定,结直肠癌/林奇综合征文献里的最高频缩写)
- `imaging` → 医学影像(MRI/CT/PET)、细胞成像、活体成像
- `spatial` → spatial transcriptomics、spatial distribution、空间统计
- `mapping` → 基因 mapping、QTL mapping、脑功能 mapping

单词级 OR 匹配下,一篇纯临床肿瘤论文只要摘要里出现 "imaging" 就进「成像/空间」标签,进而被分到**二档「方法学:MALDI/MSI」**。

**修法**:与 §4.2 保持同一套判别标准——只认无歧义复合词。

```
mass spectrometry imaging, MALDI-MSI, MALDI imaging, imaging mass spectrometry,
DESI-MSI, spatial metabolomics, spatially resolved metabolomics, TOF-SIMS,
secondary ion mass spectrometry, MSI imaging
```

裸 `imaging` / `spatial` / `MSI` / `mapping` 全部移除。宁可漏标,不可错标——错标会把噪音推到用户唯一会认真读的那一档。

### H2. refine 分块取并集是语义错误(§4.3 + §17)

§17 写:

> Europe PMC / OpenAlex 查询串超长 | 均按短语分块拆分请求,结果**并集**(§4.3)

但 refine 的语义是 **AND**:

- **terms 分块** → 并集 ✓(`(p1 OR p2)` 拆成两次查询再合并,等价)
- **refine 分块** → 必须**交集** ✗(拆成 `AND r1` 和 `AND r2` 两次查询取并集,等于只要求命中 r1 或 r2,限定被削弱)

后果最严重的是组3:refine 是必填的 `(mass spectrometry OR MS/MS OR LC-MS OR MALDI OR tandem mass spectrometry)`,一旦分块退化为并集,`metabolite identification` 这个高频泛词会灌进大量非质谱文献——正是组3 设 refine 要防的事。

**修法(按优先级)**:

1. **首选:不分块,改用 POST**。Europe PMC 有 `searchPOST` 端点(官方存在的用途就是"queries too long for a URL"),OpenAlex 也有 OQL POST 形式,彻底绕开长度限制
2. 若坚持分块:明确写「terms 分块取并集;**refine 分块需按结果 ID 取交集**,或直接放弃本次 refine 并告警」
3. 至少要在 §17 把这句话改准确,别让实现者照抄

### H3. 开发态注册任务计划缺少 `pip install -e .` 前置

§10 的注册命令是 `pythonw -m sm_scan scan`。任务计划运行时 cwd 是 `C:\Windows\System32`,而 `pythonw` **没有控制台**,导入失败时既无窗口也无输出。

除非 `sm_scan` 已经装进 site-packages(`pip install -e .`),否则 `import sm_scan` 直接失败,任务"注册成功但从不运行",且**没有任何可见错误**。

**修法**:

1. §10 或 README 写明开发态前置:`pip install -e .`
2. `task.py register` 在调用 `schtasks` **之前**先自检:
   `pythonw -c "import sm_scan"` 返回码非 0 则拒绝注册,并在 GUI 提示具体原因
3. 这是"静默失败"里最难排查的一类,自检成本很低,值得做

---

## 四、中低优先

| # | 位置 | 问题 | 建议 |
|---|---|---|---|
| M1 | §4.1 | `Term(phrase, scope)` 有 scope,但 `refine: list[str]` 没有——IR 不对称 | refine 也用 `Term`,翻译时统一加引号 |
| M2 | §4.2 组3 | `MS/MS`、`LC-MS` 在 Europe PMC 里 `/` 和 `-` 会被分词 | 写明"所有短语一律加双引号",并进 §14.2 测试用例 |
| M3 | §10 | `/TR "\"<path>\" -m ..."` 是**批处理语法**;Python `subprocess.run` 列表形式调用时不该带 `\"` 转义,否则路径会含字面反斜杠 | 注明用列表形式调用,不传 shell |
| M4 | §10 | `schtasks /SC DAILY` 默认"仅用户登录时运行",睡眠/关机不执行、不补跑 | 可选:说明影响(窗口会自动覆盖空档,不会漏数据),或加"错过则尽快启动" |
| M5 | §18 | 验收"注册任务计划次日自动跑通"要等一天 | 加一步 `schtasks /Run /TN sm-scan-daily` 立即触发验证,再等次日 |
| M6 | §4.2 / §7 | 组1 的 16 个植物词与 §7 的 18 个植物词各自维护,已经开始漂移 | 共用一份常量,或注明"标签词表 ⊇ 组1 refine 词表" |
| M7 | §3.2 | "keyless 10 req/s" 与 "免费 key 额度 $1/天" 并列,读起来像"key 是可选增强" | 明确写:key 在**回填/翻页**场景是必需品,见 B1 |
| M8 | §9 视图4 | "对最新 N 天未读论文批量生成"——N 无默认值 | 给默认(如 3 天)并允许改 |
| M9 | §4.2 | 组2 的 9 个短语里 6 个与组1 重复,实际是"组1 去掉 refine 的超集" | 加一句说明,避免实现者以为要两套独立逻辑;分档优先级依赖 B2 |

---

## 五、我上一轮的两处判断需要更正

写在这里,避免错误结论被带进实现。

### E1. P0-7 我把「标签规则里的 MSI」错写成「§4.2 短语表里的裸 MSI」

v1 §4.2 组A 的短语表是:

```
MALDI, matrix-assisted..., MALDI-MSI, MALDI imaging, mass spectrometry imaging,
imaging mass spectrometry, spatial metabolomics
```

**确实没有裸 `MSI`**。v2 §19 指出的"评审所指与原文有出入",这个反驳成立,我认。

但要说清楚:**问题本身是真的,只是我定位错了位置**。裸 `MSI` 出现在两处,都不是 §4.2 短语表:

- §7 标签规则:`imaging, spatial, MSI, ...`(← 真问题在这)
- §17 风险表:"'MSI' 等缩写歧义"(作者自己也承认了)

而 v2 修的是 §4.2(加了 scope 机制,这本身是好的改进),**§7 那个真问题原样留着**。所以这条以 H1 的形式重新提出。

### E2. 我说 OpenAlex「所有请求需要 API key」,过于绝对

官方 authentication 页(2026-11-03)明确:

> you can make basic queries with no key at all
> No API key → 10 req/s;Free API key → 30 req/s

准确说法是:**key 在 cursor 翻页(超过第一页)时必需**,不是所有请求必需。v2 引用的 10/30 req/s 是对的。

不过这不改变 B1 的结论——因为回填必然翻页,key 在实际使用中是必需品。

---

## 六、给 v3 的最小改动清单

| # | 位置 | 改动 | 级别 |
|---|---|---|---|
| 1 | §3.2、§9 视图3、README | OpenAlex key 改必填,或 keyless 时强制降级并显式告警 | 阻断 |
| 2 | §6 | 加 `matched_groups TEXT DEFAULT '[]'`;§14.9 覆盖 g1/g2 同时命中的分档 | 阻断 |
| 3 | §3.2、§17 | OpenAlex 窗口改为固定 60 天回看 + DOI 去重,并把"无法跟踪入库时间"写进已知取舍 | 阻断 |
| 4 | §7 | 「成像/空间」改用无歧义复合词,删裸 `imaging/spatial/MSI/mapping` | 高 |
| 5 | §17、§4.3 | 分块规则改准确:terms 并集 / refine 交集;或改用 POST 绕开长度限制 | 高 |
| 6 | §10 | 补 `pip install -e .` 前置 + register 前 `import sm_scan` 自检 | 高 |
| 7 | §4.1 | refine 改为 `Term` 列表,统一加引号 | 中 |
| 8 | §10 | subprocess 列表调用、不加 shell 转义 | 中 |
| 9 | §18 | 验收加 `schtasks /Run` 立即验证 | 中 |
| 10 | §4.2、§7 | 植物词表共用常量;注明组2 ⊃ 组1 | 低 |

---

## 七、一句话结论

v2 的修订质量明显高于打补丁水平——把「跟踪新入库而非新出版」「IR 显式 scope」「数据路径不依赖 cwd」这几条**原则**重新对齐了,而不是就地打补丁。

剩下的问题集中在两类:**一是 OpenAlex 这条链路的三处约束没有互相印证**(key 可选 / cursor 翻页 / 30 天回填,三者不能同时成立);**二是 v2 新增的三档分档缺少数据支撑**(缺 `matched_groups`)。这两条修完,加上 §7 的标签词表,就可以进实现了。
