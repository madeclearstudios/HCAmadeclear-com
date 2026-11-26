-- When the render worker took the job, so a crashed render can be put back.
alter table public.clip_versions add column claimed_at timestamptz;
