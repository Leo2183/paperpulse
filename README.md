# paperpulse

可配置的**公开论文定期扫描器**:按主题检索式 + 期刊订阅 + 预印本三轨监控新论文,SQLite 落库、Markdown 日报、桌面 GUI、Windows 任务计划每日自动运行。**全程零 API key、零注册**(Europe PMC / Crossref / bioRxiv 均匿名免费)。

监控什么、订哪些刊,全部由 `presets/` 下的一份 JSON 主题包定义——换研究方向只需编辑一个文件。

## 三轨架构

| 轨道 | 数据源 | 作用 |
|---|---|---|
| 主题检索 | Europe PMC(PubMed 全量 + 预印本,布尔短语 + refine AND) | 广撒网 |
| 期刊订阅 | 32 刊 ISSN(MEDLINE 内走 Europe PMC,外走 Crossref 自动 fallback) | 确定性补漏:关注的刊一条不漏 |
| 预印本 | bioRxiv | 上线即抓 |

模式与降级均已实证校准:期刊按 90 天发文量分 `full`(整刊直收)/`filter`(宽词表过滤);Crossref 路由刊无摘要时自动降级仅标题过滤;陈年补注册旧文只入库不进日报。

## 安装(开发态)

```bat
git clone https://github.com/Leo2183/paperpulse.git && cd paperpulse
pip install -e .[dev]
pytest                       # 162 用例,离线
```

> 注册任务计划前必须 `pip install -e .`——否则任务计划里 `import paperpulse` 静默失败(pythonw 无控制台)。

## 使用

| 命令 | 作用 |
|---|---|
| `python -m paperpulse scan --dry-run` | 打印三轨请求串与窗口,不落库 |
| `python -m paperpulse scan` | 三轨扫描入库并生成当日日报 |
| `python -m paperpulse scan --days 40 --llm` | 扩大窗口 + 批量中文导读(需先配置 LLM) |
| `python -m paperpulse journals` | 逐刊审计 90 天实测发文量 vs 当前模式(约 32 次请求) |
| `python -m paperpulse gui` | 四视图 GUI |
| `python -m paperpulse report --date 2026-11-07` | 重生成某日日报 |
| `python -m paperpulse task register` | 注册每日 09:00 任务计划(paperpulse-daily) |

退出码:`0`=ok,`2`=partial(部分轨道失败),`3`=failed。

### 自定义主题(presets)

首次运行自动加载 `presets/` 中的主题包(多份并存时按文件名排序取第一份);fork 后编辑或替换这份 JSON 即成你自己的监控器。详见 [presets/README.md](presets/README.md)。

### 首次运行预期

首跑回填约 30 天窗口(实测约 10 分钟量级,取决于网络抖动与重试),日常窗口每次 1-2 分钟。GUI「订阅期刊」页可按刊名解析 ISSN、自动建议模式并回填 90 天;`paperpulse journals` 可批量审计模式配置。

### 日报结构(`reports/YYYY-MM-DD.md`)

三档分档(核心组 → 方法学组 → 其他,按组 id 分档、组改名不受影响)+ 订阅补漏(4a 巨刊筛选 / 4b 整刊直收,附每刊计数)+ 待精读(累积,注明与前文重叠)。

## LLM 中文导读(可选,默认关)

GUI → 检索式配置 → 源与 LLM:**供应商下拉**(智谱 GLM/Coding Plan、DeepSeek、OpenAI、Kimi、通义、SiliconFlow、火山方舟、xAI、Gemini 兼容、Groq、本地 Ollama/LM Studio、自定义)自动填 `base_url`;填 `api_key` 后**「获取模型列表」**直接拉取可用模型下拉选择(GLM、DeepSeek、Ollama 均可;base_url 填到 `/v4` 或 `/v4/chat/completions` 皆可)。key 经 Windows DPAPI 加密存本机(拷走 db 到他机不可解)。已有导读持久缓存不重调;无摘要论文自动跳过。

## 打包

```bat
pip install pyinstaller
python build.py
```

产出两个整目录(各自拷走即用):`dist/paperpulse/paperpulse.exe`(CLI,任务计划指向它)、`dist/paperpulse-gui/paperpulse-gui.exe`(界面)。数据目录在 exe 旁的 `data/`、`reports/`。

## 数据

`data/scan.db`(论文库 WAL / runs / 检索组 / 订阅 / 设置,含 schema 迁移)、`data/scan.log`(滚动日志)、`reports/`。均为本机文件。定时任务为"仅用户登录时运行":睡眠/关机跳过不补跑,数据不丢(下次窗口自动覆盖空档;仅 partial 的 run 不推进窗口)。

## 设计与质量

- 设计文档(五轮评审):`docs/superpowers/specs/`;实现评审:`审查报告/`
- 120 个离线测试(fixture 采用真实 API 录制 + 字段契约断言),GUI offscreen 冒烟

## License

MIT — see [LICENSE](LICENSE)
