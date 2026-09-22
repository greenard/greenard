# Image backend (API + workers Celery). Bibliothèques natives (ecCodes, GDAL, PROJ, HDF5/netCDF)
# issues de conda-forge pour garantir leur cohérence (voir environment.yml).
FROM mambaorg/micromamba:1.5.10-jammy

COPY --chown=$MAMBA_USER:$MAMBA_USER environment.yml /tmp/environment.yml
RUN micromamba install -y -n base -f /tmp/environment.yml && micromamba clean --all --yes

ARG MAMBA_DOCKERFILE_ACTIVATE=1
WORKDIR /app
COPY --chown=$MAMBA_USER:$MAMBA_USER pyproject.toml alembic.ini ./
COPY --chown=$MAMBA_USER:$MAMBA_USER alembic ./alembic
COPY --chown=$MAMBA_USER:$MAMBA_USER app ./app
RUN pip install --no-cache-dir .

ENV GREENARD_DATA_DIR=/data \
    PYTHONUNBUFFERED=1
USER root
RUN mkdir -p /data && chown $MAMBA_USER:$MAMBA_USER /data
USER $MAMBA_USER

EXPOSE 8000
# Migrations puis API ; les workers surchargent la commande (docker-compose.yml).
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]
