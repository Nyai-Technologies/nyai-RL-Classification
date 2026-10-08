FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

# OpenShift (OCP) starts containers as an arbitrary non-root user that belongs to group 0, so /app is made group-readable.
# Nothing is written to disk: logs go to stdout as JSON (read them with `oc logs`), cost lines too.
RUN chgrp -R 0 /app && chmod -R g=u /app
ENV PYTHONUNBUFFERED=1 PORT=8080 WEB_CONCURRENCY=2 \
    LOG_FORMAT=json LOG_FILE=none COST_LOG_FILE=none
USER 1001
EXPOSE 8080
# stateless service: scale with WEB_CONCURRENCY (processes) and replicas (pods). SIGTERM lets in-flight requests finish.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers ${WEB_CONCURRENCY} --no-access-log --timeout-graceful-shutdown 60"]
# Pulsar worker from the same image (a second Deployment): override the command with
#   python -m app.pulsar_worker
