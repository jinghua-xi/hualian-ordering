import {presentRecord, refreshRecord, recordDeleteControl, orderStatusLabel, statusProgress, setProgressStatus, recordProgressSummary} from '/record-dialog.js?v=status-progress-1';

const ordersTable = document.getElementById('orders-wrap');
const orderRows = document.getElementById('orders');
const orderCount = document.getElementById('sales-count');
const emptyState = document.getElementById('orders-empty');
const errorState = document.getElementById('sales-error');
const statusNotice = document.getElementById('status-notice');
const refresh = document.getElementById('refresh-orders');
const search = document.getElementById('order-search');
const company = document.getElementById('order-company');
const timeSort = document.getElementById('order-time-sort');
const statusFilters = document.getElementById('order-status-filters');
const noOrders = emptyState.textContent;
const customerHistory = document.body.dataset.orderHistory === 'customer';
let orderHistory = [];
let statusOptions = [];
let activeStatus = '';
const statusEdits = new Map();

const numberFormat = new Intl.NumberFormat('zh-CN');
const dateFormat = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
});
function cell(main, detail) {
  const td = document.createElement('td');
  td.textContent = main ?? '';
  if (detail) {
    const secondary = document.createElement('small');
    secondary.textContent = detail;
    td.append(secondary);
  }
  return td;
}

function setOrderControls(disabled) {
  refresh.disabled = disabled;
  search.disabled = disabled;
  document.querySelectorAll('.undo-order-status, #orders .status-step-button, #order-filters button, #order-filters select')
    .forEach(control => { control.disabled = disabled; });
}

async function saveStatus(order, status, feedback, isUndo = false) {
  if (refresh.disabled || order.status === status) return;
  const previous = order.status;
  setOrderControls(true);
  errorState.hidden = true;
  statusNotice.textContent = `正在保存 ${order.number} 的状态…`;
  if (feedback) feedback.textContent = '正在保存订单状态…';
  try {
    const response = await fetch(`../api/sales/orders/${encodeURIComponent(order.number)}/status`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status, expected_status: order.status }),
    });
    if (response.status === 401) throw new Error('销售登录已失效，请刷新页面后重新登录。');
    const result = await response.json().catch(() => ({}));
    if (!response.ok || !statusOptions.includes(result.status)) {
      throw new Error(result.error || '订单状态暂时无法保存，请重试。');
    }
    order.status = result.status;
    if (isUndo) statusEdits.delete(order.number);
    else if (previous !== result.status) statusEdits.set(order.number, {previous, current:result.status});
    const label = orderStatusLabel(result.status);
    statusNotice.textContent = `${order.number} 的状态已保存为「${label}」。`;
    feedback.textContent = isUndo ? `已撤销本次修改，恢复为「${label}」。` : `订单状态已保存为「${label}」。`;
    document.dispatchEvent(new CustomEvent('sales-order-status-saved'));
  } catch (error) {
    statusNotice.textContent = '';
    errorState.textContent = error.message || '订单状态暂时无法保存，请重试。';
    errorState.hidden = false;
    if (feedback) feedback.textContent = errorState.textContent;
  } finally {
    setOrderControls(false);
  }
}

function matchingOrders() {
  const query = search.value.trim().toLowerCase();
  return orderHistory.filter(order => (!company?.value || order.customer === company.value) &&
    (!query || [order.number, order.customer].some(value => String(value ?? '').toLowerCase().includes(query))));
}

function renderStatusFilters(orders) {
  if (!statusFilters) return;
  if (!statusFilters.childElementCount) {
    for (const status of ['', ...statusOptions]) {
      const button = document.createElement('button');
      button.type = 'button';
      button.value = status;
      button.onclick = () => { activeStatus = status; renderOrders(); };
      statusFilters.append(button);
    }
  }
  const counts = new Map();
  for (const order of orders) counts.set(order.status, (counts.get(order.status) ?? 0) + 1);
  for (const button of statusFilters.children) {
    const status = button.value;
    button.textContent = status ? `${orderStatusLabel(status)} ${counts.get(status) ?? 0}` : `全部 ${orders.length}`;
    button.setAttribute('aria-pressed', String(status === activeStatus));
  }
}

function renderOrders() {
  const matches = matchingOrders();
  renderStatusFilters(matches);
  const orders = activeStatus ? matches.filter(order => order.status === activeStatus) : matches;
  if (timeSort?.value === 'oldest') {
    orders.sort((left, right) => (Date.parse(left.created_at) || 0) - (Date.parse(right.created_at) || 0));
  }
  orderRows.replaceChildren();
  for (const order of orders) orderRows.append(orderThread(order));
  ordersTable.hidden = orders.length === 0;
  emptyState.hidden = orders.length !== 0;
  const filtered = Boolean(search.value.trim() || company?.value || activeStatus);
  emptyState.textContent = filtered && orderHistory.length ? '没有找到符合筛选条件的订单。' : noOrders;
  const count = numberFormat.format(orders.length);
  orderCount.textContent = filtered ? `找到 ${count} 笔订单 · 北京时间` : `共 ${count} 笔订单 · 北京时间`;
}

function orderThread(order) {
  const items = Array.isArray(order.items) && order.items.length ? order.items : [order];
  const thread = document.createElement('article');
  thread.className = 'record-thread';
  thread.dataset.recordNumber = order.number;
  const add = (tag, value, className) => {
    const node = document.createElement(tag); node.textContent = value; node.className = className; return node;
  };
  const created = new Date(order.created_at);
  const submitted = Number.isNaN(created.getTime()) ? order.created_at : dateFormat.format(created);
  const toggle = add('button', '详情', 'thread-toggle'); toggle.type = 'button';
  toggle.setAttribute('aria-label', `查看订单 ${order.number} 详情`); toggle.setAttribute('aria-haspopup', 'dialog');
  const content = add('div', '', 'thread-content');
  const reference = add('p', `订单号：${order.number} · 客户：${order.customer}`, 'thread-reference');
  const toolbar = add('div', '', 'thread-toolbar');
  const feedback = add('p', '', 'inline-error'); feedback.setAttribute('role', 'status');
  if (customerHistory) {
    const summary = add('div', '', 'thread-summary');
    const number = add('strong', order.number, 'thread-number'); number.title = order.number;
    const customer = add('span', order.customer, 'thread-customer'); customer.title = order.customer;
    const repeat = add('a', '再来一单', 'button secondary repeat-order');
    repeat.href = `/?reorder=${encodeURIComponent(order.number)}`;
    repeat.setAttribute('aria-label', `订单 ${order.number} 再来一单`);
    const actions = add('div', '', 'thread-summary-actions');
    actions.append(toggle, repeat);
    summary.append(number, customer, add('span', `${items.length} 项商品`, 'thread-count'),
      add('span', submitted, 'thread-date'), add('span', orderStatusLabel(order.status), 'thread-state'), actions);
    thread.append(summary);
    toolbar.append(add('span', `订单状态：${orderStatusLabel(order.status)}`, 'thread-customer-status'));
    content.append(reference, toolbar);
  } else {
    const undo = add('button', '撤销本次修改', 'button secondary undo-order-status'); undo.type = 'button';
    const edit = statusEdits.get(order.number); undo.hidden = !edit || edit.current !== order.status;
    const progress = statusProgress({number:order.number, status:order.status, onChange:changeStatus});
    thread.append(recordProgressSummary({number:order.number, customer:order.customer, submitted,
      count:items.length, progress, details:toggle}));
    async function changeStatus(status, isUndo = false) {
      const previous = order.status;
      const focusedStep = progress.contains(document.activeElement);
      await saveStatus(order, status, feedback, isUndo);
      setProgressStatus(progress, order.status);
      const latest = statusEdits.get(order.number);
      undo.hidden = !latest || latest.current !== order.status;
      if (previous !== order.status) {
        if (activeStatus && (previous === activeStatus || order.status === activeStatus)) {
          renderOrders();
          if (focusedStep) statusFilters.querySelector('[aria-pressed="true"]')?.focus();
        } else {
          renderStatusFilters(matchingOrders());
        }
      }
    }
    undo.onclick = () => {
      const latest = statusEdits.get(order.number);
      if (!latest || latest.current !== order.status) return;
      void changeStatus(latest.previous, true);
    };
    const download = add('a', '下载 Excel', 'button secondary');
    download.href = `./orders/${encodeURIComponent(order.number)}.xlsx`;
    const deleteControl = recordDeleteControl({kind:'order', number:order.number, status:() => order.status, onDeleted:async () => {
      statusEdits.delete(order.number);
      await loadOrders();
      statusNotice.textContent = `已删除订单 ${order.number}，数据库保留留档。`;
      refresh.focus();
    }});
    content.classList.add('order-detail');
    toolbar.classList.add('order-detail-toolbar');
    toolbar.append(reference, undo, download, deleteControl);
    content.append(toolbar);
  }
  const wrap = add('div', '', 'thread-items-wrap'); wrap.tabIndex = 0; wrap.setAttribute('aria-label', `订单 ${order.number} 商品明细，可横向滚动`);
  const table = add('table', '', 'thread-items');
  const caption = add('caption', `订单 ${order.number} 商品明细`, 'sr-only'); table.append(caption);
  const head = document.createElement('thead'); const header = document.createElement('tr');
  for (const title of ['商品与货号', '规格与颜色', '数量', '期望交期', '备注']) {
    const th = add('th', title, ''); th.scope = 'col'; header.append(th);
  }
  head.append(header); table.append(head);
  const body = document.createElement('tbody');
  for (const item of items) {
    const row = document.createElement('tr');
    row.append(cell(item.name, item.manufacturer_sku || item.sku), cell(item.spec, item.color),
      cell(`${numberFormat.format(item.quantity)} ${item.unit}`), cell(item.delivery_date || '—'), cell(item.note || '—'));
    body.append(row);
  }
  table.append(body); wrap.append(table); content.append(feedback, wrap);
  toggle.onclick = () => presentRecord({number:order.number, title:customerHistory?'我的订单详情':'采购员订单详情', content, trigger:toggle});
  refreshRecord(order.number, content);
  return thread;
}

async function loadOrders() {
  setOrderControls(true);
  errorState.hidden = true;
  if (statusNotice) statusNotice.textContent = '';
  orderCount.textContent = '正在加载订单';
  try {
    const response = await fetch(customerHistory ? '../api/orders' : './orders', { cache: 'no-store' });
    if (response.status === 401) throw new Error('登录已失效，请刷新整个页面后重新登录。');
    if (response.status === 403 && customerHistory) throw new Error('请在启用账号登录的接单网站查看历史订单。');
    if (!response.ok) throw new Error('订单列表暂时无法加载，请刷新订单。');
    const { orders, statuses = [] } = await response.json();
    orderHistory = orders;
    statusOptions = statuses;
    if (company) {
      const selected = company.value;
      const companies = [...new Set(orders.map(order => order.customer).filter(Boolean))].sort((left, right) => left.localeCompare(right, 'zh-CN'));
      company.replaceChildren(new Option('全部公司', ''), ...companies.map(name => new Option(name, name)));
      company.value = companies.includes(selected) ? selected : '';
    }
    renderOrders();
  } catch (error) {
    orderHistory = [];
    orderRows.replaceChildren();
    ordersTable.hidden = true;
    emptyState.hidden = true;
    orderCount.textContent = '加载失败';
    errorState.textContent = error.message || '订单列表暂时无法加载，请刷新订单。';
    errorState.hidden = false;
  } finally {
    setOrderControls(false);
  }
}

refresh.addEventListener('click', () => void loadOrders());
search.addEventListener('input', renderOrders);
company?.addEventListener('change', renderOrders);
timeSort?.addEventListener('change', renderOrders);
void loadOrders();

if(customerHistory && new URLSearchParams(location.search).get('reorder')==='1') document.querySelector('.sales-hero p').textContent='点击订单旁的「再来一单」，将商品和数量带入采购车。请重新填写交期。';
