-- Trigger functions are never called directly.
revoke execute on function public.handle_new_user(), public.apply_invite(), public.apply_review()
  from public, anon, authenticated;
-- my_role() is used inside policies and returns only the caller's own role.
revoke execute on function public.my_role() from public, anon;
grant execute on function public.my_role() to authenticated;
-- invites is deliberately client-invisible: managed from the dashboard / service role only.
comment on table public.invites is 'Who gets which role. No client policies on purpose: managed with the service role.';
