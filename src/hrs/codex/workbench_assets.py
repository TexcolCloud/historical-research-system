"""Packaged browser enhancement without a separate frontend build."""

SCRIPT = r"""
/* Local progressive enhancement: synchronize progress while preserving edits. */
(() => {
  const workspace = document.body.dataset.workspaceId;
  const indicator = document.querySelector('[data-sync-status]');
  const changes = new URL('/changes', location.href);
  changes.searchParams.set('token', new URL(location.href).searchParams.get('token'));
  let revision = null;
  let pending = false;
  let failures = 0;

  function message(text) {
    indicator.textContent = text;
  }

  document.addEventListener('input', (event) => {
    if (event.target.form) event.target.form.dataset.dirty = 'true';
  });

  function merge(page) {
    let deferred = false;
    for (const current of document.querySelectorAll('[data-live-section]')) {
      const key = current.dataset.liveSection;
      const incoming = page.querySelector(`[data-live-section="${key}"]`);
      if (!incoming) continue;
      // An open human form remains the operator's working copy, even when clean.
      if (key === 'review-content' && current.querySelector('form')) {
        const before = current.querySelector('[name=input_sha256]')?.value;
        const after = incoming.querySelector('[name=input_sha256]')?.value;
        if (before !== after) {
          message('待审页面已更新。当前编辑内容保留，请复制修改后刷新核对。');
          current.querySelector('form').dataset.stale = 'true';
          for (const button of current.querySelectorAll('button[name=verdict]')) {
            button.disabled = true;
          }
        }
        continue;
      }
      if (current.querySelector('form[data-dirty=true]') ||
          current.contains(document.activeElement)) {
        deferred = true;
        continue;
      }
      const openDetails = [...current.querySelectorAll('details')].map(node => node.open);
      [...incoming.querySelectorAll('details')].forEach((node, index) => {
        if (openDetails[index]) node.open = true;
      });
      current.replaceWith(incoming);
    }
    return deferred;
  }

  async function poll() {
    try {
      const response = await fetch(changes, {cache: 'no-store', mode: 'same-origin'});
      if (!response.ok) throw new Error('Disconnected');
      const state = await response.json();
      if (state.workspace_id !== workspace) {
        message('工作区连接不一致。当前编辑内容保留，请重新打开工作台。');
        return;
      }
      if (revision !== state.revision || pending) {
        const snapshotURL = new URL(location.href);
        const workingForm = document.querySelector('[data-live-section="review-content"] form');
        if (workingForm) {
          // A new queue entry must not change which original the person is editing.
          snapshotURL.searchParams.set('document', workingForm.elements.document_id.value);
          snapshotURL.searchParams.set('page', workingForm.elements.page.value);
        }
        const snapshot = await fetch(snapshotURL, {cache: 'no-store', mode: 'same-origin'});
        if (!snapshot.ok) throw new Error('Disconnected');
        const page = new DOMParser().parseFromString(await snapshot.text(), 'text/html');
        if (page.body.dataset.workspaceId !== workspace) throw new Error('Workspace changed');
        message('进度自动更新');
        pending = merge(page);
        revision = state.revision;
      } else if (failures) {
        message('连接已恢复，进度自动更新');
      }
      failures = 0;
    } catch {
      failures += 1;
      message('连接暂时中断，编辑内容保留，正在自动重连…');
    }
    setTimeout(poll, Math.min(1500 * (failures + 1), 15000));
  }

  document.addEventListener('submit', async (event) => {
    const form = event.target;
    if (form.method.toLowerCase() !== 'post') return;
    event.preventDefault();
    if (form.dataset.submitting || form.dataset.stale) return;
    form.dataset.submitting = 'true';
    let alert = form.querySelector('.form-error');
    if (!alert) {
      alert = document.createElement('p');
      alert.className = 'notice form-error';
      alert.setAttribute('role', 'alert');
      form.prepend(alert);
    }
    alert.hidden = true;
    const body = new URLSearchParams(new FormData(form));
    if (event.submitter?.name) body.append(event.submitter.name, event.submitter.value);
    const buttons = [...form.querySelectorAll('button')];
    buttons.forEach(button => { button.disabled = true; });
    try {
      const response = await fetch(form.action, {
        method: 'POST', body, mode: 'same-origin',
        headers: {'X-HRS-Form': '1'},
      });
      if (!response.ok) {
        const errorPage = new DOMParser().parseFromString(await response.text(), 'text/html');
        throw new Error(errorPage.body.textContent.trim() || '提交失败，请核对后重试。');
      }
      if (!response.redirected || new URL(response.url).origin !== location.origin) {
        throw new Error('未收到保存回执，当前内容已保留。请重试。');
      }
      location.assign(response.url);
    } catch (error) {
      alert.textContent = error instanceof TypeError
        ? '连接中断，输入内容已保留。连接恢复后可重新提交。' : error.message;
      alert.hidden = false;
      if (!form.dataset.stale) buttons.forEach(button => { button.disabled = false; });
      delete form.dataset.submitting;
    }
  });

  poll();
})();
"""
