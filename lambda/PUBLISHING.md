# Публикация пакетов

**Статус на 2026-11-27: публикация идёт только через Trusted Publishing
(OIDC) по тегу, токен не нужен и отозван.** Проверено живыми релизами:
продукт и шесть плагинов опубликованы тегом `v0.17.1`, ядро — тегом
`v0.12.1`, job `publish` зелёный, ни одного токена в процессе.

Ниже сохранены разделы про токен: это запасной путь на случай, если OIDC
недоступен (например, сломался publisher на PyPI) или нужно загрузить
артефакт в обход CI.

Всё остальное (сборка, порядок, проверки, установка) автоматизировано:
тег → CI: job `dist` собирает и прикрепляет артефакты к релизу, job
`publish` публикует по OIDC и пропускает версии, которые уже в индексе.

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

## 2. Как передать токен инструменту (запасной путь)

```bash
read -rs UV_PUBLISH_TOKEN && export UV_PUBLISH_TOKEN   # ввод не виден и не в history
# Файла ~/.config/pypi-token больше нет: токен отозван 2026-11-27.
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

## 3a. Лимит на новые проекты (свежий аккаунт)

PyPI ограничивает создание **новых** проектов: для новых аккаунтов это
**4 проекта в 24 часа** (подтверждено в
[pypi/support#11479](https://github.com/pypi/support/issues/11479); в
документации лимит не описан). Проявляется как
`429 Too Many Requests: Too many new projects created`.

Что делать:

- подождать сутки и продолжить: `scripts/publish.sh --index pypi --resume`
  (без пересборки, уже опубликованные проекты пропускаются);
- либо попросить исключение: <https://github.com/pypi/support/issues>
  (owner-аккаунт, ссылка на проект).

Скрипт различает эти случаи: `--resume` печатает `skip` для готовых
проектов, а на лимите завершается с кодом 3 и подсказкой.

## 3b. Личные коды восстановления PyPI

PyPI выдаёт recovery-коды для 2FA (файл `PyPI-Recovery-Codes-*.txt`). Их
нельзя держать в `~/Downloads` (каталог читается всеми) и рядом с токеном:

```bash
mkdir -p ~/.config/pypi && chmod 700 ~/.config/pypi
chmod 600 ~/Downloads/PyPI-Recovery-Codes-*.txt
mv ~/Downloads/PyPI-Recovery-Codes-*.txt ~/.config/pypi/recovery-codes.txt

# зашифровать (спросит парольную фразу; её хранить отдельно от файла):
gpg --symmetric --cipher-algo AES256 ~/.config/pypi/recovery-codes.txt
gpg -d ~/.config/pypi/recovery-codes.txt.gpg | head -1   # проверить
shred -u ~/.config/pypi/recovery-codes.txt               # после проверки
```

Лучший вариант — менеджер паролей (KeePassXC/Bitwarden) или бумажная копия в
сейфе. `shred` на SSD не гарантирует затирание — при паранойе считать файл
скомпрометированным и перевыпустить коды в PyPI.

## 4. Trusted Publishing (без токенов)

PyPI пускает CI по OIDC: токен нигде не хранится, GitHub выдаёт
краткоживущий пропуск. В CI уже есть job `publish` (тег `v*`), который
публикует продукт и каждый плагин отдельным вызовом.

Что нужно сделать один раз в PyPI (для каждого из 7 проектов: `agoralar`,
`agoralar-store-json`, `agoralar-store-sqlite`,
`agoralar-source-filesystem`, `agoralar-source-sql`, `agoralar-source-http`,
`agoralar-analyzer-text`):

1. Если проект уже существует: *Manage* → *Publishing* → *Add a new
   publisher*.
2. Если проекта ещё нет: *Publishing* → *Add a pending publisher* (имя
   проекта вводится вручную).
3. Заполнить одинаково для всех: **Owner** `authorlarciss`,
   **Repository** `AgoraLar`, **Workflow** `ci.yml`,
   **Environment** `release`.

**Сделано для всех восьми проектов** (семь выше плюс `axislar-core` с
репозиторием `AxisLar`; для ядра ещё нужна переменная репозитория
`PYPI_OIDC=true`, она выставлена).

После этого публикация — тег:

```bash
git tag -a vX.Y.Z -m "..." && git push origin nu omega refs/tags/vX.Y.Z
```

CI соберёт артефакты, прикрепит их к релизу и опубликует в PyPI. Шаг
пропускает версии, которые уже есть в индексе (проверка
`/pypi/<проект>/<версия>/json`), поэтому повторный запуск и ручная
публикация больше не ломают job. Сборка воспроизводима
(`SOURCE_DATE_EPOCH` от времени коммита), так что хеши совпадают.

Лимит «4 новых проекта в 24 часа» действует и здесь: он про создание
проектов, а не про способ аутентификации.

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
