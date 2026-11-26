-- Watermarked previews: the renderer also makes a half-size copy of each clip,
-- stamped "For review – not approved", so the marketing team can watch what
-- they made before Medical Affairs approves it. The clean file stays locked
-- (see "clip files by role" in 0001) until that exact version is approved.

alter table public.clip_versions add column if not exists preview_path text;

-- Only the renderer (secret key) sets preview_path: a marketer pointing it at
-- a clean file would otherwise unlock that file early.
alter policy "marketers queue versions" on public.clip_versions
  with check ((select public.my_role()) in ('marketer', 'admin') and created_by = (select auth.uid())
    and status = 'queued' and storage_path is null and sha256 is null and preview_path is null);

drop policy if exists "team watches previews" on storage.objects;
create policy "team watches previews" on storage.objects for select to authenticated
  using (bucket_id = 'clips'
    and (select public.my_role()) in ('marketer', 'medaffairs', 'admin')
    and name like '%-preview.mp4'
    and exists (select 1 from public.clip_versions v where v.preview_path = name));
