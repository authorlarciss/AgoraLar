# agoralar-analyzer-text

Анализатор для [AgoraLar](https://github.com/authorlarciss/AgoraLar):
текстовый документ сводится к короткому предпросмотру
(`text/plain; charset=utf-8`), который прикрепляется к объекту под ролью
`preview`.

Плагин продукта (вид `analyzer`, entry point `agoralar.analyzer`).
Не зависит ни от ядра, ни от продукта: интерфейс структурный, данные —
обычные (`роль, байты, media_type`).

```toml
[plugins.text]
limit = 200                    # символов, не байтов
role = "preview"               # под каким именем прикрепить результат
media_types = ["text/", "application/json", "application/xml", "application/yaml"]
encoding = "utf-8"
```

Запуск (из продукта):

```bash
agoralar analyze --analyzer text --input-role original --apply
agoralar analyze --analyzer text --output-role preview --force --apply
```

Что важно:

- предпросмотр — **производный** артефакт: продукт пишет
  `derived_by = {plugin: "text", version: "0.1.0"}`; смена версии
  алгоритма в плагине меняет провенанс, а не «тихо» подменяет значение;
- вход читается как UTF-8 с `errors="replace"`: битый файл не роняет
  прогон;
- обрезка идёт по символам, поэтому многобайтовые тексты не рвутся
  посередине;
- анализатор не решает, куда писать: продукт считает адрес артефакта
  (`sha256`) и дедуплицирует байты сам.
