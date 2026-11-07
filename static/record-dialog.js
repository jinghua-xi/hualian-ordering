let dialog;
let dialogBody;
let dialogTitle;
let dialogNumber;
let opener;

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
