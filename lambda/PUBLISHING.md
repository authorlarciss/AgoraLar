# Публикация пакетов

Публикация — единственный шаг, который требует твоих личных учётных данных:
аккаунта и токена. Всё остальное (сборка, порядок, проверки, установка)
автоматизировано: `scripts/publish.sh` / `make publish*`.

## 1. Где берутся доступы

**PyPI (боевой индекс)**

1. Аккаунт: <https://pypi.org/account/register/> — почта, пароль, подтвердить
   письмо. Аккаунт бесплатный, один на человека.
2. **2FA обязательна**: <https://pypi.org/manage/account/> → *Two-factor
   authentication* → добавить TOTP (любое приложение-аутентификатор).
   Без 2FA токены не выдаются. Сохрани recovery-коды — иначе при потере
   телефона доступ к проектам восстанавливается только через поддержку.
3. **API-токен**: <https://pypi.org/manage/account/token/> → *Add API token*.
   - Имя: `agoralar-bootstrap`.
   - Scope: **Entire account** — на первую загрузку, потому что проектов
     `axislar-core`, `agoralar`, `agoralar-*` ещё не существует и выбрать их
     в списке нельзя.
   - Токен показывается **один раз** (начинается с `pypi-`). Скопируй сразу.
4. После первой публикации заведи отдельные токены на каждый проект
   (Project → *Manage* → *Settings* → *API tokens*, scope — этот проект) и
   удали bootstrap-токен. Так утечка одного токена не открывает всё.

**TestPyPI (тренировочный индекс, отдельная база)**

- Аккаунт: <https://test.pypi.org/account/register/> — это **другая** учётная
  запись, логин с PyPI не подходит.
- Токен: <https://test.pypi.org/manage/account/token/> — так же, Entire account.
- Проекты там живут отдельно от боевых, имена можно занимать те же.

**Секретов в репозитории нет и не будет.** Токен передаётся только через
переменную окружения.

## 2. Как передать токен инструменту

```bash
read -rs UV_PUBLISH_TOKEN && export UV_PUBLISH_TOKEN   # ввод не виден и не в history
# или: export UV_PUBLISH_TOKEN="$(cat ~/.config/pypi-token)"
```

Не вставляй токен в чат, в коммиты, в `pyproject.toml` и в CI-секреты без
необходимости. Проверить, что переменная есть: `test -n "$UV_PUBLISH_TOKEN" && echo ok`.

## 3. Порядок публикации

```bash
make publish-check    # сборка + twine check метаданных, без загрузки
make publish-test     # TestPyPI (нужен токен TestPyPI)
make publish          # боявой PyPI (нужен токен PyPI)
```

Скрипт публикует в правильном порядке: **ядро `axislar-core` → плагины →
`agoralar`**, потому что пакеты ссылаются друг на друга по именам.
`--check-url` пропускает уже загруженные файлы: повторный запуск не падает
на «File already exists».

Проверка после TestPyPI (в чистом окружении, TestPyPI требует
дополнительный индекс для зависимостей):

```bash
uv venv /tmp/t && uv pip install --python /tmp/t/bin/python \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ agoralar
/tmp/t/bin/agoralar plugins list
```

После боевой публикации:

```bash
uv venv /tmp/p && uv pip install --python /tmp/p/bin/python agoralar
/tmp/p/bin/agoralar plugins list
```

## 4. Trusted Publishing (без токенов) — для следующих релизов

PyPI умеет пускать CI по OIDC: токен не хранится нигде, GitHub выдаёт
кратковременный пропуск.

1. Для уже существующего проекта: *Manage* → *Publishing* → *Add a new
   publisher* → GitHub: owner `authorlarciss`, repository `AgoraLar`,
   workflow `ci.yml`, environment `release`.
2. Для проекта, которого ещё нет: *Publishing* → *Pending publisher* с теми
   же полями (имя проекта вводится вручную).
3. В workflow добавить job с `permissions: id-token: write` и
   `uv publish --trusted-publishing always`.
4. Публикация идёт по тегу; в этом репозитории push-события сейчас не
   создают прогоны, поэтому запускать вручную:
   `gh workflow run ci.yml --ref v0.14.2`.

Пока этого нет — работает путь с токеном из раздела 3.

## 5. Ограничения и подводные камни

- **Имена занимаются навсегда.** `agoralar`, `axislar-core` и имена плагинов
  после первой загрузки удалить из индекса нельзя (можно только выпустить
  новую версию). Все имена проверены как свободные.
- **Версию не перезаписать.** Опечатку в метаданных исправляют новой
  версией (у нас — bump patch), а не повторной загрузкой.
- **Первый токен — на весь аккаунт.** Это неизбежно для новых проектов;
  после первой загрузки сузь права до project-scoped.
- **TestPyPI не гарантирует долговечность**: файлы там могут удаляться,
  индекс — только для тренировки.
- `uv publish` берёт токен из `UV_PUBLISH_TOKEN`; `--check-url` нужен именно
  для идемпотентности, без него повторный запуск падает.
