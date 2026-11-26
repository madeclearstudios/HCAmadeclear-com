-- Fine-edit requests: when a clip needs more than the studio can do, the
-- marketer routes it to Made Clear with a note. The request carries a
-- Premiere Pro XML (xmeml) of that version, cut from the master files, which
-- Made Clear downloads from /fine-edits. No email; Made Clear (admin) works
-- from the list and marks each one done.
create table public.fine_edits (
  id           uuid primary key default gen_random_uuid(),
  clip_id      uuid not null references public.clips (id) on delete cascade,
  version_id   uuid not null references public.clip_versions (id) on delete cascade,
  note         text not null check (char_length(trim(note)) between 1 and 1000),
  xml          text not null,
  status       text not null default 'open' check (status in ('open', 'done')),
  done_note    text check (char_length(done_note) <= 1000),
  done_at      timestamptz,
  requested_by uuid not null default auth.uid() references public.profiles (id),
  created_at   timestamptz not null default now()
);
create index on public.fine_edits (clip_id);
create index on public.fine_edits (version_id);
create index on public.fine_edits (requested_by);
alter table public.fine_edits enable row level security;

-- Marketing and Made Clear see requests; Medical Affairs doesn't need to.
create policy "marketing and made clear read fine edits" on public.fine_edits for select to authenticated
  using ((select public.my_role()) in ('marketer', 'admin'));
create policy "marketing routes fine edits" on public.fine_edits for insert to authenticated
  with check ((select public.my_role()) in ('marketer', 'admin') and requested_by = (select auth.uid()) and status = 'open');
-- Only Made Clear closes a request, and only these columns.
revoke update on public.fine_edits from anon, authenticated;
grant update (status, done_note, done_at) on public.fine_edits to authenticated;
create policy "made clear closes fine edits" on public.fine_edits for update to authenticated
  using ((select public.my_role()) = 'admin')
  with check ((select public.my_role()) = 'admin');

-- Mike works the fine-edit list as Made Clear (admin can also review and make clips).
insert into public.invites (email, name, role) values ('mike@madeclearstudios.com', 'Mike Clear (Made Clear)', 'admin')
  on conflict (email) do update set role = 'admin', name = excluded.name;
