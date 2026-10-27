FROM python:3.12.8-slim-bookworm@sha256:2199a62885a12290dc9c5be3ca0681d367576ab7bf037da120e564723292a2f0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
    HOME=/tmp

COPY requirements.lock /opt/requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes -r /opt/requirements.lock \
    && mkdir /workspace /results /acceptance \
    && chown 10001:10001 /workspace /results /acceptance

USER 10001:10001
WORKDIR /workspace
ENTRYPOINT ["python", "-B", "-m", "pytest"]
