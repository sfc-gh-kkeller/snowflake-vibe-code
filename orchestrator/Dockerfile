FROM python:3.12-slim

RUN pip install --no-cache-dir snowflake-connector-python

COPY api.py /opt/orchestrator/api.py

ENV API_PORT=8080
ENV SNOWFLAKE_ACCOUNT=""
ENV SNOWFLAKE_HOST=""
ENV PROJECT_SCHEMA="DEVCONTAINER_DB.PROJECTS"
ENV PROJECT_POOL="PROJECT_POOL"
ENV PROJECT_IMAGE="/devcontainer_db/spcs/images/project-base:latest"
ENV QUERY_WAREHOUSE="DEVCONTAINER_WH"

EXPOSE 8080

CMD ["python", "/opt/orchestrator/api.py"]
