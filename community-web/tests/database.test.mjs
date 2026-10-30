// Database test suite for the community forum migration.
// Every test runs against its own fresh in-memory Postgres (PGlite) and talks to the
// database only through the public forum_request RPC or, for operator actions, the
// forum_private.moderate function executed as the superuser connection.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  createDatabase,
  registerMember,
  asRole,
  rpcAs,
  topicData,
  replyData,
} from './database-fixture.mjs';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

// Wraps a test body with a fresh database that is always closed afterwards.
function dbTest(name, body) {
  test(name, async () => {
    const db = await createDatabase();
    try {
      await body(db);
    } finally {
      await db.close();
    }
  });
}

// Asserts that an async call rejects with a specific custom SQLSTATE. Falls back to
// the message when PGlite does not surface a code for that particular failure.
async function expectCode(fn, code, messagePattern) {
  try {
    await fn();
  } catch (err) {
    if (err && err.code) {
      assert.equal(err.code, code, `expected SQLSTATE ${code} but got ${err.code}: ${err.message}`);
    } else {
      assert.match(err.message, messagePattern ?? new RegExp(code));
    }
    return err;
  }
  assert.fail(`expected SQLSTATE ${code} but the call resolved successfully`);
}

// Operator-only moderation, executed as the default superuser connection.
async function moderate(db, action, target, reason = 'Operator policy action here') {
  const result = await db.query('select forum_private.moderate($1,$2,$3) as result', [action, target, reason]);
  return result.rows[0].result;
}

// Clears the rate-limit ledger so a test can perform many writes without tripping limits.
async function clearActions(db) {
  await db.exec('delete from forum_private.actions');
}

async function count(db, sql, params = []) {
  const result = await db.query(sql, params);
  return result.rows[0].n;
}

// 1. Privilege isolation.

dbTest('direct table access is denied for anon and authenticated', async (db) => {
  const alice = await registerMember(db, 'alice');
  for (const [role, uid] of [['anon', null], ['authenticated', alice]]) {
    for (const table of ['posts', 'profiles', 'reports']) {
      await expectCode(
        () => asRole(db, uid, (tx) => tx.query(`select * from forum_private.${table}`), role),
        '42501',
        /permission denied/,
      );
    }
  }
});

dbTest('authenticated role cannot call the operator moderation function', async (db) => {
  const alice = await registerMember(db, 'alice');
  await expectCode(
    () => asRole(db, alice, (tx) => tx.query("select forum_private.moderate('lock', gen_random_uuid(), 'reason text')"), 'authenticated'),
    '42501',
    /permission denied/,
  );
});

dbTest('public forum_request is executable by anon and authenticated', async (db) => {
  const privs = await db.query(`
    select has_function_privilege('anon', 'public.forum_request(text,jsonb,jsonb)', 'execute') as anon,
           has_function_privilege('authenticated', 'public.forum_request(text,jsonb,jsonb)', 'execute') as authenticated,
           has_function_privilege('public', 'public.forum_request(text,jsonb,jsonb)', 'execute') as public`);
  assert.equal(privs.rows[0].anon, true);
  assert.equal(privs.rows[0].authenticated, true);
  assert.equal(privs.rows[0].public, false);
});

// 2. Forged identity.

dbTest('anon role carrying a real member subject is treated as a guest', async (db) => {
  const alice = await registerMember(db, 'alice');
  await expectCode(() => rpcAs(db, alice, 'threads', topicData(), {}, 'anon'), 'PT401');
  const session = await rpcAs(db, alice, 'session', {}, {}, 'anon');
  assert.equal(session.account, null);
});

// 3. Signup trigger.

dbTest('signup creates a lowercased profile visible through session and meta', async (db) => {
  const id = await registerMember(db, 'MixedCaseUser');
  const session = await rpcAs(db, id, 'session');
  assert.equal(session.account.username, 'mixedcaseuser');
  assert.equal(session.account.name, 'MixedCaseUser');
  const meta = await rpcAs(db, id, 'meta');
  assert.equal(meta.account.username, 'mixedcaseuser');
  assert.equal(meta.restricted, false);
});

dbTest('signup rejects a username with an invalid pattern', async (db) => {
  await expectCode(() => registerMember(db, 'bad.name'), 'PT400');
});

dbTest('signup rejects a duplicate username', async (db) => {
  await registerMember(db, 'alice');
  await expectCode(() => registerMember(db, 'alice'), 'PT409');
});

dbTest('signup rejects reserved names including obfuscated variants', async (db) => {
  for (const handle of ['admin', 'Mod_erator', 'm-a-g-i-m-a-t-i-x']) {
    await expectCode(() => registerMember(db, handle), 'PT409');
  }
});

dbTest('signup with no username metadata assigns an auto user handle', async (db) => {
  const id = await registerMember(db, undefined);
  const session = await rpcAs(db, id, 'session');
  assert.match(session.account.username, /^user_[0-9a-f]{27}$/);
  assert.equal(session.account.name, session.account.username);
});

// 4. Username availability route.

dbTest('username route reports free, taken, reserved, and invalid handles', async (db) => {
  await registerMember(db, 'alice');
  const free = await rpcAs(db, null, 'username', {}, { username: 'freshname' });
  assert.deepEqual([free.valid, free.available], [true, true]);
  const taken = await rpcAs(db, null, 'username', {}, { username: 'alice' });
  assert.deepEqual([taken.valid, taken.available], [true, false]);
  const reserved = await rpcAs(db, null, 'username', {}, { username: 'admin' });
  assert.deepEqual([reserved.valid, reserved.available], [true, false]);
  const invalid = await rpcAs(db, null, 'username', {}, { username: 'ab' });
  assert.equal(invalid.valid, false);
  assert.equal(invalid.available, false);
});

dbTest('username route rejects a non-empty payload', async (db) => {
  await expectCode(() => rpcAs(db, null, 'username', { anything: 1 }, { username: 'alice' }), 'PT400');
});

// 5. Threads.

dbTest('creating a thread returns ids on page 1, auto-follows the author, and lists with zero replies', async (db) => {
  const alice = await registerMember(db, 'alice');
  const created = await rpcAs(db, alice, 'threads', topicData());
  assert.match(created.thread_id, UUID);
  assert.match(created.post_id, UUID);
  assert.equal(created.page, 1);
  const list = await rpcAs(db, alice, 'threads');
  assert.equal(list.threads.length, 1);
  assert.equal(list.threads[0].replies, 0);
  assert.equal(list.threads[0].following, true);
});

dbTest('creating a thread validates the category', async (db) => {
  const alice = await registerMember(db, 'alice');
  await expectCode(() => rpcAs(db, alice, 'threads', topicData({ category: 'nonexistent' })), 'PT400');
});

dbTest('creating a thread enforces title and body length bounds', async (db) => {
  const alice = await registerMember(db, 'alice');
  await expectCode(() => rpcAs(db, alice, 'threads', topicData({ title: 'ab' })), 'PT400');
  await expectCode(() => rpcAs(db, alice, 'threads', topicData({ title: 'a'.repeat(141) })), 'PT400');
  await expectCode(() => rpcAs(db, alice, 'threads', topicData({ body: 'a'.repeat(12001) })), 'PT400');
  await clearActions(db);
  const edge = await rpcAs(db, alice, 'threads', topicData({ title: 'abc', body: 'a'.repeat(12000) }));
  assert.match(edge.thread_id, UUID);
});

dbTest('anon can read the thread list and a thread view', async (db) => {
  const alice = await registerMember(db, 'alice');
  const created = await rpcAs(db, alice, 'threads', topicData());
  const list = await rpcAs(db, null, 'threads');
  assert.equal(list.threads.length, 1);
  const view = await rpcAs(db, null, `threads/${created.thread_id}`);
  assert.equal(view.thread.id, created.thread_id);
  assert.equal(view.account, null);
});

dbTest('an unknown route returns PT404', async (db) => {
  const alice = await registerMember(db, 'alice');
  await expectCode(() => rpcAs(db, alice, 'nonsense/extra/branch'), 'PT404');
});

// 6. Replies.

dbTest('a reply bumps the thread updated timestamp and reply count', async (db) => {
  const alice = await registerMember(db, 'alice');
  const bruno = await registerMember(db, 'bruno');
  const created = await rpcAs(db, alice, 'threads', topicData());
  const before = (await db.query('select updated::text as u from forum_private.threads where id=$1', [created.thread_id])).rows[0].u;
  await clearActions(db);
  await rpcAs(db, bruno, `threads/${created.thread_id}/reply`, replyData());
  const bumped = await db.query('select updated > $2::timestamptz as bumped from forum_private.threads where id=$1', [created.thread_id, before]);
  assert.equal(bumped.rows[0].bumped, true);
  const list = await rpcAs(db, alice, 'threads');
  assert.equal(list.threads[0].replies, 1);
});

dbTest('thread paging splits 31 posts into 30 then 1 with correct page math', async (db) => {
  const alice = await registerMember(db, 'alice');
  const created = await rpcAs(db, alice, 'threads', topicData());
  let lastPage;
  for (let i = 0; i < 30; i++) {
    await clearActions(db);
    const reply = await rpcAs(db, alice, `threads/${created.thread_id}/reply`, replyData());
    lastPage = reply.page;
  }
  assert.equal(lastPage, 2);
  const page1 = await rpcAs(db, alice, `threads/${created.thread_id}`, {}, { page: '1' });
  assert.equal(page1.total, 31);
  assert.equal(page1.posts.length, 30);
  assert.equal(page1.page_size, 30);
  const page2 = await rpcAs(db, alice, `threads/${created.thread_id}`, {}, { page: '2' });
  assert.equal(page2.posts.length, 1);
});

dbTest('a locked thread rejects replies and unlock restores them', async (db) => {
  const alice = await registerMember(db, 'alice');
  const bruno = await registerMember(db, 'bruno');
  const created = await rpcAs(db, alice, 'threads', topicData());
  await moderate(db, 'lock', created.thread_id, 'Closing this thread');
  await clearActions(db);
  await expectCode(() => rpcAs(db, bruno, `threads/${created.thread_id}/reply`, replyData()), 'PT409');
  await moderate(db, 'unlock', created.thread_id, 'Reopening this thread');
  await clearActions(db);
  const reply = await rpcAs(db, bruno, `threads/${created.thread_id}/reply`, replyData());
  assert.match(reply.post_id, UUID);
});

// 7. Idempotency.

dbTest('a repeated request id with the same body returns the same post as a duplicate', async (db) => {
  const alice = await registerMember(db, 'alice');
  const payload = topicData();
  const first = await rpcAs(db, alice, 'threads', payload);
  const second = await rpcAs(db, alice, 'threads', payload);
  assert.equal(second.duplicate, true);
  assert.equal(second.post_id, first.post_id);
  assert.equal(second.thread_id, first.thread_id);
  assert.equal(await count(db, 'select count(*)::int as n from forum_private.posts'), 1);
});

dbTest('a repeated request id with a different body is a conflict', async (db) => {
  const alice = await registerMember(db, 'alice');
  const payload = topicData();
  await rpcAs(db, alice, 'threads', payload);
  await expectCode(() => rpcAs(db, alice, 'threads', { ...payload, body: 'A different body entirely.' }), 'PT409');
});

// 8. Ownership.

dbTest('a member cannot edit or delete another member post', async (db) => {
  const alice = await registerMember(db, 'alice');
  const bruno = await registerMember(db, 'bruno');
  const created = await rpcAs(db, alice, 'threads', topicData());
  await expectCode(() => rpcAs(db, bruno, `posts/${created.post_id}/edit`, { body: 'Taking this over.' }), 'PT403');
  await expectCode(() => rpcAs(db, bruno, `posts/${created.post_id}/delete`, {}), 'PT403');
});

dbTest('an author can edit a post and only the topic post title is editable', async (db) => {
  const alice = await registerMember(db, 'alice');
  const created = await rpcAs(db, alice, 'threads', topicData({ title: 'Original title here' }));
  await clearActions(db);
  const reply = await rpcAs(db, alice, `threads/${created.thread_id}/reply`, replyData());
  await clearActions(db);
  await rpcAs(db, alice, `posts/${created.post_id}/edit`, { body: 'Edited topic body', title: 'Renamed title here' });
  await clearActions(db);
  await rpcAs(db, alice, `posts/${reply.post_id}/edit`, { body: 'Edited reply body', title: 'This title is ignored' });
  const view = await rpcAs(db, alice, `threads/${created.thread_id}`);
  assert.equal(view.thread.title, 'Renamed title here');
  const topicPost = view.posts.find((p) => p.is_topic);
  const replyPost = view.posts.find((p) => !p.is_topic);
  assert.ok(topicPost.edited);
  assert.ok(replyPost.edited);
});

dbTest('deleting a reply blanks the body and marks it deleted', async (db) => {
  const alice = await registerMember(db, 'alice');
  const created = await rpcAs(db, alice, 'threads', topicData());
  await clearActions(db);
  const reply = await rpcAs(db, alice, `threads/${created.thread_id}/reply`, replyData());
  await clearActions(db);
  await rpcAs(db, alice, `posts/${reply.post_id}/delete`, {});
  const view = await rpcAs(db, alice, `threads/${created.thread_id}`);
  const deleted = view.posts.find((p) => p.id === reply.post_id);
  assert.equal(deleted.deleted, true);
  assert.equal(deleted.body, '');
});

dbTest('deleting the topic post hides the whole thread from lists and direct view', async (db) => {
  const alice = await registerMember(db, 'alice');
  const created = await rpcAs(db, alice, 'threads', topicData());
  await clearActions(db);
  await rpcAs(db, alice, `posts/${created.post_id}/delete`, {});
  await expectCode(() => rpcAs(db, alice, `threads/${created.thread_id}`), 'PT404');
  const list = await rpcAs(db, alice, 'threads');
  assert.equal(list.threads.find((t) => t.id === created.thread_id), undefined);
});

// 9. Likes and follows.

dbTest('likes toggle idempotently and the like count reflects them', async (db) => {
  const alice = await registerMember(db, 'alice');
  const bruno = await registerMember(db, 'bruno');
  const created = await rpcAs(db, alice, 'threads', topicData());
  await clearActions(db);
  await rpcAs(db, bruno, `posts/${created.post_id}/like`, { liked: true });
  await clearActions(db);
  await rpcAs(db, bruno, `posts/${created.post_id}/like`, { liked: true });
  let view = await rpcAs(db, bruno, `threads/${created.thread_id}`);
  let topicPost = view.posts.find((p) => p.id === created.post_id);
  assert.equal(topicPost.likes, 1);
  assert.equal(topicPost.liked, true);
  await clearActions(db);
  await rpcAs(db, bruno, `posts/${created.post_id}/like`, { liked: false });
  view = await rpcAs(db, bruno, `threads/${created.thread_id}`);
  topicPost = view.posts.find((p) => p.id === created.post_id);
  assert.equal(topicPost.likes, 0);
  assert.equal(topicPost.liked, false);
});

dbTest('follows toggle idempotently and the follow state reflects them', async (db) => {
  const alice = await registerMember(db, 'alice');
  const bruno = await registerMember(db, 'bruno');
  const created = await rpcAs(db, alice, 'threads', topicData());
  const followCount = () => count(db, 'select count(*)::int as n from forum_private.follows where thread_id=$1 and account_id=$2', [created.thread_id, bruno]);
  await clearActions(db);
  await rpcAs(db, bruno, `threads/${created.thread_id}/follow`, { following: true });
  await clearActions(db);
  await rpcAs(db, bruno, `threads/${created.thread_id}/follow`, { following: true });
  assert.equal(await followCount(), 1);
  let view = await rpcAs(db, bruno, `threads/${created.thread_id}`);
  assert.equal(view.thread.following, true);
  await clearActions(db);
  await rpcAs(db, bruno, `threads/${created.thread_id}/follow`, { following: false });
  assert.equal(await followCount(), 0);
  view = await rpcAs(db, bruno, `threads/${created.thread_id}`);
  assert.equal(view.thread.following, false);
});

// 10. Reports.

dbTest('a member cannot report their own post', async (db) => {
  const alice = await registerMember(db, 'alice');
  const created = await rpcAs(db, alice, 'threads', topicData());
  await clearActions(db);
  await expectCode(() => rpcAs(db, alice, `posts/${created.post_id}/report`, { reason: 'This is a report.' }), 'PT400');
});

dbTest('reporting another post stores one report and re-reporting updates it', async (db) => {
  const alice = await registerMember(db, 'alice');
  const bruno = await registerMember(db, 'bruno');
  const created = await rpcAs(db, alice, 'threads', topicData());
  await clearActions(db);
  await rpcAs(db, bruno, `posts/${created.post_id}/report`, { reason: 'This is spam content.' });
  let reports = await db.query('select reason from forum_private.reports');
  assert.equal(reports.rows.length, 1);
  assert.equal(reports.rows[0].reason, 'This is spam content.');
  await clearActions(db);
  await rpcAs(db, bruno, `posts/${created.post_id}/report`, { reason: 'Updated reason text.' });
  reports = await db.query('select reason from forum_private.reports');
  assert.equal(reports.rows.length, 1);
  assert.equal(reports.rows[0].reason, 'Updated reason text.');
});

dbTest('a report reason must be between 5 and 1000 characters', async (db) => {
  const alice = await registerMember(db, 'alice');
  const bruno = await registerMember(db, 'bruno');
  const created = await rpcAs(db, alice, 'threads', topicData());
  await clearActions(db);
  await expectCode(() => rpcAs(db, bruno, `posts/${created.post_id}/report`, { reason: 'four' }), 'PT400');
  await clearActions(db);
  await expectCode(() => rpcAs(db, bruno, `posts/${created.post_id}/report`, { reason: 'x'.repeat(1001) }), 'PT400');
});

// 11. Rate limits.

dbTest('the ninth new thread within the window is rate limited', async (db) => {
  const alice = await registerMember(db, 'alice');
  for (let i = 0; i < 8; i++) {
    await rpcAs(db, alice, 'threads', topicData());
  }
  await expectCode(() => rpcAs(db, alice, 'threads', topicData()), 'PT429');
});

dbTest('the thirty-first mutation within the window is rate limited', async (db) => {
  const alice = await registerMember(db, 'alice');
  const created = await rpcAs(db, alice, 'threads', topicData());
  for (let i = 0; i < 29; i++) {
    await rpcAs(db, alice, `threads/${created.thread_id}/follow`, { following: i % 2 === 0 });
  }
  await expectCode(() => rpcAs(db, alice, `threads/${created.thread_id}/follow`, { following: true }), 'PT429');
});

// 12. Suspension.

dbTest('suspension blocks writes while reads continue and unsuspend restores writing', async (db) => {
  const alice = await registerMember(db, 'alice');
  const bruno = await registerMember(db, 'bruno');
  const created = await rpcAs(db, bruno, 'threads', topicData());
  await moderate(db, 'suspend', alice, 'Posting abuse observed');
  await expectCode(() => rpcAs(db, alice, 'threads', topicData()), 'PT403');
  await expectCode(() => rpcAs(db, alice, `threads/${created.thread_id}/reply`, replyData()), 'PT403');
  await expectCode(() => rpcAs(db, alice, `threads/${created.thread_id}/follow`, { following: true }), 'PT403');
  const list = await rpcAs(db, alice, 'threads');
  assert.ok(Array.isArray(list.threads));
  const view = await rpcAs(db, alice, `threads/${created.thread_id}`);
  assert.equal(view.thread.id, created.thread_id);
  const meta = await rpcAs(db, alice, 'meta');
  assert.equal(meta.restricted, true);
  await moderate(db, 'unsuspend', alice, 'Restored after review');
  const restored = await rpcAs(db, alice, 'threads', topicData());
  assert.match(restored.thread_id, UUID);
  const metaAfter = await rpcAs(db, alice, 'meta');
  assert.equal(metaAfter.restricted, false);
});

// 13. Moderation.

dbTest('hide-thread and restore-thread toggle visibility and record moderation rows', async (db) => {
  const alice = await registerMember(db, 'alice');
  const created = await rpcAs(db, alice, 'threads', topicData());
  await moderate(db, 'hide-thread', created.thread_id, 'Off topic content');
  await expectCode(() => rpcAs(db, alice, `threads/${created.thread_id}`), 'PT404');
  assert.equal((await rpcAs(db, alice, 'threads')).threads.length, 0);
  await moderate(db, 'restore-thread', created.thread_id, 'Appeal approved');
  const view = await rpcAs(db, alice, `threads/${created.thread_id}`);
  assert.equal(view.thread.id, created.thread_id);
  const rows = await db.query('select action from forum_private.moderation order by id');
  assert.deepEqual(rows.rows.map((r) => r.action), ['hide-thread', 'restore-thread']);
});

dbTest('hide-post and restore-post toggle a reply visibility', async (db) => {
  const alice = await registerMember(db, 'alice');
  const bruno = await registerMember(db, 'bruno');
  const created = await rpcAs(db, alice, 'threads', topicData());
  await clearActions(db);
  const reply = await rpcAs(db, bruno, `threads/${created.thread_id}/reply`, replyData());
  await moderate(db, 'hide-post', reply.post_id, 'Breaking the rules');
  let view = await rpcAs(db, alice, `threads/${created.thread_id}`);
  assert.equal(view.posts.find((p) => p.id === reply.post_id), undefined);
  await moderate(db, 'restore-post', reply.post_id, 'Reinstated on review');
  view = await rpcAs(db, alice, `threads/${created.thread_id}`);
  assert.ok(view.posts.find((p) => p.id === reply.post_id));
});

dbTest('resolve-report marks a stored report resolved', async (db) => {
  const alice = await registerMember(db, 'alice');
  const bruno = await registerMember(db, 'bruno');
  const created = await rpcAs(db, alice, 'threads', topicData());
  await clearActions(db);
  await rpcAs(db, bruno, `posts/${created.post_id}/report`, { reason: 'This is spam content.' });
  const report = await db.query('select id from forum_private.reports');
  await moderate(db, 'resolve-report', report.rows[0].id, 'Handled the report');
  const resolved = await db.query('select resolved from forum_private.reports where id=$1', [report.rows[0].id]);
  assert.equal(resolved.rows[0].resolved, true);
});

dbTest('an author-deleted thread cannot be restored by moderation', async (db) => {
  const alice = await registerMember(db, 'alice');
  const created = await rpcAs(db, alice, 'threads', topicData());
  await clearActions(db);
  await rpcAs(db, alice, `posts/${created.post_id}/delete`, {});
  await expectCode(() => moderate(db, 'restore-thread', created.thread_id, 'Attempting a restore'), 'PT409');
});

// 14. Unread tracking.

dbTest('a followed thread shows unread after a reply and reading the last page clears it', async (db) => {
  const alice = await registerMember(db, 'alice');
  const bruno = await registerMember(db, 'bruno');
  const created = await rpcAs(db, alice, 'threads', topicData());
  await rpcAs(db, alice, `threads/${created.thread_id}`);
  assert.equal((await rpcAs(db, alice, 'threads')).threads[0].unread, false);
  await clearActions(db);
  await rpcAs(db, bruno, `threads/${created.thread_id}/reply`, replyData());
  assert.equal((await rpcAs(db, alice, 'threads')).threads[0].unread, true);
  await rpcAs(db, alice, `threads/${created.thread_id}`, {}, { page: '1' });
  assert.equal((await rpcAs(db, alice, 'threads')).threads[0].unread, false);
});

// 15. Search.

dbTest('search matches title and visible body text and excludes hidden content', async (db) => {
  const alice = await registerMember(db, 'alice');
  const bruno = await registerMember(db, 'bruno');
  const created = await rpcAs(db, alice, 'threads', topicData({ title: 'Zebra crossing guide', body: 'An ordinary opening post.' }));
  await clearActions(db);
  const reply = await rpcAs(db, bruno, `threads/${created.thread_id}/reply`, replyData({ body: 'A quokka sighting report.' }));
  const byTitle = await rpcAs(db, alice, 'threads', {}, { q: 'ZEBRA' });
  assert.ok(byTitle.threads.some((t) => t.id === created.thread_id));
  const byBody = await rpcAs(db, alice, 'threads', {}, { q: 'quokka' });
  assert.ok(byBody.threads.some((t) => t.id === created.thread_id));
  await moderate(db, 'hide-post', reply.post_id, 'Removing this reply');
  const afterHide = await rpcAs(db, alice, 'threads', {}, { q: 'quokka' });
  assert.equal(afterHide.threads.find((t) => t.id === created.thread_id), undefined);
  const titleStill = await rpcAs(db, alice, 'threads', {}, { q: 'zebra' });
  assert.ok(titleStill.threads.some((t) => t.id === created.thread_id));
});

dbTest('the following and mine views require sign-in', async (db) => {
  await registerMember(db, 'alice');
  await expectCode(() => rpcAs(db, null, 'threads', {}, { view: 'following' }), 'PT401');
  await expectCode(() => rpcAs(db, null, 'threads', {}, { view: 'mine' }), 'PT401');
});

dbTest('the unanswered sort returns only threads without visible replies', async (db) => {
  const alice = await registerMember(db, 'alice');
  const bruno = await registerMember(db, 'bruno');
  const answered = await rpcAs(db, alice, 'threads', topicData({ title: 'Answered thread here' }));
  await clearActions(db);
  await rpcAs(db, bruno, `threads/${answered.thread_id}/reply`, replyData());
  await clearActions(db);
  const unanswered = await rpcAs(db, alice, 'threads', topicData({ title: 'Lonely thread here' }));
  const result = await rpcAs(db, alice, 'threads', {}, { sort: 'unanswered' });
  const ids = result.threads.map((t) => t.id);
  assert.ok(ids.includes(unanswered.thread_id));
  assert.ok(!ids.includes(answered.thread_id));
});

// 16. Input hardening.

dbTest('a payload that is not a JSON object is rejected', async (db) => {
  const alice = await registerMember(db, 'alice');
  await expectCode(() => rpcAs(db, alice, 'threads', []), 'PT400');
  await expectCode(() => rpcAs(db, alice, 'threads', 5), 'PT400');
});

dbTest('an oversized payload is rejected', async (db) => {
  const alice = await registerMember(db, 'alice');
  await expectCode(() => rpcAs(db, alice, 'threads', { filler: 'x'.repeat(70000) }), 'PT400');
});

dbTest('a page number outside 1 to 10000 is rejected', async (db) => {
  const alice = await registerMember(db, 'alice');
  for (const page of ['0', '10001', 'abc']) {
    await expectCode(() => rpcAs(db, alice, 'threads', {}, { page }), 'PT400');
  }
  const ok = await rpcAs(db, alice, 'threads', {}, { page: '1' });
  assert.equal(ok.page, 1);
});

dbTest('a malformed thread id returns PT404', async (db) => {
  const alice = await registerMember(db, 'alice');
  await expectCode(() => rpcAs(db, alice, 'threads/not-a-uuid'), 'PT404');
});

dbTest('a profile update with a control character in the display name is rejected', async (db) => {
  const alice = await registerMember(db, 'alice');
  await clearActions(db);
  await expectCode(() => rpcAs(db, alice, 'profile', { name: 'bad\u0001name' }), 'PT400');
});
