import {presentRecord, refreshRecord} from '/record-dialog.js?v=record-modal-1';

const ordersTable = document.getElementById('orders-wrap');
const orderRows = document.getElementById('orders');
const orderCount = document.getElementById('sales-count');
const emptyState = document.getElementById('orders-empty');
const errorState = document.getElementById('sales-error');
const statusNotice = document.getElementById('status-notice');
const refresh = document.getElementById('refresh-orders');
const search = document.getElementById('order-search');
const noOrders = emptyState.textContent;
const customerHistory = document.body.dataset.orderHistory === 'customer';
let orderHistory = [];
let statusOptions = [];
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
const emailStatuses = {
  pending: '待发送', sending: '发送中', sent: '已发送',
  failed: '发送失败', unconfigured: '待配置发件', paused: '通知已暂停',
};

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
  document.querySelectorAll('.order-status, .undo-order-status').forEach((control) => { control.disabled = disabled; });
}

async function saveStatus(order, select, feedback, isUndo = false) {
  const status = select.value;
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
    statusNotice.textContent = `${order.number} 的状态已保存为「${result.status}」。`;
    if (feedback) feedback.textContent = isUndo ? `已撤销本次修改，恢复为「${result.status}」。` : `订单状态已保存为「${result.status}」。`;
  } catch (error) {
    statusNotice.textContent = '';
    errorState.textContent = error.message || '订单状态暂时无法保存，请重试。';
    errorState.hidden = false;
    if (feedback) feedback.textContent = errorState.textContent;
  } finally {
    select.value = order.status;
    orderRows.querySelectorAll('[data-order-status]').forEach(node => {
      if (node.dataset.orderStatus === order.number) node.textContent = order.status;
    });
    document.querySelectorAll('.undo-order-status').forEach(node => {
      if (node.dataset.orderNumber === order.number) {
        const edit = statusEdits.get(order.number);
        node.hidden = !edit || edit.current !== order.status;
      }
    });
    setOrderControls(false);
  }
}

function statusCell(order, feedback) {
  if (customerHistory || !statusOptions.length) return cell(order.status);
  const td = cell('');
  const select = document.createElement('select');
  select.className = 'order-status';
  select.setAttribute('aria-label', `订单 ${order.number} 的状态`);
  if (!statusOptions.includes(order.status)) {
    select.append(new Option(order.status, order.status, false, true));
    select.options[0].disabled = true;
  }
  select.append(...statusOptions.map((status) => new Option(status, status)));
  select.value = order.status;
  select.addEventListener('change', () => void saveStatus(order, select, feedback));
  td.append(select);
  return td;
}

function renderOrders() {
  const query = search.value.trim().toLowerCase();
  const orders = query ? orderHistory.filter((order) =>
    [order.number, order.customer].some((value) => String(value ?? '').toLowerCase().includes(query))
  ) : orderHistory;
  orderRows.replaceChildren();
  for (const order of orders) {
    if (!customerHistory) {
      orderRows.append(orderThread(order));
      continue;
    }
    const created = new Date(order.created_at);
    const items = Array.isArray(order.items) && order.items.length ? order.items : [order];
    items.forEach((item, index) => {
      const row = document.createElement('tr');
      if (index === 0) {
        const identity = [cell(order.number), cell(order.customer)];
        identity.forEach((node) => { node.rowSpan = items.length; });
        row.append(...identity);
      }
      const product = cell(item.name, item.manufacturer_sku || item.sku);
      if (item.note) {
        const note = document.createElement('small');
        note.className = 'item-note';
        note.textContent = `备注：${item.note}`;
        product.append(note);
      }
      row.append(
        product,
        cell(item.spec, item.color),
        cell(`${numberFormat.format(item.quantity)} ${item.unit}`),
        cell(item.delivery_date || '—'),
      );
      if (index === 0) {
        const summary = [
          cell(Number.isNaN(created.getTime()) ? order.created_at : dateFormat.format(created)),
          statusCell(order),
        ];
        if (customerHistory) {
          const action = cell('');
          const repeat = document.createElement('a');
          repeat.className = 'button secondary';
          repeat.href = `/?reorder=${encodeURIComponent(order.number)}`;
          repeat.textContent = '再来一单';
          repeat.setAttribute('aria-label', `订单 ${order.number} 再来一单`);
          action.append(repeat);
          summary.push(action);
        }
        if (!customerHistory) {
          const email = cell(emailStatuses[order.email_status] ?? '—');
          if (order.number) {
            const line = document.createElement('small');
            const download = document.createElement('a');
            download.href = `./orders/${encodeURIComponent(order.number)}.xlsx`;
            download.textContent = '下载 Excel';
            line.append(download);
            email.append(line);
          }
          summary.push(email);
        }
        summary.forEach((node) => { node.rowSpan = items.length; });
        row.append(...summary);
      }
      orderRows.append(row);
    });
  }
  ordersTable.hidden = orders.length === 0;
  emptyState.hidden = orders.length !== 0;
  emptyState.textContent = query && orderHistory.length ? '没有找到匹配的订单。' : noOrders;
  const count = numberFormat.format(orders.length);
  orderCount.textContent = query ? `找到 ${count} 笔订单 · 北京时间` : `共 ${count} 笔订单 · 北京时间`;
}

function orderThread(order) {
  const items = Array.isArray(order.items) && order.items.length ? order.items : [order];
  const thread = document.createElement('article');
  thread.className = 'record-thread';
  thread.dataset.recordNumber = order.number;
  const summary = document.createElement('div');
  summary.className = 'thread-summary';
  const add = (tag, value, className) => {
    const node = document.createElement(tag); node.textContent = value; node.className = className; return node;
  };
  const created = new Date(order.created_at);
  const number = add('strong', order.number, 'thread-number'); number.title = order.number;
  const customer = add('span', order.customer, 'thread-customer'); customer.title = order.customer;
  const state = add('span', order.status, 'thread-state'); state.dataset.orderStatus = order.number;
  const toggle = add('button', '详情', 'thread-toggle'); toggle.type = 'button';
  toggle.setAttribute('aria-label', `查看订单 ${order.number} 详情`); toggle.setAttribute('aria-haspopup', 'dialog');
  summary.append(number, customer, add('span', `${items.length} 项商品`, 'thread-count'),
    add('span', Number.isNaN(created.getTime()) ? order.created_at : dateFormat.format(created), 'thread-date'), state, toggle);
  thread.append(summary);
  const content = add('div', '', 'thread-content');
  const reference = add('p', `订单号：${order.number} · 客户：${order.customer}`, 'thread-reference');
  content.append(reference);
  const toolbar = add('div', '', 'thread-toolbar');
  const statusLabel = add('label', '订单状态', 'thread-status-control');
  const feedback = add('p', '', 'inline-error'); feedback.setAttribute('role', 'status');
  const status = statusCell(order, feedback);
  while (status.firstChild) statusLabel.append(status.firstChild);
  const undo = add('button', '撤销本次修改', 'button secondary undo-order-status'); undo.type = 'button';
  undo.dataset.orderNumber = order.number;
  const edit = statusEdits.get(order.number); undo.hidden = !edit || edit.current !== order.status;
  undo.onclick = () => {
    const latest = statusEdits.get(order.number);
    if (!latest || latest.current !== order.status) return;
    const select = statusLabel.querySelector('select'); select.value = latest.previous;
    void saveStatus(order, select, feedback, true);
  };
  toolbar.append(statusLabel, undo, add('span', `邮件通知：${emailStatuses[order.email_status] ?? '—'}`, 'thread-mail'));
  const download = add('a', '下载 Excel', 'button secondary');
  download.href = `./orders/${encodeURIComponent(order.number)}.xlsx`; toolbar.append(download); content.append(toolbar);
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
  toggle.onclick = () => presentRecord({number:order.number, title:'采购员订单详情', content, trigger:toggle});
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
void loadOrders();

if(customerHistory && new URLSearchParams(location.search).get('reorder')==='1') document.querySelector('.sales-hero p').textContent='选择一笔历史订单，点击「再来一单」。商品和数量会带入采购车，交期需重新填写。';
