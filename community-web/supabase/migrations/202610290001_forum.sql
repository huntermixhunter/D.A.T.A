-- Public community data only. Never import dashboard accounts, secrets, or history.
-- Supabase Auth owns credentials; the application sees public profile fields only.
begin;

create schema if not exists forum_private;
revoke all on schema forum_private from public, anon, authenticated;

create table forum_private.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  name text not null check (char_length(name) between 1 and 60 and name !~ '[[:cntrl:]]'),
  username text not null unique check (username ~ '^[a-z0-9_][a-z0-9_-]{2,31}$'),
  created timestamptz not null default clock_timestamp()
);

create table forum_private.categories (
  id text primary key,
  name text not null,
  description text not null,
  position smallint not null unique
);

insert into forum_private.categories values
  ('general', 'Dashboard General', 'Conversations about using DATA, workflows, and everyday discoveries.', 1),
  ('help', 'Help & Troubleshooting', 'Ask a question, compare notes, and help someone get unstuck.', 2),
  ('ideas', 'Ideas & Requests', 'Suggest improvements and discuss what DATA could do next.', 3),
  ('showcase', 'Show & Tell', 'Share something you made, a useful setup, or a small win.', 4),
  ('extensions', 'Extensions', 'Talk about themes, skills, tools, and marketplace creations.', 5),
  ('off-topic', 'Off Topic', 'Everything else. A place to get to know the people behind the dashboards.', 6);

create table forum_private.threads (
  id uuid primary key default gen_random_uuid(),
  author_id uuid not null references forum_private.profiles(id) on delete cascade,
  category text not null references forum_private.categories(id),
  title text not null check (char_length(title) between 3 and 140),
  created timestamptz not null,
  updated timestamptz not null,
  locked boolean not null default false,
  hidden boolean not null default false
);

create table forum_private.posts (
  id uuid primary key default gen_random_uuid(),
  thread_id uuid not null references forum_private.threads(id) on delete cascade,
  author_id uuid not null references forum_private.profiles(id) on delete cascade,
  body text not null check (char_length(body) <= 12000),
  created timestamptz not null,
  edited timestamptz,
  deleted boolean not null default false,
  hidden boolean not null default false,
  is_topic boolean not null default false,
  request_id text not null check (request_id ~ '^[A-Za-z0-9_-]{16,80}$'),
  request_hash bytea not null check (octet_length(request_hash) = 32),
  unique (author_id, request_id),
  check (deleted or char_length(body) > 0)
);
create unique index one_topic_post on forum_private.posts(thread_id) where is_topic;
create index topic_activity on forum_private.threads(updated desc, id desc) where not hidden;
create index category_activity on forum_private.threads(category, updated desc, id desc) where not hidden;
create index member_topics on forum_private.threads(author_id, updated desc, id desc) where not hidden;
create index post_order on forum_private.posts(thread_id, is_topic desc, created, id) where not hidden;

create table forum_private.likes (
  post_id uuid not null references forum_private.posts(id) on delete cascade,
  account_id uuid not null references forum_private.profiles(id) on delete cascade,
  primary key (post_id, account_id)
);
create table forum_private.follows (
  thread_id uuid not null references forum_private.threads(id) on delete cascade,
  account_id uuid not null references forum_private.profiles(id) on delete cascade,
  primary key (thread_id, account_id)
);
create index member_follows on forum_private.follows(account_id, thread_id);
create table forum_private.reads (
  thread_id uuid not null references forum_private.threads(id) on delete cascade,
  account_id uuid not null references forum_private.profiles(id) on delete cascade,
  seen timestamptz not null,
  primary key (thread_id, account_id)
);
create table forum_private.reports (
  id uuid primary key default gen_random_uuid(),
  post_id uuid not null references forum_private.posts(id) on delete cascade,
  account_id uuid not null references forum_private.profiles(id) on delete cascade,
  reason text not null check (char_length(reason) between 5 and 1000),
  created timestamptz not null,
  resolved boolean not null default false,
  unique (post_id, account_id)
);
create index unresolved_reports on forum_private.reports(created) where not resolved;
create table forum_private.restrictions (
  account_id uuid primary key references forum_private.profiles(id) on delete cascade,
  reason text not null check (char_length(reason) between 5 and 1000)
);
create table forum_private.actions (
  account_id uuid not null references forum_private.profiles(id) on delete cascade,
  kind text not null,
  created timestamptz not null
);
create index action_limits on forum_private.actions(account_id, created);
create table forum_private.moderation (
  id bigint generated always as identity primary key,
  action text not null,
  target uuid not null,
  reason text not null,
  created timestamptz not null default clock_timestamp(),
  executed_by text not null default session_user
);

-- No direct data access, even if a later deployment accidentally grants table privileges.
alter table forum_private.profiles enable row level security;
alter table forum_private.categories enable row level security;
alter table forum_private.threads enable row level security;
alter table forum_private.posts enable row level security;
alter table forum_private.likes enable row level security;
alter table forum_private.follows enable row level security;
alter table forum_private.reads enable row level security;
alter table forum_private.reports enable row level security;
alter table forum_private.restrictions enable row level security;
alter table forum_private.actions enable row level security;
alter table forum_private.moderation enable row level security;

create function forum_private.text_value(data jsonb, key text, label text, minimum integer, maximum integer)
returns text language plpgsql immutable set search_path = pg_catalog as $$
declare value text;
begin
  if jsonb_typeof(data -> key) is distinct from 'string' then
    raise exception using errcode = 'PT400', message = label || ' must be text.';
  end if;
  value := btrim(data ->> key, E' \t\r\n');
  if char_length(value) < minimum or char_length(value) > maximum then
    raise exception using errcode = 'PT400', message = label || ' must be between ' || minimum || ' and ' || maximum || ' characters.';
  end if;
  return value;
end;
$$;

create function forum_private.query_value(data jsonb, key text, fallback text)
returns text language plpgsql immutable set search_path = pg_catalog as $$
begin
  if not (data ? key) then return fallback; end if;
  if jsonb_typeof(data -> key) is distinct from 'string' and not (key = 'page' and jsonb_typeof(data -> key) = 'number') then
    raise exception using errcode = 'PT400', message = 'Invalid ' || key || ' filter.';
  end if;
  return data ->> key;
end;
$$;

-- Names that would let a member pose as the operator or the product. The operator can
-- still assign one to a specific account from the SQL editor.
create function forum_private.username_reserved(handle text)
returns boolean language sql immutable set search_path = pg_catalog as $$
  select regexp_replace(handle, '[_-]', '', 'g') in (
    'admin', 'administrator', 'root', 'system', 'sysadmin', 'mod', 'mods', 'moderator', 'moderators',
    'staff', 'team', 'official', 'support', 'help', 'helpdesk', 'security', 'abuse', 'postmaster',
    'webmaster', 'noreply', 'community', 'forum', 'magimatix', 'data', 'datadaemon', 'daemon', 'owner',
    'founder', 'everyone', 'here', 'null', 'undefined', 'anonymous', 'deleted', 'guest', 'user');
$$;

create function forum_private.signup_profile()
returns trigger language plpgsql security definer set search_path = pg_catalog as $$
declare handle text; display_name text;
begin
  if coalesce(new.raw_user_meta_data, '{}'::jsonb) ? 'username' then
    handle := lower(forum_private.text_value(new.raw_user_meta_data, 'username', 'Username', 3, 32));
    if handle !~ '^[a-z0-9_][a-z0-9_-]{2,31}$' then
      raise exception using errcode = 'PT400', message = 'Use 3 to 32 letters, numbers, underscores, or hyphens for your username.';
    end if;
    if forum_private.username_reserved(handle) then
      raise exception using errcode = 'PT409', message = 'That username is reserved. Choose another.';
    end if;
  else
    handle := 'user_' || substr(replace(new.id::text, '-', ''), 1, 27);
  end if;
  if coalesce(new.raw_user_meta_data, '{}'::jsonb) ? 'display_name' then
    display_name := forum_private.text_value(new.raw_user_meta_data, 'display_name', 'Display name', 1, 60);
  else
    display_name := handle;
  end if;
  if display_name ~ '[[:cntrl:]]' then
    raise exception using errcode = 'PT400', message = 'Display names must fit on one line.';
  end if;
  insert into forum_private.profiles(id, name, username) values (new.id, display_name, handle);
  return new;
exception when unique_violation then
  raise exception using errcode = 'PT409', message = 'That username is already taken. Choose another.';
end;
$$;

create trigger forum_profile_on_signup after insert on auth.users
for each row execute function forum_private.signup_profile();

create function forum_private.profile_json(member_id uuid)
returns jsonb language sql stable set search_path = pg_catalog as $$
  select jsonb_build_object('id', p.id, 'name', p.name, 'username', p.username)
  from forum_private.profiles p where p.id = member_id;
$$;

create function forum_private.thread_json(thread_id uuid, viewer uuid)
returns jsonb language sql stable set search_path = pg_catalog as $$
  select jsonb_build_object(
    'id', t.id, 'category', t.category, 'title', t.title, 'created', t.created, 'updated', t.updated,
    'author', forum_private.profile_json(t.author_id), 'locked', t.locked,
    'replies', (select count(*) from forum_private.posts p where p.thread_id = t.id and not p.is_topic and not p.deleted and not p.hidden),
    'following', exists(select 1 from forum_private.follows f where f.thread_id = t.id and f.account_id = viewer),
    'unread', viewer is not null and coalesce((select r.seen < t.updated from forum_private.reads r where r.thread_id = t.id and r.account_id = viewer), true)
  ) from forum_private.threads t where t.id = thread_id and not t.hidden;
$$;

create function forum_private.consume_rate(member_id uuid, action_kind text)
returns timestamptz language plpgsql set search_path = pg_catalog as $$
declare stamp timestamptz := clock_timestamp();
begin
  -- The RPC locks this member row before any write, including nonce lookups.
  -- Holding the lock through commit prevents simultaneous requests bypassing limits.
  perform 1 from forum_private.profiles p where p.id = member_id for update;
  delete from forum_private.actions a where a.account_id = member_id and a.created < stamp - interval '1 hour';
  if (select count(*) from forum_private.actions a where a.account_id = member_id and a.created > stamp - interval '5 minutes') >= 30 then
    raise exception using errcode = 'PT429', message = 'Please slow down. Try again in a few minutes.';
  end if;
  if action_kind in ('thread', 'report') and
    (select count(*) from forum_private.actions a where a.account_id = member_id and a.kind = action_kind
       and a.created > stamp - case when action_kind = 'thread' then interval '15 minutes' else interval '1 hour' end) >= 8 then
    raise exception using errcode = 'PT429', message = 'You have reached the posting limit. Please try again later.';
  end if;
  insert into forum_private.actions(account_id, kind, created) values (member_id, action_kind, stamp);
  return stamp;
end;
$$;

create function public.forum_request(route text, payload jsonb default '{}'::jsonb, query jsonb default '{}'::jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare
  viewer uuid := auth.uid();
  parts text[];
  tid uuid;
  pid uuid;
  topic forum_private.threads%rowtype;
  post forum_private.posts%rowtype;
  category_filter text;
  view_filter text;
  sort_filter text;
  search_text text;
  page_text text;
  page_number integer;
  total bigint;
  items jsonb;
  body_text text;
  title_text text;
  category_id text;
  nonce text;
  input_hash bytea;
  stamp timestamptz;
  action text;
  first_post boolean;
  flag boolean;
  reason_text text;
  display_name text;
  handle text;
begin
  if route is null or char_length(route) > 128 or jsonb_typeof(payload) is distinct from 'object'
     or jsonb_typeof(query) is distinct from 'object' or octet_length(payload::text) > 65536 or octet_length(query::text) > 2048 then
    raise exception using errcode = 'PT400', message = 'Invalid forum request.';
  end if;
  -- An anon database role never obtains member access, even with a supplied subject.
  if current_setting('role', true) = 'anon' then viewer := null; end if;
  if viewer is not null and not exists(select 1 from forum_private.profiles p where p.id = viewer) then
    raise exception using errcode = 'PT401', message = 'Your community profile is unavailable. Sign in again.';
  end if;
  parts := string_to_array(route, '/');

  -- Lets the sign-up form say "taken" before Auth creates the account. Usernames are
  -- public on every post, so availability reveals nothing that is not already visible.
  if route = 'username' then
    if payload <> '{}'::jsonb then raise exception using errcode = 'PT400', message = 'This route is read only.'; end if;
    handle := lower(btrim(forum_private.query_value(query, 'username', ''), E' \t\r\n'));
    if handle !~ '^[a-z0-9_][a-z0-9_-]{2,31}$' then
      return jsonb_build_object('username', left(handle, 32), 'valid', false, 'available', false);
    end if;
    return jsonb_build_object('username', handle, 'valid', true,
      'available', not forum_private.username_reserved(handle)
        and not exists(select 1 from forum_private.profiles p where p.username = handle));
  end if;

  if route in ('meta', 'session') then
    if payload <> '{}'::jsonb then raise exception using errcode = 'PT400', message = 'This route is read only.'; end if;
    if route = 'session' then
      return jsonb_build_object('account', forum_private.profile_json(viewer), 'mode', 'shared', 'identity_scope', 'hosted');
    end if;
    select jsonb_agg(jsonb_build_object('id', c.id, 'name', c.name, 'description', c.description,
      'threads', (select count(*) from forum_private.threads t where t.category = c.id and not t.hidden)) order by c.position)
      into items from forum_private.categories c;
    return jsonb_build_object('account', forum_private.profile_json(viewer), 'mode', 'shared', 'identity_scope', 'hosted',
      'restricted', exists(select 1 from forum_private.restrictions r where r.account_id = viewer), 'categories', items,
      'threads', (select count(*) from forum_private.threads t where not t.hidden),
      'posts', (select count(*) from forum_private.posts p join forum_private.threads t on t.id = p.thread_id where not p.hidden and not p.deleted and not t.hidden));
  end if;

  if (route = 'threads' and payload = '{}'::jsonb) or (cardinality(parts) = 2 and parts[1] = 'threads') then
    if payload <> '{}'::jsonb then raise exception using errcode = 'PT400', message = 'This route is read only.'; end if;
    page_text := forum_private.query_value(query, 'page', '1');
    if page_text !~ '^[1-9][0-9]{0,4}$' or page_text::integer > 10000 then
      raise exception using errcode = 'PT400', message = 'Page must be an integer from 1 to 10000.';
    end if;
    page_number := page_text::integer;
    if route = 'threads' then
      category_filter := forum_private.query_value(query, 'category', 'all');
      view_filter := forum_private.query_value(query, 'view', 'all');
      sort_filter := forum_private.query_value(query, 'sort', 'active');
      search_text := btrim(forum_private.query_value(query, 'q', ''), E' \t\r\n');
      if category_filter <> 'all' and not exists(select 1 from forum_private.categories c where c.id = category_filter) then
        raise exception using errcode = 'PT400', message = 'Choose a valid category.';
      end if;
      if view_filter not in ('all', 'following', 'mine') or sort_filter not in ('active', 'newest', 'unanswered') or char_length(search_text) > 120 then
        raise exception using errcode = 'PT400', message = 'Invalid discussion filters. Search is limited to 120 characters.';
      end if;
      if view_filter <> 'all' and viewer is null then
        raise exception using errcode = 'PT401', message = 'Sign in to see your discussions.';
      end if;
      with filtered as (
        select t.id, case when sort_filter = 'newest' then t.created else t.updated end as sort_time
        from forum_private.threads t where not t.hidden
          and (category_filter = 'all' or t.category = category_filter)
          and (view_filter <> 'mine' or t.author_id = viewer)
          and (view_filter <> 'following' or exists(select 1 from forum_private.follows f where f.thread_id = t.id and f.account_id = viewer))
          and (search_text = '' or strpos(lower(t.title), lower(search_text)) > 0 or exists(
            select 1 from forum_private.posts p where p.thread_id = t.id and not p.hidden and not p.deleted and strpos(lower(p.body), lower(search_text)) > 0))
          and (sort_filter <> 'unanswered' or not exists(select 1 from forum_private.posts p where p.thread_id = t.id and not p.is_topic and not p.hidden and not p.deleted))
      ), selected as (select * from filtered order by sort_time desc, id desc limit 25 offset (page_number - 1) * 25)
      select (select count(*) from filtered), coalesce(jsonb_agg(forum_private.thread_json(s.id, viewer) order by s.sort_time desc, s.id desc), '[]'::jsonb)
        into total, items from selected s;
      return jsonb_build_object('threads', items, 'total', total, 'page', page_number, 'page_size', 25);
    end if;
    if parts[2] !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' then
      raise exception using errcode = 'PT404', message = 'This discussion is unavailable.';
    end if;
    tid := parts[2]::uuid;
    -- Keep activity and the post page consistent while recording the read position.
    select * into topic from forum_private.threads t where t.id = tid and not t.hidden for share;
    if not found then raise exception using errcode = 'PT404', message = 'This discussion is unavailable.'; end if;
    select count(*) into total from forum_private.posts p where p.thread_id = tid and not p.hidden;
    select coalesce(jsonb_agg(jsonb_build_object('id', p.id, 'body', case when p.deleted then '' else p.body end,
      'author', forum_private.profile_json(p.author_id), 'created', p.created, 'edited', p.edited, 'deleted', p.deleted,
      'is_topic', p.is_topic, 'likes', (select count(*) from forum_private.likes l where l.post_id = p.id),
      'liked', exists(select 1 from forum_private.likes l where l.post_id = p.id and l.account_id = viewer),
      'own', coalesce(p.author_id = viewer, false)) order by p.is_topic desc, p.created, p.id), '[]'::jsonb)
      into items from (select * from forum_private.posts p where p.thread_id = tid and not p.hidden
        order by p.is_topic desc, p.created, p.id limit 30 offset (page_number - 1) * 30) p;
    if viewer is not null and page_number * 30 >= total and (page_number - 1) * 30 < greatest(total, 1) then
      insert into forum_private.reads(thread_id, account_id, seen) values (tid, viewer, topic.updated)
        on conflict (thread_id, account_id) do update set seen = greatest(forum_private.reads.seen, excluded.seen);
    end if;
    return jsonb_build_object('thread', forum_private.thread_json(tid, viewer), 'posts', items, 'total', total,
      'page', page_number, 'page_size', 30, 'account', forum_private.profile_json(viewer));
  end if;

  if route in ('profile', 'account/profile') then action := 'profile';
  elsif route = 'threads' then action := 'thread';
  elsif cardinality(parts) = 3 and parts[1] in ('threads', 'posts') and
      parts[2] ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' and
      ((parts[1] = 'threads' and parts[3] in ('reply', 'follow')) or (parts[1] = 'posts' and parts[3] in ('like', 'edit', 'delete', 'report'))) then
    action := parts[3];
    if parts[1] = 'threads' then tid := parts[2]::uuid; else pid := parts[2]::uuid; end if;
  else raise exception using errcode = 'PT404', message = 'Unknown forum endpoint.';
  end if;
  if viewer is null then raise exception using errcode = 'PT401', message = 'Sign in to join the conversation.'; end if;
  perform 1 from forum_private.profiles p where p.id = viewer for update;
  if exists(select 1 from forum_private.restrictions r where r.account_id = viewer) then
    raise exception using errcode = 'PT403', message = 'Posting is disabled for this account. Contact the community moderator.';
  end if;

  if action = 'profile' then
    select p.name, p.username into display_name, handle from forum_private.profiles p where p.id = viewer;
    if payload ? 'name' then display_name := forum_private.text_value(payload, 'name', 'Display name', 1, 60); end if;
    if payload ? 'username' then handle := lower(forum_private.text_value(payload, 'username', 'Username', 3, 32)); end if;
    if display_name ~ '[[:cntrl:]]' or handle !~ '^[a-z0-9_][a-z0-9_-]{2,31}$' then
      raise exception using errcode = 'PT400', message = 'Use a single line display name and a username with letters, numbers, underscores, or hyphens.';
    end if;
    if forum_private.username_reserved(handle) and handle is distinct from (select p.username from forum_private.profiles p where p.id = viewer) then
      raise exception using errcode = 'PT409', message = 'That username is reserved. Choose another.';
    end if;
    stamp := forum_private.consume_rate(viewer, action);
    begin
      update forum_private.profiles p set name = display_name, username = handle where p.id = viewer;
    exception when unique_violation then
      raise exception using errcode = 'PT409', message = 'That username is already taken. Choose another.';
    end;
    return jsonb_build_object('account', forum_private.profile_json(viewer));
  end if;

  if action in ('thread', 'reply') then
    first_post := action = 'thread';
    body_text := forum_private.text_value(payload, 'body', 'Post', 1, 12000);
    nonce := forum_private.text_value(payload, 'request_id', 'Request ID', 16, 80);
    if nonce !~ '^[A-Za-z0-9_-]{16,80}$' then
      raise exception using errcode = 'PT400', message = 'A valid request ID is required.';
    end if;
    if first_post then
      title_text := forum_private.text_value(payload, 'title', 'Title', 3, 140);
      category_id := forum_private.text_value(payload, 'category', 'Category', 1, 32);
      if not exists(select 1 from forum_private.categories c where c.id = category_id) then
        raise exception using errcode = 'PT400', message = 'Choose a valid category.';
      end if;
      input_hash := sha256(convert_to(jsonb_build_object('route', route, 'body', body_text, 'title', title_text, 'category', category_id)::text, 'UTF8'));
    else
      input_hash := sha256(convert_to(jsonb_build_object('route', route, 'body', body_text)::text, 'UTF8'));
    end if;
    select * into post from forum_private.posts p where p.author_id = viewer and p.request_id = nonce;
    if found then
      if post.request_hash <> input_hash or post.deleted or post.hidden or not exists(
        select 1 from forum_private.threads t where t.id = post.thread_id and not t.hidden) then
        raise exception using errcode = 'PT409', message = 'This request ID was already used. Start a new post.';
      end if;
      select count(*) into total from forum_private.posts p where p.thread_id = post.thread_id and not p.hidden
        and (not p.is_topic, p.created, p.id) <= (not post.is_topic, post.created, post.id);
      return jsonb_build_object('thread_id', post.thread_id, 'post_id', post.id, 'duplicate', true, 'page', (total - 1) / 30 + 1);
    end if;
    if not first_post then
      select * into topic from forum_private.threads t where t.id = tid and not t.hidden for update;
      if not found then raise exception using errcode = 'PT404', message = 'This discussion is unavailable.'; end if;
      if topic.locked then raise exception using errcode = 'PT409', message = 'This discussion is closed to new replies.'; end if;
    end if;
    stamp := forum_private.consume_rate(viewer, action);
    if first_post then
      insert into forum_private.threads(author_id, category, title, created, updated) values(viewer, category_id, title_text, stamp, stamp) returning id into tid;
    else
      stamp := greatest(stamp, topic.updated + interval '1 microsecond');
      update forum_private.threads t set updated = stamp where t.id = tid;
    end if;
    insert into forum_private.posts(thread_id, author_id, body, created, is_topic, request_id, request_hash)
      values(tid, viewer, body_text, stamp, first_post, nonce, input_hash) returning id into pid;
    insert into forum_private.follows(thread_id, account_id) values(tid, viewer) on conflict do nothing;
    select count(*) into total from forum_private.posts p where p.thread_id = tid and not p.hidden;
    return jsonb_build_object('thread_id', tid, 'post_id', pid, 'page', (total - 1) / 30 + 1);
  end if;

  if action = 'follow' then
    if jsonb_typeof(payload -> 'following') is distinct from 'boolean' then
      raise exception using errcode = 'PT400', message = 'Choose whether to follow this discussion.';
    end if;
    perform 1 from forum_private.threads t where t.id = tid and not t.hidden for update;
    if not found then raise exception using errcode = 'PT404', message = 'This discussion is unavailable.'; end if;
    flag := (payload ->> 'following')::boolean;
    stamp := forum_private.consume_rate(viewer, action);
    if flag then insert into forum_private.follows(thread_id, account_id) values(tid, viewer) on conflict do nothing;
    else delete from forum_private.follows f where f.thread_id = tid and f.account_id = viewer; end if;
    return jsonb_build_object('following', flag);
  end if;

  select p.thread_id into tid from forum_private.posts p where p.id = pid;
  select * into topic from forum_private.threads t where t.id = tid and not t.hidden for update;
  if not found then raise exception using errcode = 'PT404', message = 'This post is unavailable.'; end if;
  select * into post from forum_private.posts p where p.id = pid and not p.deleted and not p.hidden for update;
  if not found then raise exception using errcode = 'PT404', message = 'This post is unavailable.'; end if;
  if action in ('edit', 'delete') and post.author_id <> viewer then
    raise exception using errcode = 'PT403', message = 'You can only change your own posts.';
  end if;
  if action = 'edit' then
    body_text := forum_private.text_value(payload, 'body', 'Post', 1, 12000);
    if post.is_topic and payload ? 'title' then title_text := forum_private.text_value(payload, 'title', 'Title', 3, 140); end if;
  elsif action = 'like' then
    if jsonb_typeof(payload -> 'liked') is distinct from 'boolean' then
      raise exception using errcode = 'PT400', message = 'Choose whether to like this post.';
    end if;
    flag := (payload ->> 'liked')::boolean;
  elsif action = 'report' then
    reason_text := forum_private.text_value(payload, 'reason', 'Reason', 5, 1000);
    if post.author_id = viewer then raise exception using errcode = 'PT400', message = 'Use edit or delete for your own post.'; end if;
  end if;
  stamp := forum_private.consume_rate(viewer, action);
  if action = 'edit' then
    update forum_private.posts p set body = body_text, edited = stamp where p.id = pid;
    if title_text is not null then update forum_private.threads t set title = title_text where t.id = tid; end if;
  elsif action = 'delete' then
    update forum_private.posts p set body = '', deleted = true, edited = stamp where p.id = pid;
    delete from forum_private.likes l where l.post_id = pid;
    if post.is_topic then update forum_private.threads t set hidden = true where t.id = tid; end if;
  elsif action = 'like' then
    if flag then insert into forum_private.likes(post_id, account_id) values(pid, viewer) on conflict do nothing;
    else delete from forum_private.likes l where l.post_id = pid and l.account_id = viewer; end if;
  elsif action = 'report' then
    insert into forum_private.reports(post_id, account_id, reason, created) values(pid, viewer, reason_text, stamp)
      on conflict (post_id, account_id) do update set reason = excluded.reason, created = excluded.created, resolved = false;
  end if;
  return jsonb_build_object('ok', true, 'thread_id', tid);
end;
$$;

-- Operator-only moderation. Run through the Supabase SQL editor as the database owner.
-- There are deliberately no client moderator roles or public moderation endpoints.
create function forum_private.moderate(action text, target uuid, reason text)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare tid uuid; post forum_private.posts%rowtype; checked_reason text;
begin
  checked_reason := forum_private.text_value(jsonb_build_object('reason', reason), 'reason', 'Moderation reason', 5, 1000);
  if action in ('lock', 'unlock', 'hide-thread', 'restore-thread') then
    perform 1 from forum_private.threads t where t.id = target for update;
    if not found then raise exception using errcode = 'PT404', message = 'Discussion not found.'; end if;
    if action = 'restore-thread' and exists(select 1 from forum_private.posts p where p.thread_id = target and p.is_topic and p.deleted) then
      raise exception using errcode = 'PT409', message = 'An author-deleted discussion cannot be restored.';
    end if;
    if action in ('lock', 'unlock') then update forum_private.threads t set locked = action = 'lock' where t.id = target;
    else update forum_private.threads t set hidden = action = 'hide-thread' where t.id = target; end if;
  elsif action in ('hide-post', 'restore-post') then
    select p.thread_id into tid from forum_private.posts p where p.id = target;
    perform 1 from forum_private.threads t where t.id = tid for update;
    select * into post from forum_private.posts p where p.id = target for update;
    if not found then raise exception using errcode = 'PT404', message = 'Post not found.'; end if;
    if post.is_topic then raise exception using errcode = 'PT400', message = 'Moderate the discussion itself for an opening post.'; end if;
    if action = 'restore-post' and post.deleted then raise exception using errcode = 'PT409', message = 'An author-deleted post cannot be restored.'; end if;
    update forum_private.posts p set hidden = action = 'hide-post' where p.id = target;
  elsif action in ('suspend', 'unsuspend') then
    perform 1 from forum_private.profiles p where p.id = target for update;
    if not found then raise exception using errcode = 'PT404', message = 'Account not found.'; end if;
    if action = 'suspend' then
      insert into forum_private.restrictions(account_id, reason) values(target, checked_reason)
        on conflict (account_id) do update set reason = excluded.reason;
    else delete from forum_private.restrictions r where r.account_id = target; end if;
  elsif action = 'resolve-report' then
    update forum_private.reports r set resolved = true where r.id = target;
    if not found then raise exception using errcode = 'PT404', message = 'Report not found.'; end if;
  else raise exception using errcode = 'PT400', message = 'Unknown moderation action.';
  end if;
  insert into forum_private.moderation(action, target, reason) values(action, target, checked_reason);
  return jsonb_build_object('ok', true);
end;
$$;

revoke all on all tables in schema forum_private from public, anon, authenticated;
revoke all on all sequences in schema forum_private from public, anon, authenticated;
revoke all on all functions in schema forum_private from public, anon, authenticated;
alter default privileges in schema forum_private revoke all on tables from public, anon, authenticated;
alter default privileges in schema forum_private revoke all on sequences from public, anon, authenticated;
alter default privileges in schema forum_private revoke execute on functions from public, anon, authenticated;
revoke all on function public.forum_request(text, jsonb, jsonb) from public, anon, authenticated;
grant execute on function public.forum_request(text, jsonb, jsonb) to anon, authenticated;

commit;
