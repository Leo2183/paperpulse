# Presets(主题包)

paperpulse 的默认检索组与订阅期刊不再硬编码在代码里,而是从这个目录的主题包加载。
换研究主题 = 换/改一份 JSON,不用动一行代码。

## 结构

```jsonc
{
  "name": "spatial-metabolomics",
  "description": "……",
  "groups": [            // 检索组(Europe PMC 布尔短语 + refine AND 限定)
    { "id": 1, "name": "植物+空间成像", "enabled": 1,
      "terms":  [{ "phrase": "mass spectrometry imaging", "scope": "title_abs" }],
      "refine": [{ "phrase": "plant", "scope": "title_abs" }] }
  ],
  "journals": [          // ISSN 订阅清单;mode: full=整刊直收 | filter=宽词表过滤
    { "name": "Nature", "issn": "0028-0836", "mode": "filter" }
  ]
}
```

## 用法

- 首次运行时,若数据库为空,自动加载 `presets/<name>.json`
  (多份时按文件名排序取第一份);此后一切改动在 GUI 或库中进行
- 修改默认主题:直接编辑本目录 JSON 后**删除 `data/scan.db` 重新初始化**
  (或建第二份 JSON 并换名排序靠前)
- 短语 `scope`:`title_abs`(标题+摘要,默认)或 `title`(歧义缩写仅标题,如裸 MSI)
- 加刊建议先在 GUI 里用「解析」按刊名查 ISSN,mode 参考 90 天发文量
  (≤300 建议 full,>300 建议 filter;`paperpulse journals` 可批量审计)
