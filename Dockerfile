FROM python:3.12-slim

# libgl/glib/gtk: opencv GUI build (preview/enroll windows); loginctl etc. for locking via host session
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 libgtk-3-0 libnotify-bin \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY shoulder_guard ./shoulder_guard
RUN pip install --no-cache-dir .

ENTRYPOINT ["shoulder-guard"]
CMD ["run"]
