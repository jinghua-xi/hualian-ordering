import {presentRecord, refreshRecord, recordDeleteControl} from '/record-dialog.js?v=record-delete-1';

async function api(path, body) {
  const response = await fetch(path, body === undefined ? {cache:'no-store'} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '操作失败，请刷新后重试');
  return data;
}
function text(tag, value) { const node=document.createElement(tag);node.textContent=value;return node; }
const accountForm = document.getElementById('account-form');
async function loadAccounts() {
  const {accounts: accountRows} = await api('/api/admin/accounts');
  const list=document.getElementById('account-list'); list.replaceChildren();
  for(const account of accountRows) {
    const row=document.createElement('article');row.className='account-row';
    const identity=document.createElement('div');identity.className='account-identity';
    const name=text('strong',account.username);identity.append(name);
    const meta=document.createElement('div');meta.className='account-meta';
    meta.append(text('span',account.role === 'buyer' ? '客户采购员' : '公司销售'));
    const badge=text('span',account.active ? '已启用' : '已停用');badge.className=`account-badge ${account.active?'is-active':'is-inactive'}`;meta.append(badge);identity.append(meta);row.append(identity);
    if(account.role==='buyer') {
      const binding=document.createElement('form');binding.className='account-binding-form';
      const label=text('label','绑定客户名称');const input=document.createElement('input');input.value=account.customer_name||account.username;input.required=true;input.maxLength=80;input.setAttribute('aria-label',`${account.username}绑定的客户名称`);label.append(input);
      const save=text('button','保存名称');save.type='submit';save.className='button secondary';binding.append(label,save);identity.append(binding);
      binding.onsubmit=async event=>{event.preventDefault();save.disabled=true;try{await api('/api/admin/accounts',{action:'customer',username:account.username,customer_name:input.value});document.getElementById('account-notice').textContent='客户名称已更新，新订单将使用新名称，历史订单保留原名称。';}catch(error){document.getElementById('account-notice').textContent=error.message;}finally{save.disabled=false;}};
    }
    const reset=document.createElement('form');reset.className='account-reset-form';
    const password=document.createElement('input');password.type='password';password.required=true;password.minLength=4;password.maxLength=18;password.placeholder='新密码（4–18 位）';password.autocomplete='new-password';password.setAttribute('aria-label',`${account.username}的新密码`);reset.append(password);
    const resetButton=text('button','重置密码');resetButton.type='submit';resetButton.className='button secondary';reset.append(resetButton);row.append(reset);
    const toggle=text('button',account.active?'停用':'启用');toggle.type='button';toggle.className=`button account-toggle ${account.active?'secondary':'primary'}`;toggle.setAttribute('aria-label',`${account.active?'停用':'启用'}账号 ${account.username}`);row.append(toggle);
    const save=async action=>{
      row.querySelectorAll('button,input').forEach(node=>node.disabled=true);
      try {await api('/api/admin/accounts',{username:account.username,action,...(action==='reset'?{password:password.value}:{})});document.getElementById('account-notice').textContent=`${account.username}：${action==='reset'?'密码已重置':action==='disable'?'账号已停用':'账号已启用'}；旧登录已失效。`;await loadAccounts();}
      catch(error){document.getElementById('account-notice').textContent=error.message;}
      finally {row.querySelectorAll('button,input').forEach(node=>node.disabled=false);}
    };
    reset.onsubmit=event=>{event.preventDefault();void save('reset');};
    toggle.onclick=()=>void save(account.active?'disable':'enable');
    list.append(row);
  }
  document.getElementById('account-count').textContent=`${accountRows.length} 个账号`;
  if(!accountRows.length) {const empty=text('p','暂无已派发账号，请在上方创建。');empty.className='account-empty';list.append(empty);}
}
if(accountForm) {
 accountForm.onsubmit=async event=>{event.preventDefault();const button=accountForm.querySelector('button');button.disabled=true;try{await api('/api/admin/accounts',{action:'create',username:document.getElementById('account-name').value,customer_name:document.getElementById('account-customer').value,password:document.getElementById('account-password').value,role:document.getElementById('account-role').value});accountForm.reset();document.getElementById('account-notice').textContent='账号已创建，请通过内部渠道派发。';await loadAccounts();}catch(error){document.getElementById('account-notice').textContent=error.message;}finally{button.disabled=false;}};
 loadAccounts().catch(error=>document.getElementById('account-notice').textContent=error.message);
}
const inquiryList=document.getElementById('inquiries');
const inquiryStatusEdits=new Map();
const inquiryStatusMessages=new Map();
const inquiryDateFormat=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'});
const inquiryNumberFormat=new Intl.NumberFormat('zh-CN');
async function inquiries(){
 const refresh=document.getElementById('refresh-inquiries');
 const error=document.getElementById('inquiry-error');
 refresh.disabled=true;error.hidden=true;
 try {
  const [result,catalog,clock]=await Promise.all([api('/api/sales/inquiries'),api('/api/catalog').catch(()=>({products:[]})),api('/api/time').catch(()=>({}))]);
  const products=new Map(catalog.products.map(product=>[product.sku,product]));
  const rows=document.createDocumentFragment();
  if(!result.inquiries.length) {const empty=text('p','暂无新客户询价。');empty.className='empty-orders';rows.append(empty);}
  for(const inquiry of result.inquiries){
   const items=inquiry.payload.items;
   const thread=document.createElement('article');thread.className='record-thread';thread.dataset.recordNumber=inquiry.number;
   const summary=document.createElement('div');summary.className='thread-summary';
   const number=text('strong',inquiry.number);number.className='thread-number';number.title=inquiry.number;
   const customer=text('span',inquiry.payload.customer);customer.className='thread-customer';customer.title=inquiry.payload.customer;
   const count=text('span',`${items.length} 项商品`);count.className='thread-count';
   const created=new Date(inquiry.created_at);const submitted=text('span',Number.isNaN(created.getTime())?inquiry.created_at:inquiryDateFormat.format(created));submitted.className='thread-date';
   const state=text('span',inquiry.order_deleted?'关联订单已删除':inquiry.status||'未确认订单');state.className='thread-state';
   const toggle=text('button','详情');toggle.type='button';toggle.className='thread-toggle';toggle.setAttribute('aria-haspopup','dialog');toggle.setAttribute('aria-label',`查看询价 ${inquiry.number} 详情`);
   summary.append(number,customer,count,submitted,state,toggle);thread.append(summary);
   const content=document.createElement('div');content.className='thread-content';
   const reference=text('p',`询价编号：${inquiry.number} · 客户：${inquiry.payload.customer}`);reference.className='thread-reference';content.append(reference);
   const contacts=document.createElement('dl');contacts.className='thread-contacts';
   for(const [key,value] of Object.entries(inquiry.payload.contacts)){
    const group=document.createElement('div');group.append(text('dt',({qq:'QQ',email:'邮箱',phone:'电话',wechat:'微信'})[key]||key),text('dd',value));contacts.append(group);
   }
   content.append(contacts);
   const wrap=document.createElement('div');wrap.className='thread-items-wrap';wrap.tabIndex=0;wrap.setAttribute('aria-label',`询价 ${inquiry.number} 商品明细，可横向滚动`);
   const table=document.createElement('table');table.className='thread-items';
   const caption=text('caption',`询价 ${inquiry.number} 商品明细`);caption.className='sr-only';table.append(caption);
   const head=document.createElement('thead');const header=document.createElement('tr');
   for(const title of ['商品与货号','规格与颜色','数量','期望交期','备注']){const th=text('th',title);th.scope='col';header.append(th);}head.append(header);table.append(head);
   const body=document.createElement('tbody');
   for(const item of items){
    const product=products.get(item.sku)||{};const row=document.createElement('tr');
    const productCell=text('td',product.name||item.sku);productCell.append(text('small',product.manufacturer_sku||item.sku));
    const spec=text('td',product.spec||'—');if(product.color)spec.append(text('small',product.color));
    row.append(productCell,spec,text('td',`${inquiryNumberFormat.format(item.quantity)} ${product.unit||''}`),text('td',item.delivery_date||'—'),text('td',item.note||'—'));body.append(row);
   }
   table.append(body);wrap.append(table);content.append(wrap);
   const form=document.createElement('form');form.className='inquiry-convert';
   form.onsubmit=event=>event.preventDefault();
   const actions=document.createElement('div');actions.className='inquiry-convert-actions';
   const statusLabel=document.createElement('label');statusLabel.className='thread-status-control';statusLabel.append(text('span','订单状态'));
   const select=document.createElement('select');select.className='inquiry-status';select.setAttribute('aria-label',`询价 ${inquiry.number} 的订单状态`);
   const current=inquiry.status||'未确认订单';
   for(const value of result.statuses||['未确认订单','已确认订单','已发货','已交付'])select.append(new Option(value,value));
   select.value=current;select.disabled=inquiry.order_deleted;statusLabel.append(select);actions.append(statusLabel);
   const undo=text('button','撤销本次修改');undo.type='button';undo.className='button secondary';
   const lastEdit=inquiryStatusEdits.get(inquiry.number);undo.hidden=inquiry.order_deleted||!lastEdit||lastEdit.current!==current;actions.append(undo);form.append(actions);
   let adjustment=null,date=null;
   if(!inquiry.order_number){
    adjustment=document.createElement('details');adjustment.className='inquiry-date-adjustment';adjustment.append(text('summary','统一调整交期（选填）'));
    const label=document.createElement('label');label.className='inquiry-date-field';label.append(text('span','整单期望交期'));
    date=document.createElement('input');date.type='date';date.disabled=true;if(clock.min_delivery_date)date.min=clock.min_delivery_date;label.append(date);adjustment.append(label);
    const hint=text('p','首次改变状态时应用此交期，覆盖所有商品交期，至少预留7天。');hint.className='field-hint';adjustment.append(hint);
    adjustment.addEventListener('toggle',()=>{date.disabled=!adjustment.open;if(!adjustment.open)date.value='';});form.append(adjustment);
   }
   const explanation=text('p',inquiry.order_deleted?`关联订单 ${inquiry.order_number} 已删除，不能再次转单或修改状态；本询价保留。`:inquiry.order_number?`正式订单：${inquiry.order_number}。状态与采购员订单同步；撤销只恢复状态。`:'改为已确认、已发货或已交付时生成正式订单，默认沿用各商品原交期。');explanation.className='inquiry-status-help';form.append(explanation);
   const message=text('p',inquiryStatusMessages.get(inquiry.number)||'');message.className='inquiry-status-message';message.setAttribute('role','status');form.append(message);
   const localError=text('p','');localError.className='inline-error';localError.setAttribute('role','alert');localError.hidden=true;form.append(localError);
   async function persistStatus(isUndo=false){
    if(inquiry.order_deleted)return;
    const edit=inquiryStatusEdits.get(inquiry.number);
    if(isUndo&&(!edit||edit.current!==current))return;
    if(!form.reportValidity()){select.value=current;return;}
    const desired=isUndo?edit.previous:select.value;
    select.disabled=true;undo.disabled=true;if(date)date.disabled=true;localError.hidden=true;message.textContent='正在保存状态…';
    try{
     const body={status:desired,expected_status:current};if(adjustment?.open&&date.value)body.delivery_date=date.value;
     const saved=await api(`/api/sales/inquiries/${inquiry.number}/status`,body);
     if(isUndo)inquiryStatusEdits.delete(inquiry.number);
     else if(current!==saved.status)inquiryStatusEdits.set(inquiry.number,{previous:current,current:saved.status});
     inquiryStatusMessages.set(inquiry.number,isUndo?`已撤销本次修改，恢复为「${saved.status}」。`:`状态已保存为「${saved.status}」。`);
     await inquiries();document.getElementById('refresh-orders').click();
    }catch(failure){select.value=current;message.textContent='';localError.textContent=failure.message;localError.hidden=false;}
    finally{select.disabled=false;undo.disabled=false;if(date)date.disabled=!adjustment.open;}
   }
   select.onchange=()=>void persistStatus();undo.onclick=()=>void persistStatus(true);
   content.insertBefore(form,wrap);
   content.insertBefore(recordDeleteControl({kind:'inquiry',number:inquiry.number,status:()=>inquiry.status||'未确认订单',onDeleted:async()=>{
    inquiryStatusEdits.delete(inquiry.number);inquiryStatusMessages.delete(inquiry.number);
    await inquiries();
    document.getElementById('inquiry-notice').textContent=`已删除询价 ${inquiry.number}，关联订单不受影响。`;
    document.getElementById('refresh-inquiries').focus();
   }}),wrap);
   toggle.onclick=()=>presentRecord({number:inquiry.number,title:'新客户询价详情',content,trigger:toggle});
   refreshRecord(inquiry.number,content);rows.append(thread);
  }
  inquiryList.replaceChildren(rows);
 } catch(failure) {error.textContent=failure.message;error.hidden=false;}
 finally {refresh.disabled=false;}
}
if(inquiryList){
 document.getElementById('refresh-inquiries').onclick=()=>void inquiries();
 document.addEventListener('sales-record-deleted',event=>{if(event.detail.kind==='order')void inquiries();});
 void inquiries();
}

const accountsLink=document.getElementById('accounts-link');
if(accountsLink)api('/api/me').then(me=>{accountsLink.hidden=me.role!=='admin';}).catch(()=>{});

const notificationForm=document.getElementById('notification-form');
if(notificationForm) {
  const enabled=document.getElementById('notification-enabled');
  const recipient=document.getElementById('notification-recipient');
  const save=document.getElementById('notification-save');
  const label=document.getElementById('notification-switch-label');
  const status=document.getElementById('notification-status');
  let saved=null;
  const busy=value=>{enabled.disabled=value;recipient.disabled=value;save.disabled=value;};
  function showSettings(settings) {
    saved=settings;enabled.checked=settings.enabled;recipient.value=settings.recipient;
    label.textContent=settings.enabled?'已启用':'已关闭';
    const service=settings.demo_mode?'当前为本地演示，不会实际发信。':settings.configured?'发件服务已配置。':'发件服务尚未配置，请先在服务器配置 SMTP。';
    status.textContent=`${service} 待发 ${settings.pending_count} 封。`;
    status.classList.remove('is-error');
  }
  async function loadSettings() {
    busy(true);
    try {showSettings(await api('/api/sales/email-settings'));busy(false);}
    catch(error) {status.textContent=error.message;status.classList.add('is-error');label.textContent='读取失败';}
  }
  async function saveSettings() {
    if(!saved)return;
    if(!notificationForm.reportValidity()) {enabled.checked=saved.enabled;return;}
    const body={enabled:enabled.checked,recipient:recipient.value.trim(),version:saved.version};
    busy(true);status.textContent='正在保存…';status.classList.remove('is-error');
    try {
      showSettings(await api('/api/sales/email-settings',body));
      status.textContent=`设置已保存。${status.textContent}`;
    } catch(error) {
      enabled.checked=saved.enabled;label.textContent=saved.enabled?'已启用':'已关闭';
      status.textContent=error.message;status.classList.add('is-error');
    } finally {busy(false);}
  }
  notificationForm.onsubmit=event=>{event.preventDefault();void saveSettings();};
  enabled.onchange=()=>void saveSettings();
  recipient.oninput=()=>{status.textContent='邮箱已修改，点击“保存邮箱”后生效。';status.classList.remove('is-error');};
  void loadSettings();
}
