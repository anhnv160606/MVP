-- Backend service-role access only. API authentication is handled by the workspace server.
create table if not exists public.projects (
    id uuid primary key default gen_random_uuid(),
    name text not null check (length(trim(name)) > 0),
    phase text not null default 'IFC_LOADED',
    created_at timestamptz not null default now()
);

create table if not exists public.project_files (
    id uuid primary key default gen_random_uuid(),
    project_id uuid not null references public.projects(id),
    file_kind text not null check (file_kind in ('architecture', 'electrical')),
    original_name text not null,
    bucket text not null default 'ifc-files',
    object_path text not null unique,
    size_bytes bigint not null check (size_bytes > 0),
    sha256 text not null check (sha256 ~ '^[0-9a-f]{64}$'),
    ifc_schema text not null,
    status text not null default 'pending' check (status in ('pending', 'ready', 'failed')),
    created_at timestamptz not null default now(),
    unique (project_id, id)
);

alter table public.projects enable row level security;
alter table public.project_files enable row level security;
revoke all on public.projects, public.project_files from public, anon, authenticated;
grant select, insert, update on public.projects, public.project_files to service_role;

insert into storage.buckets (id, name, public)
values ('ifc-files', 'ifc-files', false) on conflict (id) do nothing;
