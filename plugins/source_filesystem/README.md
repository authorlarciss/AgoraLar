# agoralar-source-filesystem

Источник объектов для [AxisLar](https://github.com/authorlarciss/AxisLar):
файловое дерево раскладывается по осям, имя файла — по фасетам.

Плагин продукта [AgoraLar](https://github.com/authorlarciss/AgoraLar).
Дерево не изменяется: источник только читает.

```toml
[plugins.filesystem]
root = "/data/Owner"
source_id = "owner-media"          # стабильный id источника, не путь
levels = ["owner", "media", "shelf", "section", "title"]
codes = { VTA = "anime", VTG = "games" }
```
