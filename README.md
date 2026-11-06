# sm-scan — 空间代谢组学论文定期扫描器

独立 Windows 程序:三轨定期扫描公开论文(PubMed 主题检索 + 32 刊 ISSN 订阅 + bioRxiv 预印本),SQLite 落库,Markdown 日报,PySide6 GUI,任务计划每日自动运行。**全程零 API key、零注册**(Europe PMC / Crossref / bioRxiv 均匿名免费)。

设计文档:`docs/superpowers/specs/2026-11-06-sm-scan-design.md`(v5.1,五轮评审定稿)

## 安装(开发态)

```bat
git clone <repo> && cd sm-scan
pip install -e .[dev]
pytest            # 全量测试(90 用例,离线)
```

> 注册任务计划前必须 `pip install -e .`——否则任务计划里 `import sm_scan` 静默失败(pythonw 无控制台)。

## 使用

| 命令 | 作用 |
|---|---|
| `python -m sm_scan scan --dry-run` | 打印三轨请求串与窗口,不落库 |
| `python -m sm_scan scan` | 三轨扫描入库并生成当日日报 |
| `python -m sm_scan gui` | 启动四视图 GUI(列表/标记/配置/扫描中心) |
| `python -m sm_scan report --date 2026-11-06` | 重生成某日日报 |
| `python -m sm_scan task register` | 注册每日 09:00 任务计划(sm-scan-daily) |

退出码:`0`=ok,`2`=partial(部分轨道失败),`3`=failed。

### 首次运行预期

首跑回填:Europe PMC 主题检索 30 天 + 订阅 32 刊随主窗口 30 天(GUI 加新刊才单独回填 90 天)+ bioRxiv 7 天。合计约 50-80 次请求(1 req/s),**预计 1-3 分钟**,日志见 `data/scan.log`。

### 日报结构(`reports/YYYY-MM-DD.md`)

三档分档(核心植物+成像 → MALDI/MSI 方法学 → 其他)+ 订阅补漏(4a 巨刊筛选 [订阅] / 4b 整刊直收 [整刊],通常为最大一节,属预期)+ 待精读(累积,注明与前文重叠)。

## LLM 中文导读(可选,默认关)

GUI → 检索式配置 → 源与 LLM:填 OpenAI 兼容 `base_url` / `api_key` / `model`(GLM、DeepSeek、Ollama 均可;base_url 填到 `/v4` 或 `/v4/chat/completions` 皆可)。key 经 Windows DPAPI 加密存本机库,**拷走 data/scan.db 到他机不可解**(迁移需重新输入)。已有导读持久缓存不重调。

## 订阅期刊

默认 32 本(CNS + Nature 子刊 + 质谱/分析 + 植物天然产物),`full`(整刊直收)/`filter`(宽词表过滤)两级模式:加刊时自动探测近 90 天发文量建议模式(≤300 → full)。GUI 加新刊自动回填该刊近 90 天。内置 ISSN 常量已逐一经 Crossref 实测校验。

已知限制(设计期实证,非 bug):Crossref 路由的刊(如 Int J Mass Spectrom)无摘要(Elsevier 不提交)→ 该类刊 filter 降级为仅标题匹配、日报标注「摘要未提供」、导读跳过;预印本与正式版 DOI 不同,同一研究两条并存(不去重合并)。

## 打包

```bat
pip install pyinstaller
python build.py
```

产出两个整目录(各自拷走即用):`dist/sm-scan-gui/sm-scan-gui.exe`(界面)、`dist/sm-scan/sm-scan.exe`(CLI,任务计划指向它)。数据目录在 exe 旁的 `data/`、`reports/`。

## 数据

- `data/scan.db` — 论文库(WAL)、runs、检索组、订阅清单、设置
- `data/scan.log` — 滚动日志(1MB×3)
- `reports/` — 日报、待精读清单

均为本机文件,git 忽略。定时任务为"仅用户登录时运行":睡眠/关机跳过不补跑,数据不丢(下次扫描窗口自动覆盖空档)。
