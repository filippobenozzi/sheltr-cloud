FROM oven/bun:1.3.12-alpine AS frontend-builder

WORKDIR /app/frontend

COPY frontend/package.json ./
COPY frontend/bun.lock ./
COPY frontend/tsconfig.json ./
COPY frontend/tsconfig.node.json ./
COPY frontend/vite.config.ts ./
COPY frontend/components.json ./
COPY frontend/index.html ./

RUN bun install --frozen-lockfile

COPY frontend/src ./src
RUN bun run build

FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY static ./static
COPY --from=frontend-builder /app/frontend/dist ./static/app

ENV APP_PORT=8080
EXPOSE 8080

CMD ["gunicorn", "-w", "1", "-b", "0.0.0.0:8080", "app:app"]
