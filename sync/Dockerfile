FROM python:3.12-slim

WORKDIR /app

COPY sync/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY sync/sync_to_mirror.py ./sync_to_mirror.py

ENTRYPOINT ["python", "/app/sync_to_mirror.py"]
