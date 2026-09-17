CREATE TABLE ai_records (
    id text PRIMARY KEY,
    project_id text NOT NULL,
    kind text NOT NULL,
    revision integer NOT NULL DEFAULT 1,
    data jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ai_records_project ON ai_records(project_id, kind);

CREATE TABLE ai_requests (
    project_id text NOT NULL,
    request_key text NOT NULL,
    fingerprint text NOT NULL,
    record_id text NOT NULL REFERENCES ai_records(id),
    PRIMARY KEY(project_id, request_key)
);
