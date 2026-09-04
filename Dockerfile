FROM python:3.12-slim

WORKDIR /app

# Install system dependencies: ffmpeg and curl
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py /app/

ENV PORT=10000
EXPOSE 10000

CMD ["python", "server.py"]
