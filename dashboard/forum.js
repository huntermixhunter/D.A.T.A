/* DATA community forum. All member content is rendered as text. */
(() => {
  'use strict';
  const root = document.getElementById('forum-root');
  if (!root || !window.MarketplaceUI) return;
  const { node, button, note, modal, field, busy } = window.MarketplaceUI.elements;
  const state = { meta: null, account: null, category: 'all', view: 'all', sort: 'active', query: '',
    page: 1, threadId: null, postPage: 1, result: null, message: '', error: '', drafts: new Map() };
  let sequence = 0;
  let navigation = 0;
  const active = () => document.getElementById('panel-forum').classList.contains('active');
  const shared = () => state.meta?.mode === 'shared';
  const key = () => `${state.meta?.community_origin || 'local'}:${state.account?.id || 'guest'}:${state.threadId || 'new'}`;
  const category = id => state.meta?.categories.find(item => item.id === id);
  const requestId = () => crypto.randomUUID().replaceAll('-', '');
  const date = value => new Date(value).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });

  async function api(path, data) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 25000);
    try {
      const response = await fetch(`${API_BASE}/marketplace/forum/${path}`, {
        method: data === undefined ? 'GET' : 'POST', credentials: 'same-origin', signal: controller.signal,
        headers: data === undefined ? {} : { 'Content-Type': 'application/json', 'X-Marketplace-Request': '1' },
        body: data === undefined ? undefined : JSON.stringify(data),
      });
      let result;
      try { result = await response.json(); } catch (_) { throw new Error('The forum is unavailable. Please try again.'); }
      if (!response.ok) {
        const error = new Error(result.error || 'The request could not be completed.');
        error.status = response.status;
        throw error;
      }
      return result;
    } catch (error) {
      if (error.name === 'AbortError' || error instanceof TypeError) throw new Error('Could not reach the forum. Your draft is still here; try again when connected.');
      throw error;
    } finally { clearTimeout(timer); }
  }

  function rememberReply() {
    const input = root.querySelector('#forum-reply');
    if (input?.dataset.draftKey) {
      const draft = state.drafts.get(input.dataset.draftKey) || { requestId: requestId() };
      draft.body = input.value;
      state.drafts.set(input.dataset.draftKey, draft);
    }
  }

  async function refresh() {
    if (!active()) return;
    rememberReply();
    const serial = ++sequence;
    const path = state.threadId ? `threads/${state.threadId}?page=${state.postPage}` : 'threads?' + new URLSearchParams({
      category: state.category, view: state.view, sort: state.sort, q: state.query, page: state.page,
    });
    root.setAttribute('aria-busy', 'true');
    if (!state.meta) root.replaceChildren(node('p', { class: 'mp-muted', role: 'status' }, 'Loading conversations...'));
    try {
      const [meta, result] = await Promise.all([api('meta'), api(path)]);
      if (serial !== sequence) return;
      const identityChanged = state.account?.id !== meta.account?.id || state.meta?.community_origin !== meta.community_origin;
      if (identityChanged) state.drafts.clear();
      state.meta = meta;
      state.account = meta.account;
      state.result = result;
      state.error = '';
      render();
    } catch (error) {
      if (serial !== sequence) return;
      if (error.status === 401 && state.view !== 'all') {
        state.view = 'all';
        state.account = null;
        return refresh();
      }
      state.error = error.message;
      render();
    } finally {
      if (serial === sequence) root.setAttribute('aria-busy', 'false');
    }
  }

  function choose(filters = {}) {
    navigation++;
    rememberReply();
    Object.assign(state, { threadId: null, page: 1, postPage: 1, message: '', result: null }, filters);
    refresh();
  }

  function openThread(id, page = 1) {
    navigation++;
    rememberReply();
    state.threadId = id;
    state.postPage = page;
    state.result = null;
    refresh();
    document.getElementById('panel-forum').scrollTop = 0;
  }

  function identity(author) {
    return node('span', { class: 'forum-person' }, [
      node('span', { class: 'mp-avatar', 'aria-hidden': 'true' }, author.name.slice(0, 1).toUpperCase()),
      node('span', {}, [node('strong', {}, author.name), node('span', { class: 'forum-handle' }, '@' + author.username)]),
    ]);
  }

  async function accountDialog(mode) {
    if (!state.meta) return;
    if (!shared() || state.meta.identity_scope === 'shared') {
      await window.MarketplaceUI.refresh();
      window.MarketplaceUI.openAccount(mode);
      return;
    }
    mode = mode || (state.account ? 'profile' : 'login');
    const profile = mode === 'profile', register = mode === 'register';
    const { dialog, body } = modal(profile ? 'Your community account' : register ? 'Join the community' : 'Sign in to the community');
    body.append(node('p', { class: 'mp-muted' }, 'This shared account works across devices connected to this community. Local Marketplace accounts remain on their own computer.'));
    if (state.meta.community_origin) body.append(node('p', { class: 'forum-service' }, state.meta.community_origin));
    const form = node('form', { class: 'mp-form' });
    const name = node('input', { required: true, maxlength: 60, autocomplete: 'nickname', value: profile ? state.account.name : '' });
    const username = node('input', { required: true, minlength: 3, maxlength: 32, autocomplete: 'username' });
    const password = node('input', { required: true, type: 'password', minlength: 12, maxlength: 128, autocomplete: register ? 'new-password' : 'current-password' });
    if (profile || register) form.append(field('Display name', name));
    if (!profile) form.append(field('Username', username), field('Password', password, '12 to 128 characters.'));
    const error = node('p', { class: 'mp-error', role: 'alert' });
    const submit = node('button', { type: 'submit', class: 'mp-btn mp-btn-primary' }, profile ? 'Save profile' : register ? 'Create account' : 'Sign in');
    form.append(error, submit);
    form.addEventListener('submit', event => {
      event.preventDefault();
      busy(submit, error, () => freeze(form, async () => {
        const data = profile ? { name: name.value } : { username: username.value, password: password.value };
        if (register) data.name = name.value;
        await api('account/' + mode, data);
        dialog.close();
        await refresh();
      }));
    });
    body.append(form);
    if (profile) body.append(button('Sign out', event => busy(event.currentTarget, error, async () => {
      await api('account/logout', {}); dialog.close(); state.view = 'all'; state.drafts.clear(); await refresh();
    })));
    else body.append(button(register ? 'Already have an account? Sign in' : 'Create a new account', () => {
      dialog.close(); accountDialog(register ? 'login' : 'register');
    }));
  }

  function render() {
    root.replaceChildren();
    const accountButton = button(state.account ? state.account.name : 'Sign in', () => accountDialog());
    const newButton = button('+ New discussion', () => state.account ? composer() : accountDialog('login'), 'mp-btn-primary');
    accountButton.disabled = !state.meta;
    newButton.disabled = !state.meta || state.meta.restricted;
    root.append(node('header', { class: 'mp-head forum-header' }, [
      node('div', {}, [node('div', { class: 'mp-eyebrow' }, 'DATA COMMUNITY'), node('h1', {}, 'People behind the dashboards.'),
        node('p', { class: 'mp-muted' }, 'Ask a question. Share an idea. Find your people.')]),
      node('div', { class: 'mp-actions' }, [accountButton, newButton]),
    ]));
    if (state.meta) root.append(node('div', { class: 'forum-connection' }, [
      node('span', { class: 'forum-mode' }, shared() ? 'Shared community' : 'Local preview'),
      node('p', {}, shared() ? 'Posts are visible to people connected to this community.' : 'These discussions stay on this computer. The shared community has not been connected yet.'),
    ]));
    const status = node('p', { id: 'forum-status', class: 'forum-status', role: 'status', 'aria-live': 'polite' }, state.message);
    root.append(status);
    if (state.error) {
      root.append(note(state.error, true), node('div', { class: 'mp-actions' }, [button('Try again', refresh), button('Back to discussions', () => choose())]));
      return;
    }
    if (!state.meta || !state.result) return;
    if (state.meta.restricted) root.append(note('Posting is disabled for this account. You can still read discussions.', true));
    if (state.threadId) renderThread();
    else renderList();
  }

  function renderList() {
    const rail = node('aside', { class: 'forum-rail', 'aria-label': 'Forum categories' });
    rail.append(node('h2', { class: 'forum-label' }, 'Categories'));
    const categories = [{ id: 'all', name: 'All discussions', threads: state.meta.threads }, ...state.meta.categories];
    const nav = node('nav', { class: 'forum-categories' });
    for (const item of categories) {
      const control = button('', () => choose({ category: item.id }), 'forum-category');
      control.setAttribute('aria-pressed', String(state.category === item.id));
      control.append(node('span', {}, item.name), node('span', { class: 'forum-count' }, item.threads));
      nav.append(control);
    }
    rail.append(nav, node('div', { class: 'forum-guidelines' }, [node('h3', {}, 'Room for everyone'),
      node('p', {}, 'Be kind. Stay curious. Keep private information private. Share your own work and give credit.'),
      node('p', {}, 'Use Report on a post when a moderator needs to take a look.') ]));
    const main = node('section', { class: 'forum-discussions', 'aria-label': 'Discussions' });
    const current = category(state.category);
    main.append(node('div', { class: 'forum-list-heading' }, [
      node('div', {}, [node('h2', {}, current?.name || 'All discussions'),
        node('p', { class: 'mp-muted' }, current?.description || 'Useful conversations, new ideas, and a little of everything else.')]),
      button('Refresh', refresh),
    ]));
    const tabs = node('div', { class: 'mp-tabs', 'aria-label': 'Discussion filters' });
    for (const [id, label] of [['all', 'Everyone'], ['following', 'Following'], ['mine', 'My discussions']]) {
      const tab = button(label, () => (id !== 'all' && !state.account) ? accountDialog('login') : choose({ view: id }), 'mp-tab');
      tab.setAttribute('aria-pressed', String(state.view === id));
      tabs.append(tab);
    }
    const search = node('input', { type: 'search', class: 'mp-search', 'aria-label': 'Search discussions', placeholder: 'Search discussions...', maxlength: 120, value: state.query });
    const sort = node('select', { 'aria-label': 'Sort discussions', onchange: event => choose({ sort: event.target.value }) },
      [['active', 'Recently active'], ['newest', 'Newest first'], ['unanswered', 'Unanswered']].map(([id, label]) => node('option', { value: id, selected: state.sort === id }, label)));
    const form = node('form', { class: 'mp-toolbar forum-toolbar' }, [search, node('button', { type: 'submit', class: 'mp-btn' }, 'Search'), sort]);
    form.addEventListener('submit', event => { event.preventDefault(); choose({ query: search.value.trim() }); });
    main.append(tabs, form, node('p', { class: 'forum-label', role: 'status' }, `${state.result.total} discussion${state.result.total === 1 ? '' : 's'}`));
    const list = node('div', { class: 'forum-thread-list' });
    for (const thread of state.result.threads) {
      const title = button(thread.title, () => openThread(thread.id), 'forum-thread-title');
      title.setAttribute('aria-label', thread.title);
      const meta = node('div', { class: 'forum-thread-meta' }, [node('span', { class: 'forum-tag' }, category(thread.category)?.name || thread.category),
        node('span', {}, '@' + thread.author.username), thread.locked ? node('span', {}, 'Closed') : null,
        thread.following ? node('span', {}, 'Following') : null]);
      const heading = node('div', { class: 'forum-thread-summary' }, [title, meta]);
      if (thread.unread) {
        title.setAttribute('aria-description', 'Unread activity');
        title.prepend(node('span', { class: 'forum-unread', title: 'Unread activity', 'aria-hidden': 'true' }));
      }
      list.append(node('article', { class: 'forum-thread-row' }, [heading,
        node('div', { class: 'forum-thread-activity' }, [node('span', {}, `${thread.replies} ${thread.replies === 1 ? 'reply' : 'replies'}`),
          node('time', { datetime: new Date(thread.updated).toISOString() }, date(thread.updated))]) ]));
    }
    if (!state.result.threads.length) list.append(node('div', { class: 'forum-empty' }, [
      node('span', { class: 'forum-empty-symbol', 'aria-hidden': 'true' }, '[ ... ]'),
      node('h3', {}, state.query ? 'No matching conversations yet.' : state.view === 'following' ? 'Keep good conversations close.' : 'Start the conversation.'),
      node('p', { class: 'mp-muted' }, state.query ? 'Try another search or choose a different category.' : state.view === 'following' ? 'Follow a discussion to find it here. Unread activity is marked when you return.' : 'A question, a discovery, or an idea is all it takes.'),
      button(state.query ? 'Clear search' : '+ New discussion', () => state.query ? choose({ query: '' }) : state.account ? composer() : accountDialog('login'), 'mp-btn-primary'),
    ]));
    main.append(list, pager(state.result, page => { state.page = page; refresh(); }));
    root.append(node('div', { class: 'forum-layout' }, [rail, main]));
  }

  function pager(result, move) {
    const pages = Math.max(1, Math.ceil(result.total / result.page_size));
    const previous = button('Previous', () => move(result.page - 1)); previous.disabled = result.page <= 1;
    const next = button('Next', () => move(result.page + 1)); next.disabled = result.page >= pages;
    return node('nav', { class: 'forum-pagination', 'aria-label': 'Pagination' }, [previous, node('span', {}, `Page ${result.page} of ${pages}`), next]);
  }

  function renderThread() {
    const result = state.result, thread = result.thread;
    const content = node('div', { class: 'forum-conversation' });
    content.append(node('div', { class: 'mp-actions' }, [button('Back to discussions', () => choose()), button('Refresh replies', refresh)]), node('div', { class: 'forum-topic-header' }, [
      node('span', { class: 'forum-tag' }, category(thread.category)?.name || thread.category),
      node('h2', { tabindex: '-1' }, thread.title),
      node('div', { class: 'mp-actions' }, [identity(thread.author), node('span', { class: 'mp-muted' }, `${thread.replies} replies`),
        button(thread.following ? 'Following' : 'Follow discussion', event => mutate(event.currentTarget, async () => {
          if (!state.account) { accountDialog('login'); return; }
          await api(`threads/${thread.id}/follow`, { following: !thread.following });
          await refresh();
        }))]),
    ]));
    for (const post of result.posts) {
      const actions = node('div', { class: 'forum-post-actions' });
      if (!post.deleted) {
        const like = button(`${post.liked ? 'Liked' : 'Like'}${post.likes ? ' ' + post.likes : ''}`, event => mutate(event.currentTarget, async () => {
          if (!state.account) { accountDialog('login'); return; }
          await api(`posts/${post.id}/like`, { liked: !post.liked }); await refresh();
        }));
        like.setAttribute('aria-pressed', String(post.liked));
        actions.append(like);
        if (post.own) actions.append(button('Edit', () => editPost(post, thread)), button('Delete', () => deletePost(post)));
        else if (state.account) actions.append(button('Report', () => reportPost(post)));
      }
      content.append(node('article', { class: 'forum-post', 'data-post-id': post.id, 'aria-label': `Post by ${post.author.name}` }, [
        node('header', { class: 'forum-post-header' }, [identity(post.author), node('div', { class: 'mp-muted' }, [
          node('time', { datetime: new Date(post.created).toISOString() }, date(post.created)), post.edited && !post.deleted ? ' (edited)' : '',
        ])]),
        node('p', { class: 'forum-post-body' + (post.deleted ? ' mp-muted' : '') }, post.deleted ? 'This post was deleted by its author.' : post.body), actions,
      ]));
    }
    content.append(pager(result, page => openThread(thread.id, page)));
    if (thread.locked) content.append(note('This discussion is closed to new replies.'));
    else if (!state.account) content.append(node('div', { class: 'forum-signin' }, [node('p', {}, 'Have something to add?'), button('Sign in to reply', () => accountDialog('login'), 'mp-btn-primary')]));
    else if (!state.meta.restricted) {
      const draftKey = key();
      const reply = node('textarea', { id: 'forum-reply', rows: 5, required: true, maxlength: 12000, placeholder: 'Add to the conversation...' });
      const draft = state.drafts.get(draftKey) || { body: '', requestId: requestId() };
      state.drafts.set(draftKey, draft);
      reply.dataset.draftKey = draftKey;
      reply.value = draft.body;
      reply.addEventListener('input', () => { draft.body = reply.value; });
      const error = node('p', { class: 'mp-error', role: 'alert' });
      const submit = node('button', { type: 'submit', class: 'mp-btn mp-btn-primary' }, 'Post reply');
      const form = node('form', { class: 'forum-reply mp-form' }, [field('Your reply', reply, 'Plain text. Your name and username will appear with your reply.'), error, submit]);
      form.addEventListener('submit', event => { event.preventDefault(); busy(submit, error, () => freeze(form, async () => {
        const author = state.account?.id;
        const destination = navigation;
        const sent = reply.value;
        if (draft.lastSent !== undefined && draft.lastSent !== sent) draft.requestId = requestId();
        draft.lastSent = sent;
        const response = await api(`threads/${thread.id}/reply`, { body: sent, request_id: draft.requestId });
        state.drafts.delete(draftKey); reply.value = ''; reply.dataset.draftKey = '';
        if (state.threadId === thread.id && navigation === destination && state.account?.id === author && active()) {
          state.message = 'Reply posted.';
          openThread(thread.id, response.page || 1);
        }
      })); });
      content.append(form);
    }
    root.append(content);
  }

  async function mutate(control, action) {
    const status = document.getElementById('forum-status');
    await busy(control, status, action);
  }

  async function freeze(form, action) {
    const inputs = [...form.querySelectorAll('input, textarea, select')];
    inputs.forEach(input => { input.disabled = true; });
    try { return await action(); }
    finally { inputs.forEach(input => { input.disabled = false; }); }
  }

  function composer() {
    const { dialog, body } = modal('Start a discussion');
    const form = node('form', { class: 'mp-form' });
    const title = node('input', { required: true, minlength: 3, maxlength: 140, placeholder: 'What would you like to talk about?' });
    const select = node('select', {}, state.meta.categories.map(item => node('option', { value: item.id, selected: item.id === state.category }, item.name)));
    const text = node('textarea', { required: true, rows: 8, maxlength: 12000, placeholder: 'Give people a little context...' });
    const error = node('p', { class: 'mp-error', role: 'alert' });
    const submit = node('button', { type: 'submit', class: 'mp-btn mp-btn-primary' }, 'Post discussion');
    form.append(field('Title', title), field('Category', select), field('Your post', text, 'Plain text, up to 12,000 characters. Do not share passwords or private data.'), error, submit);
    const id = requestId();
    form.addEventListener('submit', event => { event.preventDefault(); busy(submit, error, () => freeze(form, async () => {
      const result = await api('threads', { title: title.value, category: select.value, body: text.value, request_id: id });
      if (!dialog.isConnected) return;
      dialog.close(); state.message = 'Discussion posted.'; openThread(result.thread_id);
    })); });
    body.append(form);
  }

  function editPost(post, thread) {
    const { dialog, body } = modal('Edit your post');
    const form = node('form', { class: 'mp-form' });
    const title = node('input', { required: true, minlength: 3, maxlength: 140, value: thread.title });
    if (post.is_topic) form.append(field('Title', title));
    const text = node('textarea', { required: true, rows: 8, maxlength: 12000 }); text.value = post.body;
    const error = node('p', { class: 'mp-error', role: 'alert' });
    const submit = node('button', { type: 'submit', class: 'mp-btn mp-btn-primary' }, 'Save changes');
    form.append(field('Your post', text), error, submit);
    form.addEventListener('submit', event => { event.preventDefault(); busy(submit, error, () => freeze(form, async () => {
      await api(`posts/${post.id}/edit`, { body: text.value, ...(post.is_topic ? { title: title.value } : {}) });
      dialog.close(); state.message = 'Post updated.'; await refresh();
    })); });
    body.append(form);
  }

  function deletePost(post) {
    const { dialog, body } = modal(post.is_topic ? 'Delete this discussion?' : 'Delete your reply?');
    body.append(node('p', {}, post.is_topic ? 'This removes your discussion and its replies from the forum.' : 'Your reply will be replaced with a deleted-post notice. This cannot be undone.'));
    const error = node('p', { class: 'mp-error', role: 'alert' });
    body.append(error, node('div', { class: 'mp-actions' }, [button('Cancel', () => dialog.close()), button('Delete', event => busy(event.currentTarget, error, async () => {
      await api(`posts/${post.id}/delete`, {}); dialog.close();
      if (post.is_topic) choose(); else { state.message = 'Reply deleted.'; await refresh(); }
    }), 'mp-btn-danger')]));
  }

  function reportPost(post) {
    const { dialog, body } = modal('Report this post');
    body.append(node('p', { class: 'mp-muted' }, 'Reports go to the community moderator. They are not shown to other members.'));
    const reason = node('textarea', { rows: 4, required: true, minlength: 5, maxlength: 1000 });
    const error = node('p', { class: 'mp-error', role: 'alert' });
    const submit = node('button', { type: 'submit', class: 'mp-btn mp-btn-primary' }, 'Send report');
    const form = node('form', { class: 'mp-form' }, [field('What should the moderator know?', reason), error, submit]);
    form.addEventListener('submit', event => { event.preventDefault(); busy(submit, error, () => freeze(form, async () => {
      await api(`posts/${post.id}/report`, { reason: reason.value }); dialog.close();
      state.message = 'Report sent to the moderator.'; document.getElementById('forum-status').textContent = state.message;
    })); });
    body.append(form);
  }

  // `shared` lets Settings > Account say, without a request of its own, that a
  // connected community keeps its accounts on the service rather than here.
  window.ForumUI = { refresh, shared };
  window.addEventListener('data-account-changed', () => {
    if (active() && (!shared() || state.meta?.identity_scope === 'shared')) refresh();
  });
  // Do not fetch the forum when the optional page is hidden or unopened.
  if (location.hash === '#forum') {
    if (SidebarView.visible('forum')) showPanel('forum');
    else openSettingsTab('view');
  }
})();
