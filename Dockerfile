FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY platform/requirements.txt /app/platform/requirements.txt
COPY deploy/render/requirements.txt /app/deploy/render/requirements.txt
RUN pip install --no-cache-dir -r /app/deploy/render/requirements.txt \
    && groupadd --gid 10001 primoscore \
    && useradd --uid 10001 --gid primoscore --no-create-home primoscore
COPY dist /app/dist
COPY platform/primoscore_core /app/platform/primoscore_core
COPY platform/primoscore_server /app/platform/primoscore_server
COPY deploy/render/runtime.py /app/deploy/render/runtime.py
EXPOSE 4173
ENTRYPOINT ["python", "/app/deploy/render/runtime.py"]
CMD ["web"]
