create table public.phase1_questionnaire_revisions (
    project_id text not null,
    revision bigint not null check (revision > 0),
    snapshot jsonb not null,
    primary key (project_id, revision)
);

create table public.phase1_custom_devices (
    project_id text not null,
    request_id text not null,
    load_class_id text not null unique,
    payload jsonb not null,
    item jsonb not null,
    primary key (project_id, request_id)
);

alter table public.phase1_questionnaire_revisions enable row level security;
alter table public.phase1_custom_devices enable row level security;
revoke all on public.phase1_questionnaire_revisions, public.phase1_custom_devices from public, anon, authenticated;
grant select, insert on public.phase1_questionnaire_revisions, public.phase1_custom_devices to service_role;

create function public.phase1_save_questionnaire(
    p_project_id text, p_expected_revision bigint, p_submission jsonb, p_context jsonb
) returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
    current_revision bigint := 0;
    latest_snapshot jsonb;
    saved_snapshot jsonb;
begin
    perform pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended('phase1.questionnaire:' || p_project_id, 0)
    );
    select revision, snapshot into current_revision, latest_snapshot
    from public.phase1_questionnaire_revisions
    where project_id = p_project_id
    order by revision desc limit 1;
    current_revision := coalesce(current_revision, 0);

    if latest_snapshot -> 'submission' = p_submission and p_expected_revision <= current_revision then
        return latest_snapshot;
    end if;
    if p_expected_revision is distinct from current_revision then
        return jsonb_build_object('conflict_revision', current_revision);
    end if;

    saved_snapshot := jsonb_build_object(
        'questionnaire_revision', current_revision + 1,
        'submission', p_submission,
        'context', p_context
    );
    insert into public.phase1_questionnaire_revisions values (
        p_project_id, current_revision + 1, saved_snapshot
    );
    return saved_snapshot;
end;
$$;

revoke execute on function public.phase1_save_questionnaire(text, bigint, jsonb, jsonb) from public, anon, authenticated;
grant execute on function public.phase1_save_questionnaire(text, bigint, jsonb, jsonb) to service_role;
