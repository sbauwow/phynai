FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ripgrep git curl \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .

RUN pip install --no-cache-dir -e .

ENV PHYNAI_HOME=/opt/data
VOLUME ["/opt/data"]

EXPOSE 8080

ENTRYPOINT ["phynai", "serve", "--host", "0.0.0.0", "--port", "8080"]
