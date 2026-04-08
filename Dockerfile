FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ripgrep git curl \
  && rm -rf /var/lib/apt/lists/*

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

WORKDIR /app
COPY . .

RUN uv sync --frozen

ENV PHYNAI_HOME=/opt/data
VOLUME ["/opt/data"]

EXPOSE 8080

ENTRYPOINT ["uv", "run", "phynai", "serve", "--host", "0.0.0.0", "--port", "8080"]
