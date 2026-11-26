-- The one shared team login (netlify/functions/team-login.mjs). Create the
-- user itself in the dashboard (Authentication > Users > Add user, email
-- team@hcamadeclear.com, auto-confirm) and put its password in Netlify as
-- DEMO_PASSWORD. This invite makes it 'admin' the moment it exists.
insert into public.invites (email, name, role) values ('team@hcamadeclear.com', 'HCA team', 'admin')
  on conflict (email) do update set role = 'admin', name = excluded.name;
