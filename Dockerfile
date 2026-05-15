FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY run_server.py .

# Create persistent volume mount points
RUN mkdir -p /app/data /app/artifacts

ENV PYTHONPATH=/app/src
ENV AUTOPILOT_PORT=8090

EXPOSE 8090

CMD ["python", "run_server.py"]
