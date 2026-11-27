# agoralar-source-markdown

Источник объектов для [AxisLar](https://github.com/authorlarciss/AxisLar)
поверх дерева заметок Markdown: frontmatter, `#tags`, `[[wiki-links]]`.
Плагин продукта [AgoraLar](https://github.com/authorlarciss/AgoraLar).

Дерево **не изменяется**: источник только читает. Ссылка на
несуществующую заметку не создаёт теневой объект — она попадает в
предупреждения и отбрасывается.

```toml
[plugins.markdown]
root = "vault"
source_id = "vault"          # стабильный id источника, не путь
# необязательно: имена осей и вид объекта
# folder_axis = "folder"
# tags_axis = "tags"
# links_axis = "links"
# created_axis = "created"
# kind = "note"
```

Что раскладывается по осям:

| Источник | Ось | Тип |
|---|---|---|
| каталог заметки | `folder` | categorical |
| `tags` в frontmatter и `#tags` в теле | `tags` | categorical, multi |
| `[[wiki-links]]` | `links` | reference → `note`, multi |
| `created` / `date` / `updated` в frontmatter | `created` | measurable, сек |

Скаляры: `title` (frontmatter или имя файла), `path`, `filename`.

Ограничения, которые зафиксированы осознанно:

- frontmatter разбирается упрощённо: скаляры, `[a, b]` и блок `- item`.
  Вложенные таблицы не поддерживаются;
- wiki-ссылка ищется по имени файла (как в Obsidian) или по пути без
  расширения; при совпадении имён в разных папках побеждает первый по
  порядку обхода, а источник об этом предупреждает;
- кодовые блоки в тройных обратных кавычках игнорируются при поиске тегов
  и ссылок.
