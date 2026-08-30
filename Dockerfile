FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY run_server.py .

# Persistent volume mount points plus an unprivileged runtime user
RUN mkdir -p /app/data /app/artifacts \
    && useradd --system --create-home --home-dir /app autopilot \
    && chown -R autopilot:autopilot /app

USER autopilot

ENV PYTHONPATH=/app/src
ENV AUTOPILOT_PORT=8090

EXPOSE 8090

CMD ["python", "run_server.py"]
