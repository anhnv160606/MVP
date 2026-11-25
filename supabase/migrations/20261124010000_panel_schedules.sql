-- Dev C. Apply after project/questionnaire/placement migrations.
-- Only backend service_role RPCs may write; agent never receives this credential.
create table public.panel_design_contexts (
    project_id uuid not null references public.projects(id),
    design_context_id text not null,
    snapshot jsonb not null check (jsonb_typeof(snapshot) = 'object'),
    is_current boolean not null default true,
    created_at timestamptz not null default now(),
    primary key (project_id, design_context_id)
);
create unique index panel_one_current_context on public.panel_design_contexts(project_id)
where is_current;

create table public.phien_ban_phuong_an_tu_dien (
    id uuid primary key default gen_random_uuid(),
    project_id uuid not null references public.projects(id),
    plan_id uuid not null,
    version bigint not null check (version > 0),
    design_context_id text not null,
    request_id text not null check (length(request_id) between 1 and 128),
    request_hash text not null check (request_hash ~ '^[0-9a-f]{64}$'),
    schedule jsonb not null check (jsonb_typeof(schedule) = 'object'),
    schedule_hash text not null check (schedule_hash ~ '^[0-9a-f]{64}$'),
    source_refs jsonb not null check (jsonb_typeof(source_refs) = 'object'),
    calculation_summary jsonb,
    validation_result jsonb not null check (jsonb_typeof(validation_result) = 'object'),
    design_reasons jsonb not null check (jsonb_typeof(design_reasons) = 'array'),
    diff_summary jsonb not null check (jsonb_typeof(diff_summary) = 'object'),
    review_status text not null default 'pending_review' check (review_status = 'pending_review'),
    sld_status text not null default 'pending' check (sld_status in ('pending', 'ready', 'failed')),
    sld_artifact jsonb,
    created_at timestamptz not null default now(),
    unique (project_id, plan_id, version),
    unique (project_id, version), -- MVP: one plan lineage per project
    unique (project_id, request_id),
    foreign key (project_id, design_context_id)
        references public.panel_design_contexts(project_id, design_context_id),
    check (validation_result ->> 'validated_schedule_hash' = schedule_hash),
    check ((sld_status = 'ready') = (sld_artifact is not null))
);

alter table public.panel_design_contexts enable row level security;
alter table public.phien_ban_phuong_an_tu_dien enable row level security;
revoke all on public.panel_design_contexts, public.phien_ban_phuong_an_tu_dien
from public, anon, authenticated, service_role;
grant select on public.panel_design_contexts, public.phien_ban_phuong_an_tu_dien to service_role;

-- Same project row lock as placement writes; same advisory lock as questionnaire.
-- Call only inside a transaction that holds the project row lock.
create function public.panel_assert_source(p_project_id uuid, p_refs jsonb)
returns void language plpgsql security definer set search_path = '' as $$
declare
    placement record;
    questionnaire_version bigint;
begin
    perform pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended('phase1.questionnaire:' || p_project_id::text, 0));
    select * into placement from public.phien_ban_phuong_an_bo_tri
        where project_id = p_project_id and applied_at is not null
        order by applied_at desc, id desc limit 1;
    if not found or placement.review_status <> 'approved' or
       placement.plan_id::text is distinct from p_refs ->> 'placement_plan_id' or
       placement.version is distinct from (p_refs ->> 'placement_version')::bigint or
       placement.output_file_id::text is distinct from p_refs ->> 'source_file_id' then
        raise exception 'PANEL_STALE_SOURCE: approved and applied placement required' using errcode = '40001';
    end if;
    if not exists (select 1 from public.project_files where project_id = p_project_id
        and id = placement.output_file_id and file_kind = 'electrical' and status = 'ready'
        and sha256 = p_refs ->> 'model_revision') then
        raise exception 'PANEL_STALE_SOURCE: IFC revision is not ready/current' using errcode = '40001';
    end if;
    select max(revision) into questionnaire_version from public.phase1_questionnaire_revisions
        where project_id = p_project_id::text;
    if questionnaire_version is null or questionnaire_version is distinct from
        (p_refs ->> 'questionnaire_revision')::bigint then
        raise exception 'PANEL_STALE_SOURCE: questionnaire changed' using errcode = '40001';
    end if;
end;
$$;

create function public.panel_register_context(p_project_id uuid, p_context jsonb)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare
    existing public.panel_design_contexts;
    context_id text := p_context ->> 'design_context_id';
begin
    perform 1 from public.projects where id = p_project_id for update;
    if not found or p_context ->> 'project_id' is distinct from p_project_id::text or
       context_id is null or length(context_id) not between 1 and 128 or
       coalesce(length(p_context #>> '{source_refs,playbook_revision}'), 0) = 0 or
       coalesce(length(p_context #>> '{source_refs,ruleset_revision}'), 0) = 0 then
        raise exception 'Invalid backend panel context' using errcode = '23514';
    end if;
    perform public.panel_assert_source(p_project_id, p_context -> 'source_refs');
    select * into existing from public.panel_design_contexts
        where project_id = p_project_id and design_context_id = context_id;
    if found then
        if existing.snapshot <> p_context or not existing.is_current then
            return jsonb_build_object('conflict', 'Context ID is immutable or superseded');
        end if;
        return existing.snapshot;
    end if;
    update public.panel_design_contexts set is_current = false where project_id = p_project_id;
    insert into public.panel_design_contexts(project_id, design_context_id, snapshot)
        values (p_project_id, context_id, p_context);
    return p_context;
end;
$$;

create function public.panel_load_context(p_project_id uuid, p_context_id text)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare data jsonb;
begin
    perform 1 from public.projects where id = p_project_id for update;
    select snapshot into data from public.panel_design_contexts
        where project_id = p_project_id and design_context_id = p_context_id and is_current;
    if data is null then
        return jsonb_build_object('conflict', 'Unknown or superseded design context');
    end if;
    perform public.panel_assert_source(p_project_id, data -> 'source_refs');
    return data;
end;
$$;

create function public.panel_save_revision(p_project_id uuid, p_row jsonb, p_expected_version bigint)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare
    previous public.phien_ban_phuong_an_tu_dien;
    saved public.phien_ban_phuong_an_tu_dien;
    pinned jsonb;
    current_version bigint;
begin
    perform 1 from public.projects where id = p_project_id for update;
    if not found then
        raise exception 'Unknown project' using errcode = '23503';
    end if;
    -- Retry is resolved before stale-version/source checks, even after later edits.
    select * into saved from public.phien_ban_phuong_an_tu_dien
        where project_id = p_project_id and request_id = p_row ->> 'request_id';
    if found then
        if saved.request_hash is distinct from p_row ->> 'request_hash' then
            return jsonb_build_object('conflict', 'request_id reused with different payload');
        end if;
        return to_jsonb(saved);
    end if;
    select * into previous from public.phien_ban_phuong_an_tu_dien
        where project_id = p_project_id order by version desc limit 1;
    current_version := coalesce(previous.version, 0);
    if p_expected_version is distinct from current_version or
       (current_version > 0 and previous.plan_id::text is distinct from p_row ->> 'plan_id') then
        return jsonb_build_object('conflict', 'Plan/version changed; reload the current draft');
    end if;
    select snapshot into pinned from public.panel_design_contexts
        where project_id = p_project_id and design_context_id = p_row ->> 'design_context_id'
        and is_current;
    if pinned is null or pinned -> 'source_refs' is distinct from p_row -> 'source_refs' then
        return jsonb_build_object('conflict', 'Design context/source refs changed');
    end if;
    perform public.panel_assert_source(p_project_id, pinned -> 'source_refs');
    if p_row #>> '{validation_result,validated_schedule_hash}' is distinct from p_row ->> 'schedule_hash'
       or jsonb_typeof(p_row #> '{validation_result,errors}') is distinct from 'array'
       or jsonb_typeof(p_row #> '{validation_result,unperformed_checks}') is distinct from 'array' then
        raise exception 'Invalid validation result' using errcode = '23514';
    end if;
    insert into public.phien_ban_phuong_an_tu_dien (
        project_id, plan_id, version, design_context_id, request_id, request_hash,
        schedule, schedule_hash, source_refs, calculation_summary, validation_result,
        design_reasons, diff_summary
    ) values (
        p_project_id, (p_row ->> 'plan_id')::uuid, current_version + 1,
        p_row ->> 'design_context_id', p_row ->> 'request_id', p_row ->> 'request_hash',
        p_row -> 'schedule', p_row ->> 'schedule_hash', p_row -> 'source_refs',
        nullif(p_row -> 'calculation_summary', 'null'::jsonb), p_row -> 'validation_result',
        p_row -> 'design_reasons', p_row -> 'diff_summary'
    ) returning * into saved;
    update public.projects set phase = 'REVIEW_PANELS' where id = p_project_id;
    return to_jsonb(saved);
end;
$$;

-- Artifacts belong to a precise immutable revision. A failing concurrent renderer
-- cannot overwrite a successful artifact. Pending/failed can always be retried.
create function public.panel_set_render(p_project_id uuid, p_plan_id uuid, p_version bigint,
    p_schedule_hash text, p_status text, p_artifact jsonb)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare saved public.phien_ban_phuong_an_tu_dien;
begin
    select * into saved from public.phien_ban_phuong_an_tu_dien where project_id = p_project_id
        and plan_id = p_plan_id and version = p_version for update;
    if not found or saved.schedule_hash is distinct from p_schedule_hash then
        return jsonb_build_object('conflict', 'Render targets an unknown revision/hash');
    end if;
    if saved.sld_status = 'ready' then return to_jsonb(saved); end if;
    if p_status not in ('ready', 'failed') or p_status is null or
       (p_status = 'ready' and (p_artifact is null or
        p_artifact ->> 'plan_id' is distinct from p_plan_id::text or
        (p_artifact ->> 'version')::bigint is distinct from p_version or
        p_artifact ->> 'schedule_hash' is distinct from p_schedule_hash or
        p_artifact ->> 'render_status' is distinct from 'ready')) then
        raise exception 'Invalid render result' using errcode = '23514';
    end if;
    update public.phien_ban_phuong_an_tu_dien set sld_status = p_status,
        sld_artifact = case when p_status = 'ready' then p_artifact else null end
        where id = saved.id returning * into saved;
    return to_jsonb(saved);
end;
$$;

-- Private helper is callable only by the function owner. Fixed search_path prevents
-- object shadowing. All RPCs require the backend service role, not authenticated.
revoke all on function public.panel_assert_source(uuid, jsonb) from public, anon, authenticated, service_role;
revoke all on function public.panel_register_context(uuid, jsonb) from public, anon, authenticated;
revoke all on function public.panel_load_context(uuid, text) from public, anon, authenticated;
revoke all on function public.panel_save_revision(uuid, jsonb, bigint) from public, anon, authenticated;
revoke all on function public.panel_set_render(uuid, uuid, bigint, text, text, jsonb) from public, anon, authenticated;
grant execute on function public.panel_register_context(uuid, jsonb) to service_role;
grant execute on function public.panel_load_context(uuid, text) to service_role;
grant execute on function public.panel_save_revision(uuid, jsonb, bigint) to service_role;
grant execute on function public.panel_set_render(uuid, uuid, bigint, text, text, jsonb) to service_role;
