# SPDX-FileCopyrightText: 2026 Ram0 contributors
# SPDX-License-Identifier: MIT

FROM oven/bun:1.3.6 AS dependencies
WORKDIR /repo

COPY . .
RUN bun install --frozen-lockfile --filter memory-graph-playground

FROM node:24.8.0-bookworm-slim AS build
WORKDIR /repo/apps/memory-graph-playground
COPY --from=dependencies /repo /repo
RUN node ../../node_modules/next/dist/bin/next build

FROM node:24.8.0-bookworm-slim AS runtime
WORKDIR /app
COPY LICENSE NOTICE /usr/share/licenses/ram0/
ENV HOSTNAME=0.0.0.0
ENV NODE_ENV=production
ENV PORT=3000
COPY --from=build --chown=node:node /repo/apps/memory-graph-playground/.next/standalone ./
COPY --from=build --chown=node:node /repo/apps/memory-graph-playground/.next/static ./apps/memory-graph-playground/.next/static
COPY --from=build --chown=node:node /repo/apps/memory-graph-playground/public ./apps/memory-graph-playground/public
USER node
EXPOSE 3000
CMD ["node", "apps/memory-graph-playground/server.js"]
