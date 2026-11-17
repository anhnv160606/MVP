create table public.phien_ban_phuong_an_bo_tri (
    id uuid primary key default gen_random_uuid(),
    project_id uuid not null references public.projects(id),
    plan_id uuid not null,
    version bigint not null check (version > 0),
    source_file_id uuid not null,
    output_file_id uuid,
    proposals jsonb not null check (jsonb_typeof(proposals) = 'array'),
    validation_result jsonb not null check (jsonb_typeof(validation_result) = 'object'),
    diff_summary jsonb not null,
    removal_details jsonb,
    review_status text not null default 'pending_review'
        check (review_status in ('pending_review', 'approved', 'rejected')),
    reviewed_by uuid,
    reviewed_at timestamptz,
    applied_at timestamptz,
    created_at timestamptz not null default now(),
    unique (project_id, plan_id, version),
    foreign key (project_id, source_file_id) references public.project_files(project_id, id),
    foreign key (project_id, output_file_id) references public.project_files(project_id, id),
    check ((output_file_id is null) = (applied_at is null)),
    check (review_status <> 'approved' or
        (reviewed_by is not null and reviewed_at is not null and
         coalesce(validation_result -> 'valid' = 'true'::jsonb, false) and
         coalesce(validation_result -> 'errors' = '[]'::jsonb, false))),
    check (output_file_id is null or review_status = 'approved')
);

create index placement_applied on public.phien_ban_phuong_an_bo_tri(project_id, applied_at desc)
where applied_at is not null;

-- Serialize commits across backend processes. Uploads can be orphaned after a
-- failed commit, but no losing writer can replace the current IFC pointer.
create function public.guard_placement_revision() returns trigger
language plpgsql set search_path = '' as $$
declare
    current_file uuid;
    latest_version bigint;
begin
    perform 1 from public.projects where id = new.project_id for update;
    select max(version) into latest_version from public.phien_ban_phuong_an_bo_tri
      where project_id = new.project_id and plan_id = new.plan_id;
    if tg_op = 'INSERT' then
        if new.version <> coalesce(latest_version, 0) + 1 or
           new.review_status <> 'pending_review' or new.output_file_id is not null then
            raise exception 'Invalid draft revision' using errcode = '23505';
        end if;
    else
        if new.project_id <> old.project_id or new.plan_id <> old.plan_id or
           new.version <> old.version or new.source_file_id <> old.source_file_id or
           new.proposals <> old.proposals or new.diff_summary <> old.diff_summary then
            raise exception 'Placement revisions are immutable' using errcode = '23514';
        end if;
        if old.output_file_id is not null or new.version <> latest_version then
            raise exception 'Stale or already applied revision' using errcode = '23505';
        end if;
        if old.review_status = 'approved' and
           (new.review_status <> old.review_status or new.reviewed_by <> old.reviewed_by or
            new.validation_result <> old.validation_result) then
            raise exception 'Approved revision is immutable' using errcode = '23514';
        end if;
    end if;
    select output_file_id into current_file from public.phien_ban_phuong_an_bo_tri
      where project_id = new.project_id and applied_at is not null
      order by applied_at desc, id desc limit 1;
    if current_file is not null and new.source_file_id <> current_file then
        raise exception 'Current IFC changed; revalidate draft' using errcode = '23505';
    end if;
    if not exists (select 1 from public.project_files where id = new.source_file_id
        and project_id = new.project_id and status = 'ready') then
        raise exception 'Source IFC is not ready' using errcode = '23514';
    end if;
    if new.output_file_id is not null and not exists (
        select 1 from public.project_files where id = new.output_file_id
        and project_id = new.project_id and status = 'ready' and file_kind = 'electrical'
    ) then
        raise exception 'Output IFC is not ready' using errcode = '23514';
    end if;
    return new;
end;
$$;

create trigger guard_placement_revision before insert or update
on public.phien_ban_phuong_an_bo_tri for each row execute function public.guard_placement_revision();

alter table public.phien_ban_phuong_an_bo_tri enable row level security;
revoke all on public.phien_ban_phuong_an_bo_tri from public, anon, authenticated;
grant select, insert, update on public.phien_ban_phuong_an_bo_tri to service_role;
