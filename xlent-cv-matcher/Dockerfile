FROM node:20-alpine AS web-build
WORKDIR /src
COPY apps/web/package*.json ./apps/web/
WORKDIR /src/apps/web
RUN npm ci
COPY apps/web/ .
RUN npm run build

FROM python:3.12-slim AS api-run
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY apps/api /app/apps/api
WORKDIR /app/apps/api
RUN pip install --no-cache-dir -e .

COPY --from=web-build /src/apps/web/dist /app/web_dist

ENV WEB_DIST_DIR=/app/web_dist
ENV CORS_ALLOW_ORIGINS=http://127.0.0.1:5173,http://localhost:5173

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
