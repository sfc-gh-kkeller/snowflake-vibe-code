-- =============================================================================
-- SPCS DevContainer: Service Deployment
-- Creates the SPCS service from the specification
-- =============================================================================

USE ROLE ACCOUNTADMIN;
USE DATABASE DEVCONTAINER_DB;
USE SCHEMA SPCS;

CREATE SERVICE IF NOT EXISTS DEVCONTAINER_DB.SPCS.DEVCONTAINER_SERVICE
  IN COMPUTE POOL DEVCONTAINER_POOL
  FROM SPECIFICATION $$
spec:
  containers:
    - name: devcontainer
      image: /devcontainer_db/spcs/images/devcontainer:latest
      env:
        HOME: "/home/pixi"
        USER: "pixi"
        WORKSPACE_DIR: "/home/pixi/workspace"
      volumeMounts:
        - name: pixi-home
          mountPath: /home/pixi
      resources:
        requests:
          memory: 2Gi
          cpu: 1000m
        limits:
          memory: 4Gi
          cpu: 2000m
      readinessProbe:
        port: 8080
        path: /api/health
  endpoints:
    - name: api
      port: 8080
    - name: vscode
      port: 3000
      public: true
    - name: terminal
      port: 7681
      public: true
  volumes:
    - name: pixi-home
      source: "@devcontainer_db.spcs.home_stage"
      uid: 1000
      gid: 1000
capabilities:
  securityContext:
    executeAsCaller: true
serviceRoles:
  - name: api_user
    endpoints:
      - api
      - vscode
      - terminal
$$
  EXTERNAL_ACCESS_INTEGRATIONS = (DEVCONTAINER_EXTERNAL_ACCESS)
  MIN_INSTANCES = 1
  MAX_INSTANCES = 1;
