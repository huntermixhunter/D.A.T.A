import { readFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { PGlite } from '@electric-sql/pglite';

export async function createDatabase() {
  const db = new PGlite();
  await db.exec(`
    create role anon nologin;
    create role authenticated nologin;
    create schema auth;
    grant usage on schema public, auth to anon, authenticated;
    create table auth.users (
      id uuid primary key,
      email text,
      raw_user_meta_data jsonb default '{}'::jsonb,
      raw_app_meta_data jsonb default '{}'::jsonb
    );
    create function auth.uid() returns uuid language sql stable as $$
      select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid;
    $$;
    grant execute on function auth.uid() to anon, authenticated;
  `);
  const migration = await readFile(new URL('../supabase/migrations/202610290001_forum.sql', import.meta.url), 'utf8');
  try {
    await db.exec(migration);
    return db;
  } catch (error) {
    await db.close();
    throw error;
  }
}

export async function registerMember(db, username, extra = {}) {
  const id = extra.id ?? randomUUID();
  const metadata = username === undefined
    ? (extra.metadata ?? {})
    : { username, display_name: username, ...extra.metadata };
  await db.query('insert into auth.users(id,email,raw_user_meta_data,raw_app_meta_data) values($1,$2,$3,$4)',
    [id, extra.email ?? `${username ?? 'fallback'}@test.invalid`, metadata, extra.appMetadata ?? {}]);
  return id;
}

export async function asRole(db, uid, callback, role = uid ? 'authenticated' : 'anon') {
  if (!['anon', 'authenticated'].includes(role)) throw new Error('Only public roles may use this helper.');
  return db.transaction(async transaction => {
    await transaction.exec(`set local role ${role}`);
    await transaction.query("select set_config('request.jwt.claim.sub',$1,true)", [uid ?? '']);
    return callback(transaction);
  });
}

export async function rpcAs(db, uid, route, payload = {}, query = {}, role = uid ? 'authenticated' : 'anon') {
  return asRole(db, uid, async transaction => {
    const result = await transaction.query('select public.forum_request($1,$2,$3) as result', [route, payload, query]);
    return result.rows[0].result;
  }, role);
}

export function topicData(extra = {}) {
  return { title: 'A useful discussion', category: 'general', body: 'A question about the community.',
    request_id: randomUUID().replaceAll('-', ''), ...extra };
}

export function replyData(extra = {}) {
  return { body: 'Here is a helpful reply.', request_id: randomUUID().replaceAll('-', ''), ...extra };
}
