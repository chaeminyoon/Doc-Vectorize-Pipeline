FROM python:3.11-slim

ARG INSTALL_LIBREOFFICE=false
ARG TORCH_VERSION=2.6.0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends libgomp1 && \
    if [ "$INSTALL_LIBREOFFICE" = "true" ]; then \
        apt-get install -y --no-install-recommends libreoffice; \
    fi && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==${TORCH_VERSION} && \
    pip install --no-cache-dir -r /app/requirements.txt

COPY config /app/config
COPY src /app/src
COPY scripts/docker-entrypoint.sh /app/scripts/docker-entrypoint.sh
COPY main.py /app/main.py
COPY HOW_TO_RUN.md /app/HOW_TO_RUN.md
COPY ONPREM_DOCKER.md /app/ONPREM_DOCKER.md

RUN chmod +x /app/scripts/docker-entrypoint.sh

ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]
CMD ["stats"]
