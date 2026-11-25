# agoralar-source-http

Источник объектов для [AxisLar](https://github.com/authorlarciss/AxisLar):
JSON-API отдаёт массив записей, плагин раскладывает поля по осям и
скалярам.

Плагин продукта [AgoraLar](https://github.com/authorlarciss/AgoraLar).
Источник только читает: `GET` без тела, без ретраев.

```toml
[plugins.http]
url = "https://api.example.net/edits"   # источник данных
source_id = "api-edits"                 # стабильный id источника, не URL
kind = "edit"
key = ["id"]                            # естественный ключ → entity_id_for
items = "data.items"                    # путь к массиву в ответе ('' — корень)

axes = ["author", "genre"]              # категориальные оси
multi = { genre = "," }                 # список или строка через запятую
measurable = ["year"]                   # измеримые оси
reference = { editor = "person" }       # ссылочные оси: поле → вид цели
scalars = ["title"]                     # текстовые поля
hash = ["title", "author", "year"]      # по умолчанию — все сопоставленные

headers = { Authorization = "Bearer ..." }
timeout = 10.0
```

Правила те же, что у `agoralar-source-sql` (это намеренно: у источников
одна семантика, разные транспорты):

- `null` и отсутствующее поле — **не значение**, а отсутствие: фасет не
  создаётся, объект разреженный (ADR-0010 ядра);
- `multi`-поле принимает и JSON-массив, и строку с разделителем;
- bool в `measurable` — предупреждение, а не `1`/`0`;
- запись без значения в ключевом поле пропускается с предупреждением;
- `content_hash` — по каноническому JSON **сопоставленных** полей, поэтому
  прочие поля ответа не считаются изменением объекта;
- `id` = `entity_id_for(source_id, key)`, `key` — значения ключевых полей
  через unit separator (`\x1f`).

Ошибки транспорта — `HttpSourceError` (подкласс `RuntimeError`): статус
`>= 400`, недоступный хост, невалидный JSON, ответ не массив объектов.
Продукт печатает причину и завершает прогон, не трогая хранилище.

Проверки — `run_source_sync_contract` из ядра, тот же набор, что у
`filesystem` и `sql`.
