-- HCA Made Clear: social clipping tool, accounts and approvals. (Ported from RDN / Shockwave Made Clear.)
--
-- Who can do what (enforced here, not just in the pages):
--   marketer    makes clips and new versions; downloads a file only once it is approved
--   medaffairs  watches any rendered clip; approves or requests changes
--   admin       both, plus whatever the dashboard needs later
-- Nobody gets a role by signing up. A role comes from the invites table, so
-- an unknown email can sign in but sees nothing.
--
-- The Mac render worker uses the service role key: it claims queued versions,
-- uploads the file to the "clips" bucket and fills in path, hash and status.
-- Clients can never edit a version or a review once written, which is the
-- approval trail.

-- ------------------------------------------------------------------ people
create table public.invites (
  email      text primary key check (email = lower(email)),
  name       text,
  role       text not null check (role in ('marketer', 'medaffairs', 'admin')),
  created_at timestamptz not null default now()
);

create table public.profiles (
  id         uuid primary key references auth.users (id) on delete cascade,
  email      text not null,
  name       text,
  role       text check (role in ('marketer', 'medaffairs', 'admin')),
  created_at timestamptz not null default now()
);

-- New sign-in: make a profile, with the role from invites if there is one.
create function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
  insert into public.profiles (id, email, name, role)
  select new.id, lower(new.email), i.name, i.role
  from (select 1) x left join public.invites i on i.email = lower(new.email);
  return new;
end $$;

create trigger on_auth_user_created after insert on auth.users
  for each row execute function public.handle_new_user();

-- An invite added after someone has already signed in still takes effect.
create function public.apply_invite() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
  update public.profiles set role = new.role, name = coalesce(new.name, name)
  where email = new.email;
  return new;
end $$;

create trigger on_invite_saved after insert or update on public.invites
  for each row execute function public.apply_invite();

create function public.my_role() returns text
language sql stable security definer set search_path = '' as $$
  select role from public.profiles where id = (select auth.uid())
$$;

-- ------------------------------------------------------------------ clips
create table public.clips (
  id         uuid primary key default gen_random_uuid(),
  film_id    text not null,
  film_title text,
  title      text not null,
  created_by uuid not null default auth.uid() references public.profiles (id),
  created_at timestamptz not null default now()
);

create table public.clip_versions (
  id           uuid primary key default gen_random_uuid(),
  clip_id      uuid not null references public.clips (id) on delete cascade,
  version      int  not null check (version > 0),
  in_s         numeric(9,2) not null,
  out_s        numeric(9,2) not null check (out_s > in_s),
  format       text not null check (format in ('9:16', '1:1', '16:9')),
  speaker      text,
  role_line    text,
  note         text,                         -- what the marketer changed, for v2+
  status       text not null default 'queued'
               check (status in ('queued', 'rendering', 'awaiting_review', 'changes_requested', 'approved', 'failed')),
  -- filled in by the render worker
  words        text,
  captions     jsonb,
  storage_path text unique,
  sha256       text,
  bytes        bigint,
  duration     numeric(7,2),
  error        text,
  rendered_at  timestamptz,
  created_by   uuid not null default auth.uid() references public.profiles (id),
  created_at   timestamptz not null default now(),
  unique (clip_id, version)
);

create table public.reviews (
  id         uuid primary key default gen_random_uuid(),
  version_id uuid not null references public.clip_versions (id) on delete cascade,
  reviewer   uuid not null default auth.uid() references public.profiles (id),
  decision   text not null check (decision in ('approved', 'changes_requested')),
  note       text,
  job_code   text,
  sha256     text,                           -- the exact file approved, copied from the version
  created_at timestamptz not null default now(),
  check (decision = 'approved' or length(trim(coalesce(note, ''))) > 0)
);

create index on public.clip_versions (clip_id);
create index on public.clip_versions (status);
create index on public.reviews (version_id);
create index on public.clips (created_by);
create index on public.clip_versions (created_by);
create index on public.reviews (reviewer);

-- A review only counts against a rendered file awaiting review, and it moves
-- the version on. The file's hash is stamped on the review.
create function public.apply_review() returns trigger
language plpgsql security definer set search_path = '' as $$
declare v public.clip_versions;
begin
  select * into v from public.clip_versions where id = new.version_id for update;
  if v.status <> 'awaiting_review' then
    raise exception 'This version is %; only versions awaiting review can be reviewed', v.status;
  end if;
  new.sha256 := v.sha256;
  update public.clip_versions set status = new.decision where id = new.version_id;
  return new;
end $$;

create trigger before_review before insert on public.reviews
  for each row execute function public.apply_review();

-- ------------------------------------------------------------------ security
alter table public.invites       enable row level security;
alter table public.profiles      enable row level security;
alter table public.clips         enable row level security;
alter table public.clip_versions enable row level security;
alter table public.reviews       enable row level security;

-- invites: no client access at all (managed in the dashboard or by admin tools).

create policy "team reads profiles" on public.profiles for select to authenticated
  using ((select public.my_role()) is not null or id = (select auth.uid()));

create policy "team reads clips" on public.clips for select to authenticated
  using ((select public.my_role()) is not null);
create policy "marketers make clips" on public.clips for insert to authenticated
  with check ((select public.my_role()) in ('marketer', 'admin') and created_by = (select auth.uid()));

create policy "team reads versions" on public.clip_versions for select to authenticated
  using ((select public.my_role()) is not null);
create policy "marketers queue versions" on public.clip_versions for insert to authenticated
  with check ((select public.my_role()) in ('marketer', 'admin')
              and created_by = (select auth.uid())
              and status = 'queued' and storage_path is null and sha256 is null);

create policy "team reads reviews" on public.reviews for select to authenticated
  using ((select public.my_role()) is not null);
create policy "med affairs reviews" on public.reviews for insert to authenticated
  with check ((select public.my_role()) in ('medaffairs', 'admin') and reviewer = (select auth.uid()));

-- ------------------------------------------------------------------ files
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('clips', 'clips', false, 104857600, array['video/mp4', 'application/json']);

-- Med Affairs can watch anything rendered; marketers get a file only once
-- that exact version is approved. Uploads come from the worker (service role).
create policy "clip files by role" on storage.objects for select to authenticated
  using (bucket_id = 'clips' and (
    (select public.my_role()) in ('medaffairs', 'admin')
    or exists (select 1 from public.clip_versions v where v.storage_path = name and v.status = 'approved')
  ));
