'use strict';

// Клиент AgoraLar: только чтение, только /api/v1. Никакой доменной логики —
// интерфейс показывает то, что посчитал сервер (ADR-0019).
//
// Состояние выборки живёт в URL (?f=...&limit=...&offset=...): страницу можно
// перезагрузить, переслать ссылкой и вернуться кнопкой «назад» без потери
// фильтров.

const state = {
  offset: 0,
  limit: 50,
  filters: [],
  total: 0,
  selected: null,
  entity: null,
  etag: null,
  axes: [],
  scalars: [],
  checked: new Set(),
};

//: Сколько объектов можно выделить для массовой правки за раз.
const BATCH_LIMIT = 500;

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

  const box = document.createElement('input');
  box.type = 'checkbox';
  box.className = 'pick';
  box.checked = state.checked.has(row.id);
  box.addEventListener('click', (event) => {
    event.stopPropagation();
    if (box.checked) state.checked.add(row.id);
    else state.checked.delete(row.id);
    updateBatch();
  });
  item.append(box, label, kind, id);
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
    const visible = new Set(page.objects.map((row) => row.id));
    for (const id of [...state.checked]) {
      if (!visible.has(id)) state.checked.delete(id);
    }
    for (const row of page.objects) list.append(objectRow(row));
    updateBatch();
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
    const [response, edges] = await Promise.all([
      fetch(`/api/v1/objects/${encodeURIComponent(id)}`, {
        headers: { Accept: 'application/json' },
      }),
      api(`/api/v1/objects/${encodeURIComponent(id)}/links`),
    ]);
    const object = await response.json();
    if (!response.ok) throw new Error(object.error?.message || `HTTP ${response.status}`);
    state.entity = object.entity;
    state.etag = response.headers.get('ETag');
    renderCard(card);
    links.replaceChildren();
    addLinkRow(links, 'входящие', edges.incoming, 'in');
    addLinkRow(links, 'исходящие', edges.outgoing, 'out');
  } catch (error) {
    showError(card, error.message);
  }
}

function renderCard(card) {
  const entity = state.entity;
  card.replaceChildren();
  const title = document.createElement('p');
  const strong = document.createElement('strong');
  strong.textContent = entity.scalars.title || entity.id;
  title.append(strong, ` · ${entity.kind} `);
  const edit = document.createElement('button');
  edit.type = 'button';
  edit.textContent = 'править';
  edit.addEventListener('click', () => openEditor(card));
  const remove = document.createElement('button');
  remove.type = 'button';
  remove.className = 'danger';
  remove.textContent = 'удалить';
  remove.addEventListener('click', () => removeObject(card));
  const upload = document.createElement('input');
  upload.type = 'file';
  upload.className = 'upload';
  upload.addEventListener('change', () => uploadArtifact(card, upload));
  title.append(edit, remove, upload);
  card.append(title);

  const facets = document.createElement('p');
  facets.className = 'muted';
  const axes = Object.entries(entity.facets || {});
  facets.textContent = axes.length
    ? axes.map(([axis, values]) => `${axis}: ${values.map((v) => v.value ?? v.target).join(', ')}`).join(' · ')
    : 'фасетов нет (разреженный объект)';
  card.append(facets);

  $('raw').textContent = JSON.stringify(entity, null, 2);
  $('raw-wrap').hidden = false;
}

// ------------------------------------------------- создание/удаление (3.3)

function scalarInput(name, value, label) {
  const row = document.createElement('label');
  row.className = 'field';
  const caption = document.createElement('span');
  caption.textContent = label || name;
  const input = document.createElement('input');
  input.value = value ?? '';
  input.dataset.scalar = name;
  row.append(caption, input);
  return row;
}

function openCreate() {
  const mask = document.createElement('div');
  mask.className = 'dialog-mask';
  const form = document.createElement('form');
  form.className = 'dialog editor';
  const heading = document.createElement('p');
  heading.textContent = 'новый объект';
  form.append(heading);

  const kindRow = document.createElement('label');
  kindRow.className = 'field';
  const kindName = document.createElement('span');
  kindName.textContent = 'вид (kind)';
  const kind = document.createElement('input');
  kind.value = 'note';
  kind.required = true;
  kindRow.append(kindName, kind);
  form.append(kindRow);
  for (const scalar of state.scalars) form.append(scalarInput(scalar.name, ''));

  const actions = document.createElement('div');
  actions.className = 'dialog-actions';
  const submit = document.createElement('button');
  submit.type = 'submit';
  submit.textContent = 'создать';
  const cancel = document.createElement('button');
  cancel.type = 'button';
  cancel.textContent = 'отмена';
  cancel.addEventListener('click', () => mask.remove());
  actions.append(submit, cancel);
  form.append(actions);

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const payload = { kind: kind.value.trim(), scalars: {} };
    for (const input of form.querySelectorAll('input[data-scalar]')) {
      const value = input.value.trim();
      if (value) payload.scalars[input.dataset.scalar] = value;
    }
    const response = await fetch('/api/v1/objects', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const reply = await response.json().catch(() => ({}));
    if (!response.ok) {
      batchResult(reply.error?.detail || reply.error?.message || `HTTP ${response.status}`, true);
      return;
    }
    mask.remove();
    await loadObjects();
    await loadStats();
    await selectObject(reply.id);
  });

  mask.append(form);
  mask.addEventListener('click', (event) => {
    if (event.target === mask) mask.remove();
  });
  document.body.append(mask);
  const first = form.querySelector('input');
  if (first) first.focus();
}

async function removeObject(card) {
  if (!state.entity) return;
  const ok = window.confirm(`удалить объект ${state.entity.id.slice(0, 8)}?`);
  if (!ok) return;
  const headers = {};
  if (state.etag) headers['If-Match'] = state.etag;
  const response = await fetch(`/api/v1/objects/${encodeURIComponent(state.entity.id)}`, {
    method: 'DELETE',
    headers,
  });
  const reply = await response.json().catch(() => ({}));
  if (response.status === 409 && reply.error?.code === 'reference_in_use') {
    const axis = reply.error.axis ? ` (ось ${reply.error.axis})` : '';
    showError(card, `нельзя удалить: ${reply.error.message}${axis}`);
    return;
  }
  if (!response.ok) {
    showError(card, reply.error?.message || `HTTP ${response.status}`);
    return;
  }
  state.entity = null;
  state.etag = null;
  state.selected = null;
  card.replaceChildren();
  const note = document.createElement('p');
  note.className = 'muted';
  note.textContent = 'объект удалён (tombstone) — «отменить» вернёт его';
  card.append(note);
  await loadObjects();
  await loadStats();
}

async function uploadArtifact(card, input) {
  const file = input.files && input.files[0];
  if (!file || !state.entity) return;
  const buffer = await file.arrayBuffer();
  let binary = '';
  const bytes = new Uint8Array(buffer);
  for (const byte of bytes) binary += String.fromCharCode(byte);
  const response = await fetch('/api/v1/artifacts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      media_type: file.type || 'application/octet-stream',
      data: btoa(binary),
    }),
  });
  const uploaded = await response.json().catch(() => ({}));
  if (!response.ok) {
    showError(card, uploaded.error?.message || `HTTP ${response.status}`);
    return;
  }
  const payload = JSON.parse(JSON.stringify(state.entity));
  payload.artifacts = payload.artifacts || {};
  payload.artifacts.file = {
    content_hash: uploaded.content_hash,
    media_type: uploaded.media_type,
    size: uploaded.size,
  };
  const result = await saveEntity(state.entity.id, payload, state.etag);
  if (result.ok) {
    await selectObject(state.entity.id);
    return;
  }
  if (result.conflict) {
    showError(card, 'объект изменили: перечитайте карточку и загрузите файл снова');
    return;
  }
  showError(card, conflictMessage(result));
}

// ---------------------------------------------------------- граф (4.2)

const SVG_NS = 'http://www.w3.org/2000/svg';

function svgNode(name, attrs) {
  const node = document.createElementNS(SVG_NS, name);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
  return node;
}

function graphAxes() {
  const box = $('graph-axes');
  if (box.dataset.ready === 'yes') return;
  box.replaceChildren();
  for (const axis of state.axes.filter((item) => item.kind === 'reference')) {
    const label = document.createElement('label');
    label.className = 'inline';
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.value = axis.name;
    input.checked = true;
    input.dataset.graphAxis = axis.name;
    label.append(input, ` ${axis.name}`);
    box.append(label);
  }
  box.dataset.ready = 'yes';
}

function graphAxesSelected() {
  return [...document.querySelectorAll('input[data-graph-axis]:checked')].map((i) => i.value);
}

function layout(window) {
  const adjacency = new Map();
  for (const node of window.nodes) adjacency.set(node.id, new Set());
  for (const edge of window.edges) {
    adjacency.get(edge.source)?.add(edge.target);
    adjacency.get(edge.target)?.add(edge.source);
  }
  const root = window.nodes[0].id;
  const levels = new Map([[root, 0]]);
  const queue = [root];
  while (queue.length) {
    const current = queue.shift();
    for (const next of adjacency.get(current) || []) {
      if (!levels.has(next)) {
        levels.set(next, levels.get(current) + 1);
        queue.push(next);
      }
    }
  }
  const byLevel = new Map();
  for (const node of window.nodes) {
    const level = levels.has(node.id) ? levels.get(node.id) : 1;
    if (!byLevel.has(level)) byLevel.set(level, []);
    byLevel.get(level).push(node.id);
  }
  const positions = new Map();
  for (const [level, ids] of byLevel) {
    ids.forEach((id, index) => {
      const angle = (2 * Math.PI * index) / ids.length - Math.PI / 2;
      const radius = level * 110;
      positions.set(id, {
        x: 400 + radius * Math.cos(angle),
        y: 180 + radius * Math.sin(angle),
      });
    });
  }
  return positions;
}

function renderGraph(window) {
  const svg = $('graph');
  svg.replaceChildren();
  const positions = layout(window);
  for (const edge of window.edges) {
    const from = positions.get(edge.source);
    const to = positions.get(edge.target);
    if (!from || !to) continue;
    const line = svgNode('line', {
      x1: from.x, y1: from.y, x2: to.x, y2: to.y,
      stroke: edge.dangling ? '#b00020' : '#9aa0a6',
      'stroke-width': edge.dangling ? 2 : 1,
      'stroke-dasharray': edge.dangling ? '4 3' : '',
    });
    const title = svgNode('title', {});
    title.textContent = edge.axis;
    line.append(title);
    svg.append(line);
  }
  for (const node of window.nodes) {
    const position = positions.get(node.id);
    if (!position) continue;
    const group = svgNode('g', { transform: `translate(${position.x},${position.y})` });
    group.style.cursor = 'pointer';
    const circle = svgNode('circle', {
      r: node.ghost ? 6 : 9,
      fill: node.ghost ? '#fff' : '#1a73e8',
      stroke: node.ghost ? '#b00020' : '#1a73e8',
      'stroke-dasharray': node.ghost ? '3 2' : '',
    });
    const text = svgNode('text', { x: 12, y: 4, 'font-size': 10, fill: '#333' });
    text.textContent = node.label.slice(0, 24);
    group.append(circle, text);
    group.addEventListener('click', () => {
      if (!node.ghost) void selectObject(node.id);
    });
    svg.append(group);
  }
  const hint = $('graph-hint');
  hint.textContent = window.ghost_count
    ? `узлов ${window.total}${window.truncated ? ' (обрезано)' : ''}, из них призраков: ${window.ghost_count}`
    : `узлов ${window.total}${window.truncated ? ' (обрезано)' : ''}`;
}

async function openGraph(event) {
  event.preventDefault();
  graphAxes();
  const root = state.selected;
  if (!root) {
    $('graph-hint').textContent = 'сначала выберите объект в списке';
    return;
  }
  const params = new URLSearchParams();
  params.set('ids', root);
  params.set('depth', $('graph-depth').value);
  params.set('direction', $('graph-direction').value);
  const axes = graphAxesSelected();
  if (axes.length) params.set('axes', axes.join(','));
  try {
    const window = await api(`/api/v1/graph?${params}`);
    window.ghost_count = window.nodes.filter((node) => node.ghost).length;
    renderGraph(window);
  } catch (error) {
    $('graph-hint').textContent = error.message;
  }
}

// ---------------------------------------------------- синхронизация (3.5)

async function waitJob(id) {
  const line = $('batch-result');
  const cancel = $('sync-cancel');
  cancel.hidden = false;
  cancel.dataset.job = id;
  for (let attempt = 0; attempt < 600; attempt += 1) {
    const job = await api(`/api/v1/jobs/${encodeURIComponent(id)}`).then((r) => r.job);
    if (job.status === 'done') {
      cancel.hidden = true;
      batchResult(`синхронизация: просканировано ${job.scanned}, применено ${job.applied}`, false);
      await loadObjects();
      await loadStats();
      if (state.selected) await selectObject(state.selected);
      return;
    }
    if (job.status === 'failed') {
      cancel.hidden = true;
      batchResult(`синхронизация не удалась: ${job.error}`, true);
      return;
    }
    if (job.status === 'cancelled') {
      cancel.hidden = true;
      batchResult(`синхронизация отменена: просканировано ${job.scanned}`, false);
      await loadObjects();
      await loadStats();
      return;
    }
    line.hidden = false;
    line.className = 'hint';
    line.textContent = `синхронизация идёт… ${job.scanned ? `найдено ${job.scanned}` : ''}`;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  batchResult('синхронизация не завершилась за 5 минут — откройте задачу заново', true);
}

async function openSync() {
  const { sources } = await api('/api/v1/sources');
  const mask = document.createElement('div');
  mask.className = 'dialog-mask';
  const box = document.createElement('div');
  box.className = 'dialog';
  const text = document.createElement('p');
  text.textContent = 'синхронизировать источник';
  const select = document.createElement('select');
  for (const source of sources) {
    const option = document.createElement('option');
    option.value = source.name;
    const configured = Object.keys(source.options).length ? '' : ' (не настроен)';
    option.textContent = `${source.name}${configured}`;
    select.append(option);
  }
  const actions = document.createElement('div');
  actions.className = 'dialog-actions';
  const start = document.createElement('button');
  start.type = 'button';
  start.textContent = 'запустить';
  const cancel = document.createElement('button');
  cancel.type = 'button';
  cancel.textContent = 'отмена';
  cancel.addEventListener('click', () => mask.remove());
  start.addEventListener('click', async () => {
    const response = await fetch('/api/v1/sync', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source: select.value }),
    });
    const reply = await response.json().catch(() => ({}));
    if (!response.ok) {
      batchResult(reply.error?.message || `HTTP ${response.status}`, true);
      return;
    }
    mask.remove();
    batchResult('синхронизация запущена', false);
    await waitJob(reply.job.id);
  });
  actions.append(start, cancel);
  box.append(text, select, actions);
  mask.append(box);
  mask.addEventListener('click', (event) => {
    if (event.target === mask) mask.remove();
  });
  document.body.append(mask);
}

// ------------------------------------------------------- массовая правка (3.2)

function updateBatch() {
  const panel = $('batch');
  panel.hidden = state.checked.size === 0;
  $('batch-count').textContent = String(state.checked.size);
}

function batchResult(text, isError) {
  const line = $('batch-result');
  line.hidden = !text;
  line.textContent = text || '';
  line.className = isError ? 'err' : 'hint';
}

async function applyBatch() {
  const axis = $('batch-axis').value;
  const mode = $('batch-mode').value;
  const raw = $('batch-values').value.trim();
  const force = $('batch-force').checked;
  if (!axis) return;
  if (state.checked.size > BATCH_LIMIT) {
    batchResult(`за раз можно править не больше ${BATCH_LIMIT} объектов`, true);
    return;
  }
  if (mode !== 'unset' && !raw) {
    batchResult('нужны значения через запятую', true);
    return;
  }
  const values = mode === 'unset' ? [] : raw.split(',').map((v) => v.trim()).filter(Boolean);
  const body = {
    ids: [...state.checked],
    ops: [{ axis, mode, values }],
    force,
  };
  batchResult('применяю…', false);
  const response = await fetch('/api/v1/patch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    batchResult(payload.error?.message || `HTTP ${response.status}`, true);
    return;
  }
  const skipped = payload.skipped.length
    ? ` · пропущено (фасет источника): ${[...new Set(payload.skipped.map((s) => s.axis))].join(', ')}`
    : '';
  const conflicts = payload.conflicts.length ? ` · конфликтов: ${payload.conflicts.length}` : '';
  const errors = payload.errors.length ? ` · ошибок: ${payload.errors.length}` : '';
  batchResult(`изменено: ${payload.changed.length}${conflicts}${skipped}${errors}`, false);
  await loadObjects();
  await loadStats();
}

async function undoLast() {
  const response = await fetch('/api/v1/undo', { method: 'POST' });
  const payload = await response.json().catch(() => ({}));
  if (response.status === 404) {
    batchResult('откатывать нечего', false);
    return;
  }
  if (!response.ok) {
    batchResult(payload.error?.message || `HTTP ${response.status}`, true);
    return;
  }
  batchResult(`отменено: ${payload.undone.kind} · ${payload.undone.entity_id.slice(0, 8)}`, false);
  await loadObjects();
  await loadStats();
  if (state.selected) await selectObject(state.selected);
}

// --------------------------------------------------------------- правка (3.1)

function axisValues(axis, values) {
  return (values || []).map((value) => value.value ?? value.target ?? '').join(', ');
}

function parseValues(axis, raw) {
  const pieces = raw
    .split(',')
    .map((piece) => piece.trim())
    .filter(Boolean);
  if (axis.kind === 'measurable') {
    return pieces.map((piece) => {
      const number = Number(piece);
      if (!Number.isFinite(number)) throw new Error(`${axis.name}: ожидается число`);
      return { kind: 'measurable', value: number };
    });
  }
  if (axis.kind === 'reference') {
    return pieces.map((piece) => ({ kind: 'reference', target: piece }));
  }
  return pieces.map((piece) => ({ kind: 'categorical', value: piece }));
}

function fieldError(text) {
  const error = document.createElement('span');
  error.className = 'field-error';
  error.textContent = text;
  return error;
}

function openEditor(card) {
  const entity = state.entity;
  const form = document.createElement('form');
  form.className = 'editor';

  for (const axis of state.axes) {
    const row = document.createElement('label');
    row.className = 'field';
    const name = document.createElement('span');
    name.textContent = `${axis.name} (${axis.kind})`;
    const input = document.createElement('input');
    input.value = axisValues(axis, entity.facets?.[axis.name]);
    input.placeholder = 'через запятую; пусто — значение отсутствует';
    input.dataset.axis = axis.name;
    row.append(name, input);
    form.append(row);
  }
  for (const scalar of state.scalars) {
    const row = document.createElement('label');
    row.className = 'field';
    const name = document.createElement('span');
    name.textContent = `${scalar.name} (скаляр)`;
    const input = document.createElement('input');
    input.value = entity.scalars?.[scalar.name] ?? '';
    input.dataset.scalar = scalar.name;
    row.append(name, input);
    form.append(row);
  }

  const actions = document.createElement('div');
  actions.className = 'editor-actions';
  const save = document.createElement('button');
  save.type = 'submit';
  save.textContent = 'сохранить';
  const cancel = document.createElement('button');
  cancel.type = 'button';
  cancel.textContent = 'отмена';
  cancel.addEventListener('click', () => renderCard(card));
  actions.append(save, cancel);
  form.append(actions);

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    for (const node of form.querySelectorAll('.field-error')) node.remove();
    const payload = {
      kind: entity.kind,
      facets: {},
      scalars: {},
      artifacts: entity.artifacts || {},
    };
    let invalid = false;
    for (const input of form.querySelectorAll('input[data-axis]')) {
      const axis = state.axes.find((item) => item.name === input.dataset.axis);
      try {
        const values = parseValues(axis, input.value);
        if (values.length) payload.facets[axis.name] = values;
      } catch (error) {
        input.after(fieldError(error.message));
        invalid = true;
      }
    }
    for (const input of form.querySelectorAll('input[data-scalar]')) {
      const value = input.value.trim();
      if (value) payload.scalars[input.dataset.scalar] = value;
    }
    if (invalid) return;
    const result = await saveEntity(entity.id, payload);
    if (result.ok) {
      await selectObject(entity.id);
      return;
    }
    if (result.conflict) {
      const choice = await askConflict(result.conflict.current);
      if (choice === 'mine') {
        const retry = await saveEntity(entity.id, payload, result.conflict.current?.etag);
        if (retry.ok) await selectObject(entity.id);
        else showError(card, conflictMessage(retry));
      } else if (choice === 'theirs') {
        await selectObject(entity.id);
      }
      return;
    }
    for (const error of result.errors || []) {
      if (error.axis) {
        const input = form.querySelector(`input[data-axis="${error.axis}"]`);
        if (input) {
          input.after(fieldError(error.detail || error.code || 'ошибка'));
          continue;
        }
      }
      showError(card, error.detail || error.message || 'не удалось сохранить');
    }
  });

  card.replaceChildren(form);
  const first = form.querySelector('input');
  if (first) first.focus();
}

async function saveEntity(id, payload, etag) {
  const headers = { 'Content-Type': 'application/json' };
  if (etag) headers['If-Match'] = etag;
  const response = await fetch(`/api/v1/objects/${encodeURIComponent(id)}`, {
    method: 'PUT',
    headers,
    body: JSON.stringify(payload),
  });
  const body = await response.json().catch(() => ({}));
  if (response.status === 409) return { conflict: body };
  if (!response.ok) return { errors: body.errors || [body.error || {}] };
  return { ok: body };
}

function conflictMessage(result) {
  const error = (result.errors || [])[0] || {};
  return error.detail || error.message || 'конфликт: объект изменился';
}

function askConflict(current) {
  return new Promise((resolve) => {
    const mask = document.createElement('div');
    mask.className = 'dialog-mask';
    const box = document.createElement('div');
    box.className = 'dialog';
    const text = document.createElement('p');
    text.textContent = 'Объект изменили, пока вы правили. Ваша правка не сохранена.';
    const theirs = document.createElement('pre');
    theirs.textContent = current
      ? JSON.stringify(current.entity.facets || {}, null, 2)
      : 'текущее состояние недоступно';
    const actions = document.createElement('div');
    actions.className = 'dialog-actions';
    const mine = document.createElement('button');
    mine.type = 'button';
    mine.textContent = 'всё равно сохранить моё';
    const take = document.createElement('button');
    take.type = 'button';
    take.textContent = 'взять серверное';
    const close = () => {
      mask.remove();
      resolve(null);
    };
    mine.addEventListener('click', () => {
      mask.remove();
      resolve('mine');
    });
    take.addEventListener('click', () => {
      mask.remove();
      resolve('theirs');
    });
    actions.append(mine, take);
    box.append(text, theirs, actions);
    mask.append(box);
    mask.addEventListener('click', (event) => {
      if (event.target === mask) close();
    });
    document.body.append(mask);
  });
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
    state.axes = schema.schema.axes || [];
    state.scalars = schema.schema.scalars || [];
    const batchAxis = $('batch-axis');
    batchAxis.replaceChildren();
    for (const axis of state.axes) {
      const option = document.createElement('option');
      option.value = axis.name;
      option.textContent = axis.multi ? `${axis.name} (multi)` : axis.name;
      batchAxis.append(option);
    }
    const list = $('axis-list');
    list.replaceChildren();
    for (const axis of state.axes) {
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
$('create').addEventListener('click', () => openCreate());
$('sync').addEventListener('click', () => {
  void openSync();
});
$('graph-controls').addEventListener('submit', (event) => {
  void openGraph(event);
});
$('sync-cancel').addEventListener('click', () => {
  const job = $('sync-cancel').dataset.job;
  if (!job) return;
  void fetch(`/api/v1/jobs/${encodeURIComponent(job)}/cancel`, { method: 'POST' });
});
$('batch-apply').addEventListener('click', () => {
  void applyBatch();
});
$('batch-undo').addEventListener('click', () => {
  void undoLast();
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
