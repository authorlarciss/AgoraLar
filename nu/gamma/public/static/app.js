'use strict';

// Клиент AgoraLar: только чтение, только /api/v1. Никакой доменной логики —
// интерфейс показывает то, что посчитал сервер (ADR-0019).
//
// Состояние выборки живёт в URL (?f=...&limit=...&offset=...): страницу можно
// перезагрузить, переслать ссылкой и вернуться кнопкой «назад» без потери
// фильтров.

const state = { offset: 0, limit: 50, filters: [], total: 0, selected: null };

const $ = (id) => document.getElementById(id);

function showError(target, message) {
  target.replaceChildren();
  const p = document.createElement('p');
  p.className = 'err';
  p.textContent = message;
  target.append(p);
}

async function api(path) {
  const response = await fetch(path, { headers: { Accept: 'application/json' } });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = payload.error || {};
    throw new Error(error.message || `HTTP ${response.status}`);
  }
  return payload;
}

// ------------------------------------------------------------------ URL

function readUrl() {
  const params = new URLSearchParams(location.search);
  state.filters = params.getAll('f');
  const limit = Number(params.get('limit'));
  state.limit = Number.isFinite(limit) && limit > 0 ? Math.min(limit, 200) : 50;
  const offset = Number(params.get('offset'));
  state.offset = Number.isFinite(offset) && offset > 0 ? offset : 0;
}

function writeUrl(push) {
  const params = new URLSearchParams();
  for (const token of state.filters) params.append('f', token);
  if (state.limit !== 50) params.set('limit', String(state.limit));
  if (state.offset) params.set('offset', String(state.offset));
  const query = params.toString();
  const url = query ? `?${query}` : location.pathname;
  if (url === location.search) return;
  if (push) history.pushState(null, '', url);
  else history.replaceState(null, '', url);
}

// ---------------------------------------------------------------- фильтры

function renderChips() {
  const box = $('chips');
  box.replaceChildren();
  if (!state.filters.length) {
    const hint = document.createElement('span');
    hint.className = 'muted';
    hint.textContent = 'фильтров нет — показаны все объекты';
    box.append(hint);
    return;
  }
  state.filters.forEach((token, index) => {
    const chip = document.createElement('span');
    chip.className = 'chip';
    const label = document.createElement('span');
    label.textContent = token;
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'chip-remove';
    remove.title = 'убрать фильтр';
    remove.textContent = '×';
    remove.addEventListener('click', () => {
      state.filters.splice(index, 1);
      state.offset = 0;
      writeUrl(true);
      renderChips();
      loadObjects();
    });
    chip.append(label, remove);
    box.append(chip);
  });
}

function addFilter(token) {
  const clean = token.trim();
  if (!clean) return;
  if (!/^[^\s:=]+:(\w+)(=.*)?$/.test(clean)) {
    $('filter-hint').textContent =
      'ожидается axis:op=значения, например collection:contains=noir или tags:exists';
    return;
  }
  if (state.filters.includes(clean)) return;
  state.filters.push(clean);
  state.offset = 0;
  writeUrl(true);
  renderChips();
  loadObjects();
}

function filterTokens() {
  return state.filters;
}

// ------------------------------------------------------------------ список

function objectRow(row) {
  const item = document.createElement('div');
  item.className = 'item';
  item.dataset.id = row.id;

  const label = document.createElement('span');
  label.textContent = row.title || row.kind || '(без названия)';
  const kind = document.createElement('span');
  kind.className = 'kind';
  kind.textContent = row.kind;
  const id = document.createElement('code');
  id.textContent = row.id.slice(0, 8);

  item.append(label, kind, id);
  item.addEventListener('click', () => selectObject(row.id));
  return item;
}

async function loadObjects() {
  const params = new URLSearchParams();
  for (const token of filterTokens()) params.append('filter', token);
  params.set('offset', String(state.offset));
  params.set('limit', String(state.limit));

  const list = $('objects');
  try {
    const page = await api(`/api/v1/objects?${params}`);
    state.total = page.total;
    list.replaceChildren();
    if (!page.objects.length) {
      const empty = document.createElement('p');
      empty.className = 'muted';
      empty.textContent = 'ничего не найдено';
      list.append(empty);
    }
    for (const row of page.objects) list.append(objectRow(row));
    const from = page.total ? page.offset + 1 : 0;
    const to = page.offset + page.objects.length;
    $('range').textContent = `${from}–${to} из ${page.total}`;
    $('prev').disabled = page.offset === 0;
    $('next').disabled = page.offset + page.objects.length >= page.total;
  } catch (error) {
    showError(list, error.message);
  }
}

// ------------------------------------------------------------------ карточка

function addLinkRow(parent, title, rows, direction) {
  const heading = document.createElement('h3');
  heading.textContent = `${title} (${rows.length})`;
  parent.append(heading);
  if (!rows.length) {
    const empty = document.createElement('p');
    empty.className = 'muted';
    empty.textContent =
      direction === 'in' ? 'на объект никто не ссылается' : 'исходящих ссылок нет';
    parent.append(empty);
    return;
  }
  const list = document.createElement('ul');
  for (const row of rows) {
    const item = document.createElement('li');
    const link = document.createElement('a');
    link.href = '#';
    link.textContent = row.title || row.id.slice(0, 8);
    link.addEventListener('click', (event) => {
      event.preventDefault();
      selectObject(row.id);
    });
    item.append(`${row.axis}: `, link);
    list.append(item);
  }
  parent.append(list);
}

async function selectObject(id) {
  state.selected = id;
  for (const item of document.querySelectorAll('.item')) {
    item.classList.toggle('active', item.dataset.id === id);
  }
  const card = $('card');
  const links = $('links');
  try {
    const [object, edges] = await Promise.all([
      api(`/api/v1/objects/${encodeURIComponent(id)}`),
      api(`/api/v1/objects/${encodeURIComponent(id)}/links`),
    ]);
    const entity = object.entity;
    card.replaceChildren();
    const title = document.createElement('p');
    const strong = document.createElement('strong');
    strong.textContent = entity.scalars.title || entity.id;
    title.append(strong, ` · ${entity.kind}`);
    card.append(title);

    const facets = document.createElement('p');
    facets.className = 'muted';
    const axes = Object.entries(entity.facets || {});
    facets.textContent = axes.length
      ? axes
          .map(([axis, values]) => `${axis}: ${values.map((v) => v.value ?? v.target).join(', ')}`)
          .join(' · ')
      : 'фасетов нет (разреженный объект)';
    card.append(facets);

    $('raw').textContent = JSON.stringify(entity, null, 2);
    $('raw-wrap').hidden = false;

    links.replaceChildren();
    addLinkRow(links, 'входящие', edges.incoming, 'in');
    addLinkRow(links, 'исходящие', edges.outgoing, 'out');
  } catch (error) {
    showError(card, error.message);
  }
}

// ------------------------------------------------------------------ статистика

function distribution(axis) {
  if (axis.kind === 'measurable') {
    if (axis.min === null) return document.createTextNode('—');
    return document.createTextNode(`min ${axis.min} · медиана ${axis.median} · max ${axis.max}`);
  }
  if (!axis.top || !axis.top.length) return document.createTextNode('—');
  const list = document.createElement('ul');
  list.className = 'dist';
  const peak = axis.top[0][1] || 1;
  for (const [value, count] of axis.top) {
    const item = document.createElement('li');
    const label = document.createElement('span');
    label.className = 'value';
    label.textContent = value;
    const bar = document.createElement('span');
    bar.className = 'bar';
    bar.style.width = `${Math.max(2, (count / peak) * 100)}%`;
    const num = document.createElement('span');
    num.className = 'pct';
    num.textContent = String(count);
    item.append(label, bar, num);
    list.append(item);
  }
  return list;
}

async function loadStats() {
  const body = document.querySelector('#stats tbody');
  try {
    const stats = await api('/api/v1/stats');
    $('meta').textContent =
      `${stats.store} · объектов ${stats.objects.visible}` +
      (stats.objects.deleted ? ` (+${stats.objects.deleted} tombstone)` : '') +
      ` · артефактов ${stats.artifacts.count}`;
    body.replaceChildren();
    for (const axis of stats.axes) {
      const row = document.createElement('tr');
      const name = document.createElement('td');
      const code = document.createElement('code');
      code.textContent = axis.name;
      name.append(code);
      const kind = document.createElement('td');
      kind.textContent = axis.multi ? `${axis.kind} · multi` : axis.kind;
      const filled = document.createElement('td');
      filled.className = 'num';
      filled.textContent = `${axis.filled} / ${stats.objects.visible}`;
      const share = document.createElement('td');
      const bar = document.createElement('span');
      bar.className = 'bar';
      bar.style.width = `${Math.max(2, axis.share)}%`;
      const pct = document.createElement('span');
      pct.className = 'pct';
      pct.textContent = `${Math.round(axis.share)}%`;
      share.append(bar, pct);
      const dist = document.createElement('td');
      dist.append(distribution(axis));
      row.append(name, kind, filled, share, dist);
      body.append(row);
    }
    const empty = stats.axes.filter((axis) => axis.filled === 0).map((axis) => axis.name);
    const warning = $('stats-warning');
    warning.hidden = empty.length === 0;
    warning.textContent = empty.length
      ? `Пустые оси: ${empty.join(', ')} — значений нет ни у одного объекта.`
      : '';
  } catch (error) {
    showError(body, error.message);
  }
}

async function loadAxes() {
  try {
    const schema = await api('/schema');
    const list = $('axis-list');
    list.replaceChildren();
    for (const axis of schema.schema.axes || []) {
      const option = document.createElement('option');
      option.value = `${axis.name}:exists`;
      list.append(option);
    }
  } catch (error) {
    $('filter-hint').textContent = error.message;
  }
}

// ------------------------------------------------------------------ события

$('filters').addEventListener('submit', (event) => {
  event.preventDefault();
  addFilter($('filter-input').value);
  $('filter-input').value = '';
});
$('clear').addEventListener('click', () => {
  state.filters = [];
  state.offset = 0;
  writeUrl(true);
  renderChips();
  loadObjects();
});
$('prev').addEventListener('click', () => {
  state.offset = Math.max(0, state.offset - state.limit);
  writeUrl(true);
  loadObjects();
});
$('next').addEventListener('click', () => {
  state.offset += state.limit;
  writeUrl(true);
  loadObjects();
});
$('limit').addEventListener('change', (event) => {
  state.limit = Number(event.target.value);
  state.offset = 0;
  writeUrl(true);
  loadObjects();
});
window.addEventListener('popstate', () => {
  readUrl();
  $('limit').value = String(state.limit);
  renderChips();
  loadObjects();
});

readUrl();
$('limit').value = String(state.limit);
renderChips();
loadAxes();
loadStats();
loadObjects();
