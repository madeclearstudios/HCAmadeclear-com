-- Clips made of several kept stretches, name straps per speaker, editable
-- end card, a renderer status row, and a shared list of speakers.

-- cuts:   [{"in": s, "out": s, "first": sentence#, "last": sentence#, "trimIn": words, "trimOut": words}, ...]
--         in film order; in_s/out_s stay as the first in and the last out.
-- straps: [{"name": "...", "inst": "...", "at": s, "dur": s}, ...] in clip time.
alter table public.clip_versions
  add column cuts jsonb,
  add column straps jsonb,
  add column end_head text check (char_length(end_head) <= 40),
  add column end_sub text check (char_length(end_sub) <= 70);

-- The render worker writes here every few seconds (service role only), so the
-- studio can say plainly whether anything is rendering.
create table public.worker_status (
  id         text primary key,
  state      text not null,           -- idle | rendering | waiting
  detail     text,
  last_seen  timestamptz not null default now()
);
alter table public.worker_status enable row level security;
create policy "team reads worker status" on public.worker_status for select to authenticated
  using ((select public.my_role()) is not null);

-- Speakers and their institutions, typed once and reused for name straps.
create table public.speakers (
  name        text primary key check (char_length(name) between 2 and 60),
  institution text check (char_length(institution) <= 80),
  updated_by  uuid default auth.uid() references public.profiles (id),
  updated_at  timestamptz not null default now()
);
create index on public.speakers (updated_by);
alter table public.speakers enable row level security;
create policy "team reads speakers" on public.speakers for select to authenticated
  using ((select public.my_role()) is not null);
create policy "marketing adds speakers" on public.speakers for insert to authenticated
  with check ((select public.my_role()) in ('marketer', 'admin', 'medaffairs'));
create policy "marketing edits speakers" on public.speakers for update to authenticated
  using ((select public.my_role()) in ('marketer', 'admin', 'medaffairs'))
  with check ((select public.my_role()) in ('marketer', 'admin', 'medaffairs'));
