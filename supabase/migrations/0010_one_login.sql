-- HCA Made Clear has one login for the whole client team (Mike, 26 Nov 2026).
-- The role machinery from RDN/Shockwave stays, so the pages work unchanged,
-- but every invited person is 'admin': they can search, clip, approve and
-- download. The approval step still stands: a clean file unlocks only once
-- someone on the team has approved that exact version.
alter table public.invites alter column role set default 'admin';
