-- Local development databases only. Existing volumes are not reinitialized.
CREATE DATABASE funding_ai_test OWNER funding_ai;
CREATE USER funding_be WITH PASSWORD 'local-be-only';
CREATE DATABASE funding_be OWNER funding_be;
