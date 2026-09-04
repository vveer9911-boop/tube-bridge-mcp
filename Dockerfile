FROM python:3.12-slim

WORKDIR /app

# Install system dependencies: ffmpeg, curl, unzip
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl unzip \
    && rm -rf /var/lib/apt/lists/*

# Install deno as fast JavaScript runtime for yt-dlp
RUN curl -fsSL https://deno.land/install.sh | sh
ENV DENO_INSTALL="/root/.deno"
ENV PATH="${DENO_INSTALL}/bin:${PATH}"

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py /app/

ENV PORT=10000
EXPOSE 10000

CMD ["python", "server.py"]
