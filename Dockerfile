FROM python:3.10-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DATA_DIR=/data
WORKDIR /app
RUN useradd -m -u 1000 app && mkdir /data && chown app /data
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=app:app . .
USER app
VOLUME /data
EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7860/api/health')" || exit 1
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]
