-- Marketers can rename a clip (Medical Affairs sometimes asks for a new
-- title), and nothing else: the title is the only column clients may update,
-- and only marketing and admin can do it. The rename doesn't touch any
-- version, review or file, so approvals stay tied to the exact file approved.
revoke update on public.clips from anon, authenticated;
grant update (title) on public.clips to authenticated;

create policy "marketing renames clips" on public.clips for update to authenticated
  using ((select public.my_role()) in ('marketer', 'admin'))
  with check ((select public.my_role()) in ('marketer', 'admin') and char_length(trim(title)) between 1 and 120);
