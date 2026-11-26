-- When a clip version is queued, call the cloud renderer's Worker straight
-- away (cloud/src/index.js, POST /run) so the render starts within seconds.
-- The Worker's once-a-minute check is the backstop if this call is lost.
--
-- The Worker address and the shared secret live in Supabase Vault:
--   select vault.create_secret('https://hca-clip-renderer.<sub>.workers.dev', 'renderer_url');
--   select vault.create_secret('<same value as the TRIGGER_SECRET GitHub secret>', 'renderer_trigger_secret');
-- Until both exist this trigger does nothing.
create extension if not exists pg_net with schema extensions;

create function public.wake_renderer() returns trigger
language plpgsql security definer set search_path = '' as $$
declare u text; s text;
begin
  select decrypted_secret into u from vault.decrypted_secrets where name = 'renderer_url';
  select decrypted_secret into s from vault.decrypted_secrets where name = 'renderer_trigger_secret';
  if u is null or s is null then return new; end if;
  perform net.http_post(
    url := rtrim(u, '/') || '/run',
    headers := jsonb_build_object('Content-Type', 'application/json', 'x-trigger-secret', s),
    body := jsonb_build_object('version', new.id),
    timeout_milliseconds := 5000);
  return new;
end $$;
revoke execute on function public.wake_renderer() from public, anon, authenticated;

create trigger on_version_queued after insert on public.clip_versions
  for each row when (new.status = 'queued') execute function public.wake_renderer();
