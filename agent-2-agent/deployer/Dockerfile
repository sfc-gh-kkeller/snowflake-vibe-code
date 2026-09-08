FROM python:3.12-slim

RUN pip install --no-cache-dir snowflake-cli snowflake-connector-python

COPY deploy_server.py /opt/deployer/deploy_server.py

ENV API_PORT=8080
EXPOSE 8080

CMD ["python", "/opt/deployer/deploy_server.py"]
