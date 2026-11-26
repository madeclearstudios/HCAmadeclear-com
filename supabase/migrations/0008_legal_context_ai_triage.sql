-- Legal context and AI triage for Medical Affairs.
--
-- legal_docs     the reference library the AI triage may use, and nothing else:
--                the starter set plus anything Medical Affairs uploads
-- legal_pages    each document's text, page by page (for page citations)
-- legal_passages short overlapping passages with an embedding, searched per clip
-- ai_triage      each AI triage run: what it used and what it suggested
--
-- Medical Affairs (and Made Clear admin) read and add documents; the indexing
-- and triage functions write passages and results with the service role.
create extension if not exists vector with schema extensions;

create table public.legal_docs (
  id           uuid primary key default gen_random_uuid(),
  title        text not null check (char_length(title) between 2 and 200),
  issuer       text,
  doc_date     text,
  kind         text not null default 'upload' check (kind in ('starter', 'upload')),
  source_url   text,
  note         text,
  storage_path text unique,
  pages        int,
  status       text not null default 'uploaded' check (status in ('uploaded', 'indexing', 'indexed', 'failed')),
  error        text,
  active       boolean not null default true,
  added_by     uuid default auth.uid() references public.profiles (id),
  created_at   timestamptz not null default now()
);

create table public.legal_pages (
  doc_id  uuid not null references public.legal_docs (id) on delete cascade,
  page    int  not null check (page > 0),
  text    text not null,
  primary key (doc_id, page)
);

create table public.legal_passages (
  id         bigint generated always as identity primary key,
  doc_id     uuid not null references public.legal_docs (id) on delete cascade,
  page_from  int not null,
  page_to    int not null,
  text       text not null,
  embedding  extensions.vector(384) not null
);
create index on public.legal_passages (doc_id);
create index on public.legal_passages using hnsw (embedding extensions.vector_cosine_ops);

create table public.ai_triage (
  id          uuid primary key default gen_random_uuid(),
  version_id  uuid not null references public.clip_versions (id) on delete cascade,
  status      text not null default 'running' check (status in ('running', 'done', 'failed')),
  verdict     text check (verdict in ('approve', 'revise', 'block')),
  result      jsonb,
  docs_used   jsonb,
  model       text,
  error       text,
  created_by  uuid not null default auth.uid() references public.profiles (id),
  created_at  timestamptz not null default now(),
  finished_at timestamptz
);
create index on public.ai_triage (version_id);
create index on public.ai_triage (created_by);
create index on public.legal_docs (added_by);

alter table public.legal_docs     enable row level security;
alter table public.legal_pages    enable row level security;
alter table public.legal_passages enable row level security;
alter table public.ai_triage      enable row level security;

create policy "review team reads legal docs" on public.legal_docs for select to authenticated
  using ((select public.my_role()) in ('medaffairs', 'admin'));
create policy "review team adds legal docs" on public.legal_docs for insert to authenticated
  with check ((select public.my_role()) in ('medaffairs', 'admin') and kind = 'upload' and added_by = (select auth.uid()) and status = 'uploaded');

create policy "review team reads legal pages" on public.legal_pages for select to authenticated
  using ((select public.my_role()) in ('medaffairs', 'admin'));
create policy "review team adds pages to its uploads" on public.legal_pages for insert to authenticated
  with check ((select public.my_role()) in ('medaffairs', 'admin')
    and exists (select 1 from public.legal_docs d where d.id = doc_id and d.kind = 'upload' and d.added_by = (select auth.uid()) and d.status = 'uploaded'));

-- passages: no client access (the functions use the service role)

create policy "review team reads triage" on public.ai_triage for select to authenticated
  using ((select public.my_role()) in ('medaffairs', 'admin'));
create policy "review team starts triage" on public.ai_triage for insert to authenticated
  with check ((select public.my_role()) in ('medaffairs', 'admin') and created_by = (select auth.uid()) and status = 'running'
              and verdict is null and result is null);

-- Nearest passages to a query embedding, across active documents only.
create function public.match_legal_passages(query extensions.vector(384), match_count int default 12)
returns table (id bigint, doc_id uuid, page_from int, page_to int, text text, similarity float)
language sql stable security definer set search_path = '' as $$
  select p.id, p.doc_id, p.page_from, p.page_to, p.text, 1 - (p.embedding operator(extensions.<=>) query) as similarity
  from public.legal_passages p join public.legal_docs d on d.id = p.doc_id
  where d.active and d.status = 'indexed'
  order by p.embedding operator(extensions.<=>) query
  limit least(match_count, 50)
$$;
revoke execute on function public.match_legal_passages(extensions.vector, int) from public, anon, authenticated;

-- Private bucket for the documents themselves.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('legal', 'legal', false, 52428800, array['application/pdf', 'text/plain']);
create policy "review team reads legal files" on storage.objects for select to authenticated
  using (bucket_id = 'legal' and (select public.my_role()) in ('medaffairs', 'admin'));
create policy "review team uploads legal files" on storage.objects for insert to authenticated
  with check (bucket_id = 'legal' and (select public.my_role()) in ('medaffairs', 'admin') and name like 'uploads/%');
