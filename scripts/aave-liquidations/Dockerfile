FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc \
    python3-dev \
    libgmp-dev \
    libmpfr-dev \
    libmpc-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install poetry

COPY pyproject.toml poetry.lock ./
COPY eth_defi ./eth_defi
COPY scripts/aave-liquidations/websocket_server_hypersync.py ./

RUN poetry config virtualenvs.create false && \
    poetry install --no-root --only main --extras data --extras hypersync && \
    pip install web3

EXPOSE 8001

ENV PYTHONUNBUFFERED=1
ENV PORT=8001

CMD ["python", "websocket_server_hypersync.py"]
