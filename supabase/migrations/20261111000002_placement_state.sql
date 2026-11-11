-- Backend-only project state. This schema is outside the Supabase Data API.
CREATE SCHEMA mvp_placement;
REVOKE ALL ON SCHEMA mvp_placement FROM PUBLIC;

CREATE TABLE mvp_placement.placement_plan_versions (
    project_id text NOT NULL,
    plan_id text NOT NULL,
    version bigint NOT NULL CHECK (version > 0),
    status text NOT NULL CHECK (status = 'pending_review'),
    proposals jsonb NOT NULL,
    validation_result jsonb NOT NULL,
    diff_summary jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, plan_id, version)
);

CREATE TABLE mvp_placement.placement_removals (
    project_id text NOT NULL,
    plan_id text NOT NULL,
    version bigint NOT NULL,
    proposed_id text NOT NULL,
    device_id text NOT NULL,
    target text NOT NULL CHECK (target IN ('draft', 'ifc')),
    reason text NOT NULL,
    affected_references jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, plan_id, version),
    FOREIGN KEY (project_id, plan_id, version)
        REFERENCES mvp_placement.placement_plan_versions (project_id, plan_id, version)
);

CREATE TABLE mvp_placement.placement_approvals (
    project_id text NOT NULL,
    plan_id text NOT NULL,
    version bigint NOT NULL,
    reviewer_id text NOT NULL,
    approved_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, plan_id, version),
    FOREIGN KEY (project_id, plan_id, version)
        REFERENCES mvp_placement.placement_plan_versions (project_id, plan_id, version)
);

CREATE TABLE mvp_placement.project_ifc_versions (
    project_id text NOT NULL,
    version bigint NOT NULL CHECK (version >= 0),
    filename text NOT NULL CHECK (filename ~ '^[a-f0-9]{32}[.]ifc$'),
    sha256 text NOT NULL CHECK (sha256 ~ '^[a-f0-9]{64}$'),
    PRIMARY KEY (project_id, version)
);

CREATE TABLE mvp_placement.placement_applications (
    project_id text NOT NULL,
    plan_id text NOT NULL,
    plan_version bigint NOT NULL,
    model_version bigint NOT NULL,
    PRIMARY KEY (project_id, plan_id, plan_version),
    FOREIGN KEY (project_id, plan_id, plan_version)
        REFERENCES mvp_placement.placement_approvals (project_id, plan_id, version),
    FOREIGN KEY (project_id, model_version)
        REFERENCES mvp_placement.project_ifc_versions (project_id, version)
);
