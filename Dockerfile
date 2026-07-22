FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PRIMOSCORE_HOST=0.0.0.0 PRIMOSCORE_PORT=4173
WORKDIR /app
RUN useradd --create-home --uid 10001 primoscore && mkdir -p /app/data && chown -R primoscore:primoscore /app
COPY --chown=primoscore:primoscore server.py ./
COPY --chown=primoscore:primoscore site ./site
USER primoscore
EXPOSE 4173
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:4173/api/health',timeout=3)"
CMD ["python","server.py"]
