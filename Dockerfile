FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir ".[mcp,pdf]" \
    && useradd --create-home --uid 1000 spurlos \
    && mkdir /data /work && chown spurlos /data /work

USER spurlos
ENV SPURLOS_HOME=/data
VOLUME /data
WORKDIR /work
ENTRYPOINT ["spurlos"]
CMD ["--help"]
