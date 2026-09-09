FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml requirements.lock ./
COPY lifeops_mcp ./lifeops_mcp
RUN pip install --no-cache-dir -c requirements.lock . \
    && useradd --uid 10001 --create-home lifeops \
    && mkdir /state && chown lifeops:lifeops /state
USER lifeops
ENV LIFEOPS_STATE_DIR=/state PORT=8000
EXPOSE 8000
CMD ["lifeops-mcp"]
