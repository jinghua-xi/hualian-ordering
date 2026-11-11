const guestMode = new URLSearchParams(location.search).get('guest') === '1';
const apiPrefix = guestMode ? '/api/guest' : '/api';
const byId = (id) => document.getElementById(id);
const messages = byId('messages');
const chatForm = byId('chat-form');
const chatMessage = byId('chat-message');
const choices = byId('choices');
const quantityInput = byId('quantity');
const dateInput = byId('delivery-date');
const noteInput = byId('item-note');
const customerInput = byId('customer');
customerInput.readOnly = !guestMode;
byId('customer-binding-hint').hidden = guestMode;
if (!guestMode) {
  customerInput.setAttribute('aria-describedby', 'customer-binding-hint');
  document.querySelector('label[for="customer"]').textContent = '客户名称（与账号绑定）';
}
const draftForm = byId('draft-form');
const addItem = byId('add-item');
const clearItem = byId('clear-draft');
const orderForm = byId('order-form');
const orderWorkspace = byId('order-workspace');
const submitOrder = byId('submit-order');
const cartItems = byId('cart-items');
const cartItemTemplate = byId('cart-item-template');
const receipt = byId('receipt');
const steps = Array.from(document.querySelectorAll('.steps li'));

const greeting = messages.querySelector('.message p').textContent;
const examples = Array.from(choices.querySelectorAll('button'), (button) => ({
  label: button.textContent,
  value: button.value,
}));
const numberFormat = new Intl.NumberFormat('zh-CN');
const chinaDate = new Intl.DateTimeFormat('en', {
  timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
});
const dayMs = 86_400_000;
const fallbackDeliveryDays = 7;
const cart = [];
const maxCartLines = 100;
const requestIds = new Map();
const maxRecoveryAttempts = 12;
const maxRecoveryLength = 768_000;
let recoveryKey = `hualian-order-recovery-v2:${guestMode ? 'guest' : 'pending'}`;

let catalog = new Map();
let history = [];
let quantityUnit = '';
let deliveryClock = null;
let workspaceReady = false;
let catalogContext = null;
let chatting = false;
let chatQuota = null;
let chatQuotaLoading = false;
let submitting = false;
let submitted = false;
let accountCustomer = '';
let reorderNoticeTimer;

const draftPicker = createProductPicker({ id: 'product-search', onChange: updateDraftProduct });
const productSearch = draftPicker.input;
productSearch.required = true;
productSearch.disabled = true;
byId('draft-product-picker').append(draftPicker.element);

function todayInChina(now = new Date()) {
  const date = Object.fromEntries(chinaDate.formatToParts(now).map(({ type, value }) => [type, value]));
  return `${date.year}-${date.month}-${date.day}`;
}

function validQuantity(value) {
  return Number.isInteger(value) && value > 0 && value <= Number(quantityInput.max);
}

function customerSku(product) {
  return product?.manufacturer_sku || product?.sku || '';
}

function productLabel(product) {
  const color = product.color && !product.name?.includes(product.color) ? product.color : '';
  return [customerSku(product), product.name, color].filter(Boolean).join(' · ');
}

function calendarDate(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value ? date : null;
}

function deliveryMinimum() {
  if (!deliveryClock) {
    return new Date(calendarDate(todayInChina()).getTime() + fallbackDeliveryDays * dayMs).toISOString().slice(0, 10);
  }
  const current = new Date(deliveryClock.now + (performance.now() - deliveryClock.syncedAt));
  const elapsed = Math.max(0, calendarDate(todayInChina(current)).getTime() - deliveryClock.today);
  return new Date(deliveryClock.minimum + elapsed).toISOString().slice(0, 10);
}

function deliveryDateHint() {
  return `请预留至少 ${deliveryClock?.days ?? fallbackDeliveryDays} 天，最快可选 ${deliveryMinimum()}。`;
}

function validDeliveryDate(value) {
  return Boolean(calendarDate(value)) && value >= deliveryMinimum();
}

function refreshDateLimits() {
  const minimum = deliveryMinimum();
  dateInput.min = minimum;
  cartItems.querySelectorAll('[data-delivery-date]').forEach((field) => { field.min = minimum; });
  byId('delivery-hint').textContent = `${deliveryDateHint()}实际交期由销售确认。`;
  const exampleBase = calendarDate(minimum).getTime();
  for (const date of document.querySelectorAll('[data-paste-example-days]')) {
    date.dateTime = new Date(exampleBase + Number(date.dataset.pasteExampleDays) * dayMs).toISOString().slice(0, 10);
    date.textContent = date.dateTime;
  }
}

function showDateWarnings() {
  if (dateInput.value && !validDeliveryDate(dateInput.value)) showError('draft-error', deliveryDateHint());
  const invalidLine = cart.find((line) => line.delivery_date && !validDeliveryDate(line.delivery_date));
  if (invalidLine) {
    const sku = customerSku(catalog.get(invalidLine.sku)) || invalidLine.sku;
    showError('order-error', `请调整 ${sku} 的期望交期。${deliveryDateHint()}`);
  }
}

async function loadDeliveryClock() {
  try {
    const signal = globalThis.AbortSignal?.timeout?.(4000);
    const response = await fetch(`${apiPrefix}/time`, { cache: 'no-store', signal });
    if (!response.ok) return;
    const clock = await response.json();
    const now = Date.parse(clock.now);
    const today = calendarDate(clock.date);
    const minimum = calendarDate(clock.min_delivery_date);
    if (clock.timezone === 'Asia/Shanghai' && Number.isFinite(now) && today && minimum &&
        Number.isInteger(clock.min_delivery_days) && clock.min_delivery_days > 0) {
      deliveryClock = { now, today: today.getTime(), minimum: minimum.getTime(), days: clock.min_delivery_days, syncedAt: performance.now() };
    }
  } catch {
    // The server still checks the date when the order is submitted.
  } finally {
    refreshDateLimits();
  }
}

function draftReady() {
  return catalog.has(draftPicker.sku) && validQuantity(quantityInput.valueAsNumber) && validDeliveryDate(dateInput.value);
}

function recoveryText(value, limit) {
  return typeof value === 'string' && value.length <= limit ? value : '';
}

function saveWorkspace() {
  if (!workspaceReady) return;
  const draft = {
    sku: draftPicker.sku,
    quantity: recoveryText(quantityInput.value, 24),
    delivery_date: recoveryText(dateInput.value, 10),
    note: recoveryText(noteInput.value, noteInput.maxLength),
    unit: quantityUnit,
  };
  const customer = recoveryText(customerInput.value, 80);
  try {
    if (submitted || (!cart.length && !customer && !requestIds.size && !Object.values(draft).some(Boolean))) {
      sessionStorage.removeItem(recoveryKey);
    } else {
      const serialized = JSON.stringify({ cart, draft, customer, attempts: [...requestIds] });
      if (serialized.length <= maxRecoveryLength) sessionStorage.setItem(recoveryKey, serialized);
    }
  } catch {
    // Browser storage may be unavailable. The current page can still submit or retry.
  }
}

function restoreWorkspace() {
  let saved;
  try {
    const serialized = sessionStorage.getItem(recoveryKey);
    if (!serialized || serialized.length > maxRecoveryLength) return;
    saved = JSON.parse(serialized);
  } catch {
    return;
  }
  if (!saved || typeof saved !== 'object') return;
  if (Array.isArray(saved.cart)) {
    for (const line of saved.cart.slice(0, maxCartLines)) {
      if (!catalog.has(line?.sku)) continue;
      cart.push({
        sku: line.sku,
        quantity: Number.isFinite(line.quantity) ? line.quantity : null,
        delivery_date: recoveryText(line.delivery_date, 10),
        note: recoveryText(line.note, noteInput.maxLength),
      });
    }
  }
  if (Array.isArray(saved.attempts)) {
    for (const attempt of saved.attempts.slice(-maxRecoveryAttempts)) {
      if (!Array.isArray(attempt)) continue;
      const [signature, id] = attempt;
      if (typeof signature === 'string' && signature.length <= 34_000 &&
          typeof id === 'string' && /^[a-zA-Z0-9-]{1,80}$/.test(id)) requestIds.set(signature, id);
    }
  }
  const draft = saved.draft;
  if (draft && typeof draft === 'object') {
    if (!draftPicker.sku && catalog.has(draft.sku)) draftPicker.setSku(draft.sku);
    if (!quantityInput.value) {
      quantityInput.value = recoveryText(draft.quantity, 24);
      quantityUnit = recoveryText(draft.unit, 12);
    }
    if (!dateInput.value) dateInput.value = recoveryText(draft.delivery_date, 10);
    if (!noteInput.value) noteInput.value = recoveryText(draft.note, noteInput.maxLength);
  }
  if (!customerInput.value) customerInput.value = recoveryText(saved.customer, 80);
  renderCart();
  renderDraft();
  showDateWarnings();
}

async function request(path, body) {
  const options = body === undefined ? { cache: 'no-store' } : {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  };
  const response = await fetch(path, options);
  if (response.status === 401) throw new Error('登录已过期，请刷新页面后重新登录。');
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(typeof data.error === 'string' ? data.error :
      typeof data.message === 'string' ? data.message : `请求失败（${response.status}），请重试。`);
    error.chatQuota = data.chat_quota;
    throw error;
  }
  return data;
}

function showError(id, message = '') {
  const node = byId(id);
  node.textContent = message;
  node.hidden = !message;
}

function appendMessage(role, content, { pending = false, products = [], references = [] } = {}) {
  const bubble = document.createElement('div');
  bubble.className = `message ${role}`;
  if (pending) bubble.classList.add('pending');
  const label = document.createElement('span');
  label.className = 'message-label';
  label.textContent = role === 'user' ? '你' : 'AI 接单助手';
  const paragraph = document.createElement('p');
  if (role === 'assistant') {
    for (const [index, part] of content.split(/\*\*([^*\r\n]+)\*\*/).entries()) {
      if (index % 2 === 0) {
        paragraph.append(part);
      } else {
        const strong = document.createElement('strong');
        strong.textContent = part;
        paragraph.append(strong);
      }
    }
  } else {
    paragraph.textContent = content;
  }
  bubble.append(label, paragraph);
  if (products.length) {
    bubble.append(renderMatches(products.slice(0, 3)));
    if (products.length > 3) {
      const more = document.createElement('details');
      more.className = 'catalog-more';
      const summary = document.createElement('summary');
      summary.textContent = `查看其余 ${products.length - 3} 款`;
      more.append(summary, renderMatches(products.slice(3), 3));
      bubble.append(more);
    }
  }
  if (references.length) {
    const links=document.createElement('div'); links.className='advice-references';
    const label=document.createElement('small');label.textContent='公开资料来源';links.append(label);
    for(const [index,source] of references.slice(0,3).entries()){
      if(typeof source?.url!=='string'||!/^https?:\/\//.test(source.url))continue;
      const a=document.createElement('a');a.href=source.url;a.textContent=source.title||'参考资料';a.target='_blank';a.rel='noopener noreferrer';links.append(a);
      if (typeof source.snippet === 'string' && source.snippet) {
        a.textContent = `[${index + 1}] ${a.textContent}`;
        const snippet = document.createElement('span');
        snippet.textContent = source.snippet.slice(0, 100) + (source.snippet.length > 100 ? '…' : '');
        links.append(snippet);
      }
    }
    bubble.append(links);
  }
  messages.append(bubble);
  if (products.length) {
    messages.scrollTop += bubble.getBoundingClientRect().top - messages.getBoundingClientRect().top;
  } else {
    messages.scrollTop = messages.scrollHeight;
  }
  return bubble;
}

function renderChoices(items) {
  choices.replaceChildren();
  if (!Array.isArray(items)) return;
  for (const item of items.slice(0, 3)) {
    if (typeof item.label !== 'string' || typeof item.value !== 'string') continue;
    const button = document.createElement('button');
    button.className = 'choice';
    button.type = 'button';
    button.textContent = item.label;
    button.value = item.value;
    choices.append(button);
  }
}

function renderMatches(products, offset = 0) {
  const list = document.createElement('ol');
  list.className = 'catalog-matches';
  list.setAttribute('aria-label', '商品目录候选');
  for (const [index, product] of products.entries()) {
    const item = document.createElement('li');
    const title = document.createElement('strong');
    const number = offset + index + 1;
    title.textContent = `${number}. ${productLabel(product)}`;
    const details = document.createElement('span');
    details.textContent = [product.spec, product.color, product.unit && `按${product.unit}计量`].filter(Boolean).join(' · ');
    const description = document.createElement('span');
    description.textContent = product.description || product.details || '';
    item.append(title, details, description);
    list.append(item);
  }
  return list;
}

function renderDraft() {
  const product = catalog.get(draftPicker.sku);
  byId('product-detail').hidden = !product;
  byId('detail-sku').textContent = customerSku(product);
  byId('detail-spec').textContent = product?.spec ?? '';
  byId('detail-color').textContent = product?.color || '待销售确认';
  const details = Array.isArray(product?.details) ? product.details.join(' · ') : product?.details;
  byId('detail-notes-row').hidden = typeof details !== 'string' || !details;
  byId('detail-notes').textContent = [details, product?.description, product?.selection_notes].filter(Boolean).join('；');
  byId('quantity-unit').textContent = product?.unit || '';
  byId('draft-status').textContent = submitted ? '已提交' : draftReady() ? '待加入' : '待填写';
  clearItem.hidden = !(productSearch.value || quantityInput.value || dateInput.value || noteInput.value);
  const step = submitted ? 2 : cart.length ? 1 : 0;
  steps.forEach((node, index) => {
    if (index === step) node.setAttribute('aria-current', 'step');
    else node.removeAttribute('aria-current');
  });
}

function showChatQuota(quota) {
  if (!quota || !Number.isInteger(quota.limit) || !Number.isInteger(quota.remaining)) return;
  chatQuota = quota;
  byId('chat-quota').textContent = `每日最多 ${quota.limit} 条 · 剩余 ${quota.remaining} 条`;
}

async function refreshChatQuota() {
  if (chatQuotaLoading || chatting) return;
  chatQuotaLoading = true;
  setControls();
  try {
    const result = await request(`${apiPrefix}/chat-quota`);
    showChatQuota(result.chat_quota);
  } catch {
    if (!chatQuota) byId('chat-quota').textContent = '每日最多 150 条 · 剩余额度暂不可用';
  } finally {
    chatQuotaLoading = false;
    setControls();
  }
}

function setControls() {
  const workLocked = submitting || submitted;
  const chatLocked = chatting || workLocked || chatQuotaLoading || (chatQuota?.remaining === 0 && chatQuota.day === todayInChina());
  chatForm.querySelector('button').disabled = chatLocked;
  chatMessage.disabled = submitting || submitted;
  choices.querySelectorAll('button').forEach(button => { button.disabled = chatLocked; });
  addItem.disabled = workLocked || !workspaceReady;
  clearItem.disabled = workLocked;
  submitOrder.disabled = workLocked || !cart.length;
  cartItems.querySelectorAll('input, textarea, button').forEach((field) => { field.disabled = submitting || submitted; });
  productSearch.disabled = workLocked || !workspaceReady;
  if (productSearch.disabled) draftPicker.close();
  quantityInput.disabled = submitting;
  dateInput.disabled = submitting;
  noteInput.disabled = submitting;
  customerInput.disabled = submitting;
  byId('cart-bulk-date').querySelectorAll('input, select, button').forEach(field => { field.disabled = workLocked || !cart.length; });
}

function normalizeProductQuery(value) {
  return value.normalize('NFKC').toLowerCase().replace(/[‐‑–—−]/g, '-').replace(/\s+/g, '');
}

function productOptionLabel(product) {
  return `${productLabel(product)} · ${product.spec}`;
}

function createProductPicker({ id, label, products = () => catalog.values(), initialSku = '', onChange }) {
  const element = byId('product-picker-template').content.firstElementChild.cloneNode(true);
  const input = element.querySelector('input');
  const results = element.querySelector('.product-results');
  const list = results.querySelector('[role="listbox"]');
  const empty = results.querySelector('p');
  const status = element.querySelector('[role="status"]');
  input.id = id;
  list.id = `${id}-options`;
  input.setAttribute('aria-controls', list.id);
  if (label) input.setAttribute('aria-label', label);

  let sku = '';
  let activeIndex = -1;

  function setSku(value) {
    const product = catalog.get(value);
    sku = product?.sku ?? '';
    input.value = product ? productOptionLabel(product) : '';
    input.setCustomValidity(product ? '' : '请从列表中选择商品与规格。');
  }

  function highlight(index, scroll = false) {
    list.children[activeIndex]?.setAttribute('aria-selected', 'false');
    activeIndex = index;
    const option = list.children[index];
    if (!option) {
      input.removeAttribute('aria-activedescendant');
      return;
    }
    option.setAttribute('aria-selected', 'true');
    input.setAttribute('aria-activedescendant', option.id);
    if (scroll) option.scrollIntoView({ block: 'nearest' });
  }

  function open() {
    const query = sku ? '' : normalizeProductQuery(input.value);
    const matches = [...products()].filter(product =>
      [product.sku, customerSku(product), product.name].some(value => normalizeProductQuery(value).includes(query)));
    list.replaceChildren(...matches.map((product, index) => {
      const option = document.createElement('li');
      option.id = `${list.id}-${index}`;
      option.dataset.sku = product.sku;
      option.setAttribute('role', 'option');
      option.setAttribute('aria-selected', 'false');
      option.textContent = productOptionLabel(product);
      return option;
    }));
    list.hidden = !matches.length;
    empty.hidden = Boolean(matches.length);
    status.textContent = matches.length ? `找到 ${matches.length} 项商品` : '没有匹配的商品';
    results.hidden = false;
    results.scrollTop = 0;
    input.setAttribute('aria-expanded', 'true');
    highlight(matches.findIndex(product => product.sku === sku));
  }

  function close() {
    results.hidden = true;
    input.setAttribute('aria-expanded', 'false');
    highlight(-1);
  }

  function choose(option) {
    if (!option || input.disabled || !catalog.has(option.dataset.sku)) return;
    setSku(option.dataset.sku);
    close();
    input.select();
    onChange(sku);
  }

  input.addEventListener('focus', () => {
    if (sku) input.select();
    open();
  });
  input.addEventListener('click', () => {
    if (sku) input.select();
    if (results.hidden) open();
  });
  input.addEventListener('blur', close);
  input.addEventListener('input', () => {
    const hadSelection = Boolean(sku);
    sku = '';
    input.setCustomValidity('请从列表中选择商品与规格。');
    if (hadSelection) onChange('');
    open();
  });
  input.addEventListener('keydown', event => {
    if (event.isComposing || event.keyCode === 229) return;
    const { key } = event;
    if (key === 'Escape' && !results.hidden) {
      event.preventDefault();
      close();
      return;
    }
    if (!['ArrowDown', 'ArrowUp', 'Enter'].includes(key)) return;
    event.preventDefault();
    if (results.hidden) {
      open();
      if (key === 'Enter') return;
    }
    const count = list.children.length;
    if (!count) return;
    if (key === 'Enter') {
      choose(list.children[activeIndex] ?? (count === 1 ? list.firstElementChild : null));
      return;
    }
    const index = key === 'ArrowDown' ? Math.min(activeIndex + 1, count - 1)
      : activeIndex === -1 ? count - 1 : Math.max(0, activeIndex - 1);
    highlight(index, true);
  });
  list.addEventListener('mousedown', event => event.preventDefault());
  list.addEventListener('click', event => choose(event.target.closest('[role="option"]')));

  setSku(initialSku);
  return { element, input, get sku() { return sku; }, setSku, close };
}

async function loadCatalog() {
  try {
    if (!guestMode) {
      const me = await request('/api/me');
      recoveryKey = `hualian-order-recovery-v2:buyer:${me.username}`;
      accountCustomer=me.customer_name||me.username; customerInput.readOnly=true; customerInput.title='客户名称与账号绑定，由管理员修改';
    }
    const { products } = await request(`${apiPrefix}/catalog`);
    catalog = new Map(products.map((product) => [product.sku, product]));
    if (catalog.size === 0) {
      showError('draft-error', '公开目录暂无商品。');
    } else {
      await deliveryReady;
      restoreWorkspace();
      workspaceReady = true;
      if (!guestMode) {await loadRepeatOrder();customerInput.value=accountCustomer;renderDraft();}
    }
  } catch {
    showError('draft-error', '商品目录加载失败，请刷新页面后重试。');
  }
  setControls();
}

const deliveryReady = loadDeliveryClock();
const catalogReady = loadCatalog();

async function sendMessage(rawMessage) {
  const message = rawMessage.trim();
  if (!message || chatting || submitting || submitted || chatQuotaLoading) return;
  if (chatQuota?.remaining === 0 && chatQuota.day === todayInChina()) {
    showError('chat-error', '今天的聊天额度已用完，北京时间零点恢复。');
    return;
  }

  const revision = toolRevision;
  const previousChoices = [...choices.querySelectorAll('button')].map(button => ({
    label: button.textContent,
    value: button.value,
  }));
  chatting = true;
  chatMessage.value = '';
  renderChoices([]);
  showError('chat-error');
  setControls();
  const userBubble = appendMessage('user', message);
  const pendingBubble = appendMessage('assistant', '正在回复…', { pending: true });

  try {
    const result = await request(`${apiPrefix}/chat`, {
      message,
      history: history.slice(-12).map(turn => ({ ...turn, content: turn.content.slice(0, 600) })),
      catalog_context: catalogContext,
    });
    showChatQuota(result.chat_quota);
    if (revision !== toolRevision) return;
    pendingBubble.remove();
    const reply = result.reply;
    const products = Array.isArray(result.matches) ? result.matches : [];
    appendMessage('assistant', reply, { products, references: result.references || [] });
    history.push({ role: 'user', content: message }, { role: 'assistant', content: reply });
    catalogContext = result.catalog_context ?? null;
    renderChoices(result.choices);
  } catch (error) {
    showChatQuota(error.chatQuota);
    if (revision !== toolRevision) return;
    pendingBubble.remove();
    userBubble.remove();
    if (!chatMessage.value) chatMessage.value = message;
    renderChoices(previousChoices);
    showError('chat-error', error.message || '查询暂时不可用，请稍后重试。');
  } finally {
    chatting = false;
    setControls();
  }
}

function requestIdFor(payload) {
  const signature = JSON.stringify(payload);
  if (!requestIds.has(signature)) {
    const id = globalThis.crypto?.randomUUID?.() ??
      `demo-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
    requestIds.set(signature, id);
    if (requestIds.size > maxRecoveryAttempts) requestIds.delete(requestIds.keys().next().value);
  }
  return requestIds.get(signature);
}

function cartFeedback(message = '') {
  const node = byId('cart-feedback');
  node.textContent = message;
  node.hidden = !message;
}

function renderCart() {
  cartItems.replaceChildren();
  const minimum = deliveryMinimum();
  for (const [index, line] of cart.entries()) {
    const product = catalog.get(line.sku);
    const fragment = cartItemTemplate.content.cloneNode(true);
    const item = fragment.firstElementChild;
    item.dataset.cartIndex = String(index);
    const label = productLabel(product);
    item.querySelector('strong').textContent = label;
    item.querySelector('.cart-product p').textContent = [product.category, product.spec, product.color].filter(Boolean).join(' · ');
    item.querySelector('.cart-unit').textContent = product.unit;
    const quantity = item.querySelector('[data-quantity]');
    quantity.value = Number.isFinite(line.quantity) ? String(line.quantity) : '';
    quantity.setAttribute('aria-label', `${label} 数量`);
    const date = item.querySelector('[data-delivery-date]');
    date.min = minimum;
    date.value = line.delivery_date;
    date.setAttribute('aria-label', `${label} 期望交期`);
    const note = item.querySelector('[data-note]');
    note.value = line.note ?? '';
    note.setAttribute('aria-label', `${label} 商品备注`);
    item.querySelector('[data-remove]').setAttribute('aria-label', `移除第 ${index + 1} 项 ${label}`);
    cartItems.append(fragment);
  }
  byId('cart-bulk-date').hidden = !cart.length;
  byId('cart-common-date').min = minimum;
  cartItems.hidden = !cart.length;
  byId('cart-empty').hidden = Boolean(cart.length);
  byId('cart-count').textContent = `${numberFormat.format(cart.length)} 项`;
}

function clearDraft() {
  draftForm.reset();
  draftPicker.setSku('');
  draftPicker.close();
  refreshDateLimits();
  quantityUnit = '';
  showError('draft-error');
}

function addToCart(event) {
  event.preventDefault();
  if (submitting || submitted) return;
  const sku = draftPicker.sku;
  const product = catalog.get(sku);
  const quantity = quantityInput.valueAsNumber;
  const deliveryDate = dateInput.value;
  if (!product || !validQuantity(quantity) || !deliveryDate) {
    showError('draft-error', '请核对商品、数量和期望交期。');
    return;
  }
  if (!validDeliveryDate(deliveryDate)) {
    showError('draft-error', deliveryDateHint());
    return;
  }
  if (cart.length >= maxCartLines) {
    showError('draft-error', `一笔试单最多可添加 ${maxCartLines} 项商品。`);
    return;
  }
  cart.push({ sku, quantity, delivery_date: deliveryDate, note: noteInput.value.trim() });
  clearDraft();
  showError('order-error');
  renderCart();
  renderDraft();
  setControls();
  showDateWarnings();
  cartFeedback(`已加入 ${productLabel(product)}`);
  saveWorkspace();
}

function cartIndex(target) {
  return Number(target.closest('[data-cart-index]')?.dataset.cartIndex);
}

function editCart(event) {
  if (submitting || submitted) return;
  const input = event.target;
  const line = cart[cartIndex(input)];
  if (!line) return;
  if (input.matches('[data-quantity]')) line.quantity = input.valueAsNumber;
  if (input.matches('[data-delivery-date]')) line.delivery_date = input.value;
  if (input.matches('[data-note]')) line.note = input.value;
  cartFeedback();
  showError('order-error');
  showDateWarnings();
}

function showReceipt(order) {
  if (order.inquiry) {
    byId('receipt-title').textContent = '订单询价已提交';
    byId('receipt-title').nextElementSibling.textContent = '请保存询价编号，销售将通过您填写的联系方式联系。';
  }
  byId('receipt-number').textContent = order.number;
  byId('receipt-customer').textContent = order.customer;
  const receiptItems = byId('receipt-items');
  receiptItems.replaceChildren();
  for (const line of order.items ?? [order]) {
    const item = document.createElement('li');
    const title = document.createElement('strong');
    title.textContent = [productLabel(line), line.spec].filter(Boolean).join(' · ');
    const details = document.createElement('span');
    details.textContent = `${numberFormat.format(line.quantity)} ${line.unit}` +
      (line.delivery_date ? ` · 期望交期 ${line.delivery_date}` : '');
    item.append(title, details);
    if (line.note) {
      const note = document.createElement('span');
      note.className = 'item-note';
      note.textContent = `备注：${line.note}`;
      item.append(note);
    }
    receiptItems.append(item);
  }
  const emailMessage = ({
    paused: '订单已保存，销售可在后台查看。邮件通知目前已暂停。',
    unconfigured: '订单已保存，销售可在后台查看。邮件通知暂未发送。',
    pending: '订单已保存，正在发送销售邮件通知。',
    sent: '销售邮件通知已发送。',
    failed: '邮件通知暂未送达，销售可在后台查看订单。',
  })[order.email_status];
  byId('receipt-email').textContent = emailMessage ?? '';
  byId('receipt-email').hidden = !emailMessage;
  orderWorkspace.hidden = true;
  receipt.hidden = false;
  byId('receipt-title').focus();
}

async function submitCart(event) {
  event.preventDefault();
  if (submitting || submitted) return;
  showError('order-error');

  const customer = customerInput.value.trim();
  if (!cart.length || !customer || customer.length > 80) {
    showError('order-error', '请添加采购商品并填写客户名称。');
    return;
  }
  if (productSearch.value && quantityInput.value && dateInput.value) {
    if (!draftPicker.sku) {
      showError('draft-error', '请先确认当前商品的规格，或清空当前商品后再提交。');
      productSearch.focus();
      return;
    }
    const notice = validDeliveryDate(dateInput.value) ? '当前商品尚未加入采购车。请先加入，或清空当前商品后再提交。' :
      `请调整当前商品的期望交期，或清空后再提交。${deliveryDateHint()}`;
    showError('draft-error', notice);
    addItem.focus();
    return;
  }
  for (const line of cart) {
    const product = catalog.get(line.sku);
    const label = product ? productLabel(product) : line.sku;
    if (!product || !validQuantity(line.quantity) || !line.delivery_date) {
      showError('order-error', `请核对 ${label} 的数量和期望交期。`);
      return;
    }
    if (!validDeliveryDate(line.delivery_date)) {
      showError('order-error', `请调整 ${label} 的期望交期。${deliveryDateHint()}`);
      return;
    }
  }
  const items = cart.map(({ note, ...line }) => ({
    ...line,
    catalog_spec: catalog.get(line.sku).spec,
    ...(note?.trim() ? { note: note.trim() } : {}),
  }));
  const contacts = Object.fromEntries(['qq', 'email', 'phone', 'wechat'].map(key => [key, byId(`contact-${key}`).value.trim()]));
  if (guestMode && !Object.values(contacts).some(Boolean)) {
    showError('order-error', 'QQ、邮箱、电话、微信至少填写一种。');
    return;
  }
  const payload = guestMode ? { customer, items, contacts } : { customer, items };
  submitting = true;
  setControls();
  try {
    const requestId = requestIdFor(payload);
    saveWorkspace();
    const { order } = await request(guestMode ? '/api/guest/inquiries' : '/api/orders', { ...payload, request_id: requestId });
    if (!order?.number) throw new Error('暂时无法确认订单号，请用相同信息重试。');
    submitted = true;
    resetOrderTools();
    saveWorkspace();
    renderDraft();
    showReceipt(order);
  } catch (error) {
    showError('order-error', error.message || '订单提交失败，请重试。');
  } finally {
    submitting = false;
    setControls();
  }
}

chatForm.addEventListener('submit', (event) => {
  event.preventDefault();
  void sendMessage(chatMessage.value);
});
choices.addEventListener('click', event => {
  const button = event.target.closest('button');
  if (button && !button.disabled) void sendMessage(button.value);
});
productSearch.addEventListener('input', () => {
  showError('draft-error');
  renderDraft();
});

function updateDraftProduct(sku) {
  const product = catalog.get(sku);
  if (!product) return;
  if (quantityInput.value && quantityUnit && quantityUnit !== product.unit) {
    quantityInput.value = '';
    quantityUnit = '';
    showError('draft-error', `所选商品按「${product.unit}」计量，请重新填写数量。`);
  } else {
    if (quantityInput.value) quantityUnit = product.unit;
    showError('draft-error');
  }
  renderDraft();
  saveWorkspace();
}

quantityInput.addEventListener('input', () => {
  quantityUnit = catalog.get(draftPicker.sku)?.unit || '';
  showError('draft-error');
  renderDraft();
});
function editDeliveryDate() {
  showError('draft-error');
  refreshDateLimits();
  showDateWarnings();
  renderDraft();
}
dateInput.addEventListener('input', editDeliveryDate);
dateInput.addEventListener('change', editDeliveryDate);
noteInput.addEventListener('input', renderDraft);
customerInput.addEventListener('change', renderDraft);
draftForm.addEventListener('submit', addToCart);
clearItem.addEventListener('click', () => {
  clearDraft();
  renderDraft();
  saveWorkspace();
  productSearch.focus();
});
cartItems.addEventListener('input', editCart);
cartItems.addEventListener('change', editCart);
cartItems.addEventListener('click', (event) => {
  const button = event.target.closest('[data-remove]');
  if (!button || submitting || submitted) return;
  const index = cartIndex(button);
  if (!cart[index]) return;
  const [line] = cart.splice(index, 1);
  cartFeedback(`已移除 ${productLabel(catalog.get(line.sku))}`);
  showError('order-error');
  renderCart();
  renderDraft();
  setControls();
  saveWorkspace();
  (cartItems.querySelectorAll('[data-remove]')[Math.min(index, cart.length - 1)] || productSearch).focus();
});
orderWorkspace.addEventListener('input', saveWorkspace);
orderWorkspace.addEventListener('change', saveWorkspace);
orderWorkspace.addEventListener('focusin', (event) => {
  if (event.target === dateInput || event.target.matches('[data-delivery-date]')) {
    refreshDateLimits();
    showDateWarnings();
  }
});
orderForm.addEventListener('submit', (event) => event.preventDefault());
submitOrder.addEventListener('click', (event) => {
  refreshDateLimits();
  showDateWarnings();
  if (orderForm.reportValidity()) void submitCart(event);
});
byId('new-order').addEventListener('click', () => {
  resetOrderTools();
  cart.length = 0;
  requestIds.clear();
  clearDraft();
  submitted = false;
  orderForm.reset();
  if (accountCustomer) customerInput.value = accountCustomer;
  orderWorkspace.hidden = false;
  receipt.hidden = true;
  cartFeedback();
  showError('order-error');
  renderCart();
  renderDraft();
  setControls();
  saveWorkspace();
  productSearch.focus();
});

refreshDateLimits();
setControls();

if (guestMode) {
  document.querySelector('.reorder-entry').hidden = true;
  document.title = '订单询价 · 华联服装辅料';
  byId('page-title').textContent = '订单询价';
  document.querySelector('.after-submit').textContent = '采购车将合并为一笔询价，由销售联系并核对报价、库存和交期。';
  steps[2].lastChild.textContent = ' 提交询价';
  byId('receipt-number').previousElementSibling.textContent = '询价编号';
  byId('new-order').textContent = '继续询价';
  byId('contact-fields').hidden = false;
  submitOrder.textContent = '确认并提交询价 →';
  document.querySelector('a[href="./history/"]').hidden = true;
  document.querySelector('.topbar-actions form').hidden = true;
  const loginLink = document.createElement('a');
  loginLink.href = '/'; loginLink.className = 'quiet-link'; loginLink.textContent = '采购员登录';
  document.querySelector('.topbar-actions').append(loginLink);
  byId('cart-panel-hint').textContent = '核对商品并留下联系方式，整单提交询价。';
  byId('page-intro').textContent = '可用AI咨询、智能粘贴或手动选品，补全后加入采购车统一询价。';
}


async function loadRepeatOrder() {
  const number = new URLSearchParams(location.search).get('reorder');
  if (!number) return;
  const notice = byId('reorder-notice');
  const dismiss = () => {
    clearTimeout(reorderNoticeTimer); notice.hidden = true;
    const url=new URL(location.href);url.searchParams.delete('reorder');window.history.replaceState(null,'',url);
  };
  const say = (message, autoHide = false) => {
    clearTimeout(reorderNoticeTimer); notice.replaceChildren(); notice.hidden = false;
    const p=document.createElement('p');p.textContent=message;
    const close=document.createElement('button');close.type='button';close.className='reorder-toast-close';close.textContent='×';close.setAttribute('aria-label','关闭复购提示');close.onclick=dismiss;
    notice.append(p,close);
    notice.onpointerenter=()=>clearTimeout(reorderNoticeTimer);
    notice.onfocusin=()=>clearTimeout(reorderNoticeTimer);
    if(autoHide)reorderNoticeTimer=setTimeout(()=>{notice.hidden=true;},8000);
  };
  try {
    const previous = await request(`/api/orders/${encodeURIComponent(number)}/repeat`);
    const warnings = previous.unavailable.length ? `目录已无以下商品，未复制：${previous.unavailable.join('、')}。` : '';
    if (!previous.items.length) { say(`原订单商品目前无法复购。${warnings}`); return; }
    const apply = () => {
      if (cart.length + previous.items.length > maxCartLines) { say(`采购车最多 ${maxCartLines} 项，请先整理当前采购车。`); return; }
      cart.push(...previous.items.map(item=>({...item})));
      if (!customerInput.value.trim()) customerInput.value = previous.customer;
      // Preserve the current draft and existing request IDs; the new cart produces a new ID.
      renderCart(); renderDraft(); setControls(); saveWorkspace();
      say(`已复制 ${previous.items.length} 项商品，请重新填写交期后提交。${warnings}`, !warnings);
      const url=new URL(location.href);url.searchParams.delete('reorder');window.history.replaceState(null,'',url);
      cartItems.querySelector('input[type="date"]')?.focus();
    };
    if (cart.length || productSearch.value || quantityInput.value || dateInput.value || noteInput.value) {
      say(`当前已有采购车或未完成草稿。是否把 ${previous.number} 的 ${previous.items.length} 项商品追加到采购车？原有内容会保留。${warnings}`);
      const add=document.createElement('button');add.type='button';add.className='button secondary';add.textContent='保留当前内容，追加商品';add.onclick=apply;
      const cancel=document.createElement('button');cancel.type='button';cancel.className='quiet-link';cancel.textContent='暂不导入';cancel.onclick=dismiss;
      notice.append(add,cancel);
    } else apply();
  } catch(error) { say(error.message || '原订单暂时无法加载，请返回历史订单重试。'); }
}

// The editable review is intentionally separate from the cart and order submission.
let pasteRows = [];
let pasteBusy = false;
let toolRevision = 0;
const pastePanel = byId('paste-panel');
const pasteError = message => { byId('paste-error').textContent=message; byId('paste-error').hidden=!message; };
function resetOrderTools() {
  clearTimeout(reorderNoticeTimer);
  byId('reorder-notice').hidden = true;
  history = [];
  catalogContext = null;
  messages.replaceChildren();
  appendMessage('assistant', greeting);
  chatMessage.value = '';
  renderChoices(examples);
  toolRevision += 1;
  pasteRows = [];
  pasteBusy = false;
  byId('paste-text').value = '';
  byId('paste-rows').replaceChildren();
  byId('paste-review').hidden = true;
  byId('paste-confirm').checked = false;
  byId('paste-common-date').value = '';
  byId('paste-date-mode').value = 'missing';
  for (const id of ['paste-info', 'paste-warnings', 'paste-validation']) byId(id).textContent = '';
  pasteError('');
  byId('parse-paste').disabled = false;
  byId('import-paste').disabled = true;
  setToolExpanded('chat', false, false);
  setToolExpanded('paste', false, false);
}
function pasteProblems(row) {
  const product=catalog.get(row.sku);
  const problems=[];
  if(!product) problems.push(row.candidates?.length ? '商品有多个候选，请选择规格。' : '未匹配到目录商品，请重新选择或移除此项。');
  if(!validQuantity(row.quantity)) problems.push('数量应为1至1000000的整数。');
  if(!row.delivery_date) problems.push('请填写交付日期。');
  else if(!validDeliveryDate(row.delivery_date)) problems.push(`至少预留7天，最快${deliveryMinimum()}。`);
  if(product && row.unit && row.unit!==product.unit && !row.unitConfirmed) problems.push(`原文按「${row.unit}」，目录按「${product.unit}」，请核对数量并勾选。`);
  if((row.note||'').length>100) problems.push('备注最多100字。');
  return problems;
}
function validatePaste() {
  const pending=pasteRows.filter(row=>pasteProblems(row).length).length;
  for(const [i,row] of pasteRows.entries()) {
    const node=byId('paste-rows').children[i]?.querySelector('[data-paste-issues]');
    if(node){const issues=pasteProblems(row);node.textContent=issues.join(' ')||'信息齐全';node.className=issues.length?'paste-row-error':'paste-row-ready';}
  }
  const details=pasteRows.flatMap((row,index)=>{const issues=pasteProblems(row);return issues.length?[`第${index+1}项：${issues.join(' ')}`]:[];});
  const summary=details.slice(0,8).join('\n')+(details.length>8?'\n其余问题请查看对应商品行。':'');
  byId('paste-validation').textContent=pending?`还有${pending}项需补全：\n${summary}`:pasteRows.length?'信息齐全，请对照原文核对后确认加入采购车。':'没有待导入的商品。';
  byId('paste-validation').classList.toggle('paste-row-error',pending>0);
  byId('import-paste').disabled=pasteBusy||submitting||submitted||!pasteRows.length||pending>0||!byId('paste-confirm').checked;
}
function renderPaste() {
  const container=byId('paste-rows');container.replaceChildren();
  byId('paste-common-date').min=deliveryMinimum();
  for(const [index,row] of pasteRows.entries()) {
    const tr=document.createElement('tr');
    const cell=()=>{const td=document.createElement('td');tr.append(td);return td;};
    cell().textContent=row.source;
    const recommended = new Set(row.candidates || []);
    const products = [...catalog.values()].sort((a, b) => Number(recommended.has(b.sku)) - Number(recommended.has(a.sku)));
    const picker = createProductPicker({
      id: `paste-product-${index + 1}`,
      label: `第${index + 1}项商品`,
      products: () => products,
      initialSku: row.sku,
      onChange: sku => {
        row.sku = sku;
        row.unitConfirmed = false;
        byId('paste-confirm').checked = false;
        updateProduct();
        validatePaste();
      },
    });
    const productIssue = document.createElement('p');
    productIssue.className = 'paste-row-error';
    productIssue.textContent = recommended.size ? '请选择具体颜色或规格。' : '请选择目录商品或移除此项。';
    cell().append(picker.element, productIssue);
    const quantityCell=cell();const quantity=document.createElement('input');quantity.type='number';quantity.min='1';quantity.max='1000000';quantity.step='1';quantity.value=row.quantity??'';quantity.setAttribute('aria-label',`第${index+1}项数量`);
    quantity.oninput=()=>{row.quantity=quantity.valueAsNumber;byId('paste-confirm').checked=false;validatePaste();};
    const unit = document.createTextNode('');
    const unitCheck = document.createElement('label');
    unitCheck.className = 'paste-check';
    const check = document.createElement('input');
    check.type = 'checkbox';
    check.onchange = () => {
      row.unitConfirmed = check.checked;
      byId('paste-confirm').checked = false;
      validatePaste();
    };
    unitCheck.append(check, document.createTextNode(`已核对原文「${row.unit}」与目录单位，数量正确`));
    quantityCell.append(quantity, unit, unitCheck);

    function updateProduct() {
      const product = catalog.get(row.sku);
      productIssue.hidden = Boolean(product);
      unit.textContent = ` ${product?.unit || '待选单位'}`;
      unitCheck.hidden = !row.unit || !product || row.unit === product.unit;
      check.checked = Boolean(row.unitConfirmed);
    }
    updateProduct();
    const delivery=document.createElement('input');delivery.type='date';delivery.min=deliveryMinimum();delivery.value=row.delivery_date;delivery.setAttribute('aria-label',`第${index+1}项交付日期`);delivery.onchange=()=>{row.delivery_date=delivery.value;byId('paste-confirm').checked=false;validatePaste();};cell().append(delivery);
    const note=document.createElement('textarea');note.value=row.note||'';note.maxLength=100;note.setAttribute('aria-label',`第${index+1}项备注`);note.oninput=()=>{row.note=note.value;byId('paste-confirm').checked=false;validatePaste();};cell().append(note);
    const controls=cell();const issues=document.createElement('p');issues.dataset.pasteIssues='';const remove=document.createElement('button');remove.type='button';remove.className='quiet-link';remove.textContent='移除此项';remove.setAttribute('aria-label',`移除粘贴第${index+1}项`);remove.onclick=()=>{pasteRows.splice(index,1);byId('paste-confirm').checked=false;renderPaste();};controls.append(issues,remove);
    container.append(tr);
  }
  validatePaste();
}
function setToolExpanded(kind, expanded, focus = true) {
  const panel = kind === 'paste' ? pastePanel : byId('chat-panel');
  const toggle = byId(kind === 'paste' ? 'open-paste' : 'open-chat');
  panel.hidden = !expanded;
  toggle.setAttribute('aria-expanded', String(expanded));
  toggle.querySelector('path').setAttribute('d', expanded ? 'M7 5l5 5 5-5M7 12l5 5 5-5' : 'M5 7l5 5-5 5M12 7l5 5-5 5');
  if (expanded) {
    panel.scrollIntoView({behavior:'smooth',block:'start'});
    if (kind === 'paste') {
      refreshDateLimits();
      byId('paste-text').focus({preventScroll:true});
    } else {
      void refreshChatQuota();
      chatMessage.focus({preventScroll:true});
    }
  } else if (focus) toggle.focus({preventScroll:true});
}
byId('open-paste').onclick=()=>setToolExpanded('paste',pastePanel.hidden);
byId('close-paste').onclick=()=>setToolExpanded('paste',false);
byId('open-chat').onclick=()=>setToolExpanded('chat',byId('chat-panel').hidden);
byId('close-chat').onclick=()=>setToolExpanded('chat',false);
byId('paste-text').oninput=()=>{byId('paste-review').hidden=true;pasteRows=[];byId('paste-confirm').checked=false;byId('paste-info').textContent='内容已修改，请重新识别。';};
byId('parse-paste').onclick=async()=>{
 if(pasteBusy||submitting||submitted)return;
 await catalogReady;
 if(!workspaceReady){pasteError('商品目录尚未加载，请刷新重试。');return;}
 const text=byId('paste-text').value;
 const revision=toolRevision;
 pasteBusy=true;byId('parse-paste').disabled=true;byId('paste-review').hidden=true;pasteError('');byId('paste-info').textContent='正在识别，请稍候…';
 try{
  const result=await request(`${apiPrefix}/paste`,{text,use_model:byId('paste-model').checked});
  if(revision!==toolRevision||submitted)return;
  if(byId('paste-text').value!==text){byId('paste-info').textContent='识别期间内容已变化，请重新识别。';return;}
  pasteRows=result.rows.map(row=>({...row}));byId('paste-common-date').value=result.common_date||'';
  byId('paste-warnings').textContent=result.warnings.join(' ');byId('paste-confirm').checked=false;
  byId('paste-info').textContent=`${result.source}：识别到${pasteRows.length}项。请补全标出的信息，核对原文后加入采购车。`;
  byId('paste-review').hidden=false;renderPaste();
 }catch(error){if(revision===toolRevision&&!submitted){pasteError(error.message||'识别失败，请检查内容或手动选择商品。');byId('paste-info').textContent='';}}
 finally{if(revision===toolRevision){pasteBusy=false;byId('parse-paste').disabled=false;validatePaste();}}
};
byId('apply-paste-date').onclick=()=>{
 const value=byId('paste-common-date').value;
 if(!validDeliveryDate(value)){pasteError(`请选择有效交期，${deliveryDateHint()}`);return;}
 pasteError('');const all=byId('paste-date-mode').value==='all';
 for(const row of pasteRows)if(all||!row.delivery_date)row.delivery_date=value;
 byId('paste-confirm').checked=false;renderPaste();
};
byId('paste-confirm').onchange=validatePaste;
byId('import-paste').onclick=()=>{
 if(pasteBusy||submitting||submitted||!byId('paste-confirm').checked||!pasteRows.length||pasteRows.some(row=>pasteProblems(row).length)){validatePaste();return;}
 if(cart.length+pasteRows.length>maxCartLines){pasteError(`采购车最多${maxCartLines}项，请分批加入或整理现有商品。`);return;}
 const count=pasteRows.length;
 cart.push(...pasteRows.map(({sku,quantity,delivery_date,note})=>({sku,quantity,delivery_date,note:note.trim()})));
 pasteRows=[];byId('paste-review').hidden=true;byId('paste-confirm').checked=false;pasteError('');
 byId('paste-info').textContent=`已将${count}项加入采购车；尚未提交订单。`;
 renderCart();renderDraft();setControls();saveWorkspace();cartFeedback(`智能粘贴已加入${count}项，请核对后提交。`);
};


byId('apply-cart-date').onclick = () => {
  if (submitting || submitted || !cart.length) return;
  refreshDateLimits();
  const value = byId('cart-common-date').value;
  if (!validDeliveryDate(value)) {
    showError('order-error', `请选择有效的采购车交期。${deliveryDateHint()}`);
    byId('cart-common-date').focus();
    return;
  }
  const all = byId('cart-date-mode').value === 'all';
  const next = cart.map(line => ({ ...line, delivery_date: all || !line.delivery_date ? value : line.delivery_date }));
  const changed = next.filter((line,index) => line.delivery_date !== cart[index].delivery_date).length;
  next.forEach((line,index) => { cart[index].delivery_date = line.delivery_date; });
  showError('order-error');
  renderCart(); renderDraft(); setControls(); showDateWarnings(); saveWorkspace();
  cartFeedback(changed ? `已更新${changed}项商品的期望交期为${value}。` : '没有需要修改的交期；原有日期已保留。');
};
