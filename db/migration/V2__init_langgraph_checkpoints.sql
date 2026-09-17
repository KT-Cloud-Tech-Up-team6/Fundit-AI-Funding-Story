-- Baseline for langgraph-checkpoint-postgres 3.1.2.
-- The checkpoint_migrations rows keep the vendor schema version explicit while Flyway
-- remains the only component allowed to change the database schema.
CREATE TABLE checkpoint_migrations (
    v integer PRIMARY KEY
);

CREATE TABLE checkpoints (
    thread_id text NOT NULL,
    checkpoint_ns text NOT NULL DEFAULT '',
    checkpoint_id text NOT NULL,
    parent_checkpoint_id text,
    type text,
    checkpoint jsonb NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);

CREATE TABLE checkpoint_blobs (
    thread_id text NOT NULL,
    checkpoint_ns text NOT NULL DEFAULT '',
    channel text NOT NULL,
    version text NOT NULL,
    type text NOT NULL,
    blob bytea,
    PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
);

CREATE TABLE checkpoint_writes (
    thread_id text NOT NULL,
    checkpoint_ns text NOT NULL DEFAULT '',
    checkpoint_id text NOT NULL,
    task_id text NOT NULL,
    idx integer NOT NULL,
    channel text NOT NULL,
    type text,
    blob bytea NOT NULL,
    task_path text NOT NULL DEFAULT '',
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);

CREATE INDEX checkpoints_thread_id_idx ON checkpoints(thread_id);
CREATE INDEX checkpoint_blobs_thread_id_idx ON checkpoint_blobs(thread_id);
CREATE INDEX checkpoint_writes_thread_id_idx ON checkpoint_writes(thread_id);

INSERT INTO checkpoint_migrations(v)
SELECT generate_series(0, 9);
