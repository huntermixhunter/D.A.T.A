let serial = 0;

export function node(tag, attrs = {}, children = []) {
  const element = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key.startsWith('on')) element.addEventListener(key.slice(2), value);
    else if (key === 'class') element.className = value;
    else if (value !== undefined && value !== null && value !== false) element.setAttribute(key, value === true ? '' : String(value));
  }
  for (const child of [children].flat()) {
    if (child !== null && child !== undefined) element.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return element;
}

export const button = (label, action, extra = '') => node('button', { type: 'button', class: 'mp-btn ' + extra, onclick: action }, label);
export const note = (text, warning = false) => node('div', { class: 'mp-note' + (warning ? ' mp-note-warning' : ''), role: warning ? 'alert' : 'status' }, text);

export function modal(title) {
  const id = 'community-dialog-' + ++serial;
  const dialog = node('dialog', { class: 'mp-dialog', 'aria-labelledby': id });
  const close = button('×', () => dialog.close(), 'mp-dialog-close');
  close.setAttribute('aria-label', 'Close dialog');
  const body = node('div', { class: 'mp-dialog-body' });
  dialog.append(node('div', { class: 'mp-dialog-head' }, [node('h2', { id }, title), close]), body);
  const previousFocus = document.activeElement;
  dialog.addEventListener('close', () => {
    dialog.remove();
    if (previousFocus?.isConnected) previousFocus.focus();
    else document.getElementById('forum-root')?.focus({ preventScroll: true });
  });
  document.body.append(dialog);
  dialog.showModal();
  return { dialog, body };
}

export async function busy(control, error, action) {
  control.disabled = true;
  error.textContent = '';
  try { await action(); }
  catch (caught) { error.textContent = caught.message || 'Please try again.'; }
  finally { control.disabled = false; }
}

export function field(label, input, hint = '') {
  const id = input.id || 'community-field-' + ++serial;
  input.id = id;
  if (hint) input.setAttribute('aria-describedby', id + '-hint');
  return node('div', { class: 'mp-field' }, [node('label', { for: id }, label), input, hint ? node('small', { id: id + '-hint' }, hint) : null]);
}
