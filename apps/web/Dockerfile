# syntax=docker/dockerfile:1

FROM node:22-bookworm-slim

ENV PNPM_HOME=/pnpm \
    PATH=/pnpm:$PATH

RUN npm install --global pnpm@11.8.0 \
    && pnpm config set store-dir /pnpm/store

WORKDIR /workspace

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./

RUN --mount=type=cache,id=openrevive-web-pnpm-store,target=/pnpm/store \
    pnpm install --frozen-lockfile

EXPOSE 3000

CMD ["pnpm", "dev", "--hostname", "0.0.0.0", "--port", "3000"]
