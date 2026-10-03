-- Database + least-privilege users. DEV-ONLY credentials; in AWS these come from env/secrets.
CREATE DATABASE IF NOT EXISTS otel;
CREATE USER IF NOT EXISTS hyperdx_ro IDENTIFIED BY 'hyperdx_ro_dev';
GRANT SELECT ON otel.* TO hyperdx_ro;
