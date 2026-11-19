FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    IMGRESIZE_CACHE_DIR=/cache \
    IMGRESIZE_HOST=0.0.0.0 \
    IMGRESIZE_PORT=8080
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install . \
    && python -c "from PIL import features; print('AVIF support:', features.check('avif'))" \
    && useradd --system --uid 10001 --no-create-home app \
    && mkdir -p /cache \
    && chown app /cache \
    && rm -rf /app/build
USER app
VOLUME ["/cache"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
    CMD ["python", "-c", "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('IMGRESIZE_PORT', '8080'), timeout=2)"]
ENTRYPOINT ["imgresize"]
CMD ["serve"]
