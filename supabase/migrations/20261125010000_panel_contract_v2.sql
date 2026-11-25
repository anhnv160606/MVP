-- Dev C v2 follow-up; run AFTER 20261124010000_panel_schedules.sql.
-- Preserve schedule history/hashes. Existing metadata-only renders become retryable.
alter table public.phien_ban_phuong_an_tu_dien add column edit_reason text
    check (edit_reason is null or (length(btrim(edit_reason)) > 0 and length(edit_reason) <= 2000));

create function public.panel_sld_has_content(artifact jsonb)
returns boolean language sql immutable set search_path = '' as $$
    select coalesce(
        jsonb_typeof(artifact) = 'object' and
        jsonb_typeof(artifact -> 'artifact_id') = 'string' and
        length(btrim(artifact ->> 'artifact_id')) > 0 and
        jsonb_typeof(artifact -> 'svg') = 'string' and
        length(btrim(artifact ->> 'svg')) > 0 and
        jsonb_typeof(artifact -> 'element_index') = 'object' and
        artifact -> 'element_index' <> '{}'::jsonb and
        artifact ->> 'render_status' = 'ready', false);
$$;

update public.phien_ban_phuong_an_tu_dien
    set sld_status = 'failed', sld_artifact = null
    where sld_status = 'ready' and not public.panel_sld_has_content(sld_artifact);

alter table public.phien_ban_phuong_an_tu_dien add constraint panel_ready_has_content
    check (sld_status <> 'ready' or public.panel_sld_has_content(sld_artifact));

create or replace function public.panel_save_revision(p_project_id uuid, p_row jsonb, p_expected_version bigint)
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
    if p_row #>> '{schedule,schema_version}' is distinct from '2' or
       pinned ->> 'schema_version' is distinct from '2' or
       jsonb_typeof(p_row #> '{schedule,phase_assignments}') is distinct from 'array' then
        raise exception 'Panel schema v2 context and schedule required' using errcode = '23514';
    end if;
    if p_row #>> '{validation_result,validated_schedule_hash}' is distinct from p_row ->> 'schedule_hash'
       or jsonb_typeof(p_row #> '{validation_result,errors}') is distinct from 'array'
       or jsonb_typeof(p_row #> '{validation_result,unperformed_checks}') is distinct from 'array' then
        raise exception 'Invalid validation result' using errcode = '23514';
    end if;
    insert into public.phien_ban_phuong_an_tu_dien (
        project_id, plan_id, version, design_context_id, request_id, request_hash,
        schedule, schedule_hash, source_refs, calculation_summary, validation_result,
        design_reasons, diff_summary, edit_reason
    ) values (
        p_project_id, (p_row ->> 'plan_id')::uuid, current_version + 1,
        p_row ->> 'design_context_id', p_row ->> 'request_id', p_row ->> 'request_hash',
        p_row -> 'schedule', p_row ->> 'schedule_hash', p_row -> 'source_refs',
        nullif(p_row -> 'calculation_summary', 'null'::jsonb), p_row -> 'validation_result',
        p_row -> 'design_reasons', p_row -> 'diff_summary', p_row ->> 'edit_reason'
    ) returning * into saved;
    update public.projects set phase = 'REVIEW_PANELS' where id = p_project_id;
    return to_jsonb(saved);
end;
$$;

create or replace function public.panel_set_render(p_project_id uuid, p_plan_id uuid, p_version bigint,
    p_schedule_hash text, p_status text, p_artifact jsonb)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare saved public.phien_ban_phuong_an_tu_dien;
begin
    select * into saved from public.phien_ban_phuong_an_tu_dien where project_id = p_project_id
        and plan_id = p_plan_id and version = p_version for update;
    if not found or saved.schedule_hash is distinct from p_schedule_hash then
        return jsonb_build_object('conflict', 'Render targets an unknown revision/hash');
    end if;
    if saved.sld_status = 'ready' and public.panel_sld_has_content(saved.sld_artifact) then return to_jsonb(saved); end if;
    if p_status not in ('ready', 'failed') or p_status is null or
       (p_status = 'ready' and (not public.panel_sld_has_content(p_artifact) or
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

-- SQL enforces mandatory content. Backend additionally parses SVG and verifies IDs.
revoke all on function public.panel_sld_has_content(jsonb) from public, anon, authenticated, service_role;
revoke all on function public.panel_save_revision(uuid, jsonb, bigint) from public, anon, authenticated;
revoke all on function public.panel_set_render(uuid, uuid, bigint, text, text, jsonb) from public, anon, authenticated;
grant execute on function public.panel_save_revision(uuid, jsonb, bigint) to service_role;
grant execute on function public.panel_set_render(uuid, uuid, bigint, text, text, jsonb) to service_role;
