FROM python:3.12-slim

WORKDIR /app

# Install system dependencies: ffmpeg, curl, nodejs (JS runtime for yt-dlp)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl nodejs \
    && rm -rf /var/lib/apt/lists/*

# Add yt-dlp system config to bypass YouTube datacenter IP restrictions
RUN mkdir -p /etc/yt-dlp \
    && echo '--extractor-args "youtube:player_client=mweb,ios"' > /etc/yt-dlp/config \
    && echo '--no-warnings' >> /etc/yt-dlp/config

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py /app/

ENV PORT=10000
EXPOSE 10000

CMD ["python", "server.py"]
