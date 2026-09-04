-- =============================================================================
-- SPCS DevContainer: Lifecycle Management
-- Procedures for provisioning, suspending, resuming, and destroying containers
-- =============================================================================

USE ROLE ACCOUNTADMIN;
USE DATABASE DEVCONTAINER_DB;
USE SCHEMA SPCS;

-- -----------------------------------------------------------------------------
-- Resume the dev container (wake from auto-suspend)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE DEVCONTAINER_DB.SPCS.RESUME_CONTAINER()
RETURNS VARCHAR
LANGUAGE SQL
EXECUTE AS OWNER
AS
BEGIN
  ALTER COMPUTE POOL DEVCONTAINER_POOL RESUME;
  RETURN 'Compute pool resumed. Container will be ready in ~30 seconds.';
END;

-- -----------------------------------------------------------------------------
-- Suspend the dev container (stop costs)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE DEVCONTAINER_DB.SPCS.SUSPEND_CONTAINER()
RETURNS VARCHAR
LANGUAGE SQL
EXECUTE AS OWNER
AS
BEGIN
  ALTER COMPUTE POOL DEVCONTAINER_POOL SUSPEND;
  RETURN 'Compute pool suspended. No further compute charges until resumed.';
END;

-- -----------------------------------------------------------------------------
-- Get container status
-- -----------------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE DEVCONTAINER_DB.SPCS.CONTAINER_STATUS()
RETURNS VARIANT
LANGUAGE SQL
EXECUTE AS OWNER
AS
DECLARE
  result VARIANT;
BEGIN
  SELECT OBJECT_CONSTRUCT(
    'service_status', (SELECT status FROM TABLE(RESULT_SCAN(LAST_QUERY_ID()))),
    'compute_pool_state', (SELECT state FROM TABLE(RESULT_SCAN(LAST_QUERY_ID())))
  ) INTO :result;
  
  -- Get service status
  SHOW SERVICES LIKE 'DEVCONTAINER_SERVICE' IN SCHEMA DEVCONTAINER_DB.SPCS;
  
  RETURN :result;
END;

-- -----------------------------------------------------------------------------
-- Destroy the dev container (removes service, keeps stage data)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE DEVCONTAINER_DB.SPCS.DESTROY_CONTAINER()
RETURNS VARCHAR
LANGUAGE SQL
EXECUTE AS OWNER
AS
BEGIN
  DROP SERVICE IF EXISTS DEVCONTAINER_DB.SPCS.DEVCONTAINER_SERVICE;
  RETURN 'Service destroyed. Stage data (your code) is preserved. Re-run 02_service.sql to recreate.';
END;

-- -----------------------------------------------------------------------------
-- Get service endpoints (URLs for browser access)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE DEVCONTAINER_DB.SPCS.GET_ENDPOINTS()
RETURNS TABLE (endpoint_name VARCHAR, url VARCHAR, is_public BOOLEAN)
LANGUAGE SQL
EXECUTE AS OWNER
AS
BEGIN
  SHOW ENDPOINTS IN SERVICE DEVCONTAINER_DB.SPCS.DEVCONTAINER_SERVICE;
  LET rs RESULTSET := (
    SELECT "name" AS endpoint_name, "ingress_url" AS url, "is_public"::BOOLEAN AS is_public
    FROM TABLE(RESULT_SCAN(LAST_QUERY_ID()))
  );
  RETURN TABLE(rs);
END;

-- Grant lifecycle procedures to user role
GRANT USAGE ON PROCEDURE DEVCONTAINER_DB.SPCS.RESUME_CONTAINER()
  TO ROLE DEVCONTAINER_USER;
GRANT USAGE ON PROCEDURE DEVCONTAINER_DB.SPCS.SUSPEND_CONTAINER()
  TO ROLE DEVCONTAINER_USER;
GRANT USAGE ON PROCEDURE DEVCONTAINER_DB.SPCS.CONTAINER_STATUS()
  TO ROLE DEVCONTAINER_USER;
GRANT USAGE ON PROCEDURE DEVCONTAINER_DB.SPCS.DESTROY_CONTAINER()
  TO ROLE DEVCONTAINER_USER;
GRANT USAGE ON PROCEDURE DEVCONTAINER_DB.SPCS.GET_ENDPOINTS()
  TO ROLE DEVCONTAINER_USER;
