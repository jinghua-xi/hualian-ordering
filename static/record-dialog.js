let dialog;
let dialogBody;
let dialogTitle;
let dialogNumber;
let opener;

const statusLabels = new Map([
  ['未确认订单', '未确认'],
  ['已确认订单', '已确认'],
  ['已发货', '已发货'],
  ['已交付', '已收货'],
]);

export function orderStatusLabel(status) {
  return statusLabels.get(status) ?? status;
}

export function setProgressStatus(progress, status) {
  const buttons = [...progress.querySelectorAll('button')];
  const current = buttons.findIndex(button => button.value === status);
  buttons.forEach((button, index) => {
    button.parentElement.classList.toggle('is-past', index < current);
    button.parentElement.classList.toggle('is-current', index === current);
    button.setAttribute('aria-pressed', String(index === current));
  });
}

export function statusProgress({number, status, onChange, disabled = false}) {
  const progress = document.createElement('ol');
  progress.className = 'status-progress';
  progress.setAttribute('aria-label', `${number} 订单状态`);
  for (const [value, label] of statusLabels) {
    const step = document.createElement('li');
    const node = document.createElement('span');
    node.className = 'status-node';
    node.setAttribute('aria-hidden', 'true');
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'status-step-button';
    button.value = value;
    button.textContent = label;
    button.disabled = disabled;
    button.onclick = () => void onChange(value);
    step.append(node, button);
    progress.append(step);
  }
  setProgressStatus(progress, status);
  return progress;
}

export function recordProgressSummary({number, customer, submitted, count, progress, details, unavailable = false}) {
  const summary = document.createElement('div');
  summary.className = 'record-progress-summary';
  const identity = document.createElement('div');
  identity.className = 'record-progress-identity';
  for (const [tag, value, className] of [
    ['strong', number, 'thread-number'], ['span', customer, 'thread-customer'],
    ['span', submitted, 'thread-date'], ['span', `${count} 项商品`, 'thread-count'],
  ]) {
    const item = document.createElement(tag);
    item.className = className;
    item.textContent = value;
    item.title = value;
    identity.append(item);
  }
  const controls = document.createElement('div');
  const heading = document.createElement('div');
  heading.className = 'record-progress-heading';
  const label = document.createElement('span');
  label.textContent = unavailable ? '关联订单已删除' : '订单状态';
  heading.append(label, details);
  controls.append(heading, progress);
  summary.append(identity, controls);
  return summary;
}

function outside(event) {
  const box = dialog.getBoundingClientRect();
  return event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom;
}

function prepareDialog() {
  if (dialog) return;
  dialog = document.createElement('dialog');
  dialog.className = 'record-dialog';
  dialog.setAttribute('aria-labelledby', 'record-dialog-title');
  const header = document.createElement('header');
  header.className = 'record-dialog-header';
  const identity = document.createElement('div');
  dialogTitle = document.createElement('h2'); dialogTitle.id = 'record-dialog-title';
  dialogNumber = document.createElement('p');
  identity.append(dialogTitle, dialogNumber);
  const close = document.createElement('button'); close.type = 'button';
  close.className = 'record-dialog-close'; close.textContent = '×'; close.autofocus = true;
  close.setAttribute('aria-label', '关闭详情'); close.onclick = () => dialog.close();
  header.append(identity, close);
  dialogBody = document.createElement('div'); dialogBody.className = 'record-dialog-body';
  dialog.append(header, dialogBody); document.body.append(dialog);
  let pressedOutside = false;
  dialog.addEventListener('pointerdown', event => { pressedOutside = outside(event); });
  dialog.addEventListener('click', event => {
    if (pressedOutside && outside(event)) dialog.close();
    pressedOutside = false;
  });
  dialog.addEventListener('close', () => {
    document.documentElement.classList.remove('record-dialog-open');
    const number = dialog.dataset.recordNumber;
    dialogBody.replaceChildren();
    if (opener?.isConnected) opener.focus();
    else {
      const replacement = [...document.querySelectorAll('[data-record-number]')].find(node => node.dataset.recordNumber === number);
      replacement?.querySelector('.thread-toggle')?.focus();
    }
  });
}

export function presentRecord({number, title, content, trigger}) {
  prepareDialog();
  opener = trigger;
  dialog.dataset.recordNumber = number;
  dialogTitle.textContent = title; dialogNumber.textContent = number;
  dialogBody.replaceChildren(content); dialogBody.scrollTop = 0;
  document.documentElement.classList.add('record-dialog-open');
  if (!dialog.open) dialog.showModal();
}

export function refreshRecord(number, content) {
  if (dialog?.open && dialog.dataset.recordNumber === number) dialogBody.replaceChildren(content);
}

export function recordDeleteControl({kind, number, status, onDeleted}) {
  const label = kind === 'order' ? '订单' : '询价';
  const route = kind === 'order' ? 'orders' : 'inquiries';
  const control = document.createElement('div'); control.className = 'record-delete-control';
  const start = document.createElement('button'); start.type = 'button';
  start.className = 'button secondary record-delete-trigger'; start.textContent = `删除${label}`;
  const confirmation = document.createElement('div'); confirmation.className = 'record-delete-confirmation';
  confirmation.hidden = true; confirmation.setAttribute('role', 'group'); confirmation.setAttribute('aria-label', '删除确认');
  const warning = document.createElement('p');
  warning.textContent = `确认删除${label} ${number}？记录将从列表移除，数据库保留留档。` +
    (kind === 'order' ? '尚未发送的通知将停止，已发送邮件无法撤回；关联询价保留。' : '关联的正式订单保留，不会一起删除。');
  const confirm = document.createElement('button'); confirm.type = 'button';
  confirm.className = 'button record-delete-confirm'; confirm.textContent = `确认删除${label}`;
  const cancel = document.createElement('button'); cancel.type = 'button';
  cancel.className = 'button secondary'; cancel.textContent = '取消';
  const error = document.createElement('p'); error.className = 'inline-error'; error.hidden = true; error.setAttribute('role', 'alert');
  confirmation.append(warning, confirm, cancel, error); control.append(start, confirmation);
  let expectedStatus;
  start.onclick = () => {
    expectedStatus = status(); start.hidden = true; confirmation.hidden = false; error.hidden = true; cancel.focus();
  };
  cancel.onclick = () => { confirmation.hidden = true; start.hidden = false; start.focus(); };
  confirm.onclick = async () => {
    confirm.disabled = true; cancel.disabled = true; error.hidden = true;
    try {
      const response = await fetch(`/api/sales/${route}/${encodeURIComponent(number)}/delete`, {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({confirm_number:number, expected_status:expectedStatus}),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok || result.deleted !== true) throw new Error(result.error || '删除失败，请刷新后重试。');
      if (dialog?.open && dialog.dataset.recordNumber === number) dialog.close();
      await onDeleted();
      document.dispatchEvent(new CustomEvent('sales-record-deleted', {detail:{kind, number}}));
    } catch (failure) {
      error.textContent = failure.message || '删除失败，请稍后重试。'; error.hidden = false;
    } finally { confirm.disabled = false; cancel.disabled = false; }
  };
  return control;
}
