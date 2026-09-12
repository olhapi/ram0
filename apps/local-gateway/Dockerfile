# SPDX-FileCopyrightText: 2026 Ram0 contributors
# SPDX-License-Identifier: MIT

FROM oven/bun:1.3.6 AS build
WORKDIR /repo

COPY . .
RUN bun install --frozen-lockfile --filter @ram0/local-gateway

RUN bun run --cwd apps/local-gateway build
RUN bun build tools/ram0-migration/src/cli.ts --target=bun --outfile=/repo/tools/ram0-migration/dist/migrate.js

FROM oven/bun:1.3.6-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build --chown=bun:bun /repo/apps/local-gateway/dist/server.js ./server.js
COPY --from=build --chown=bun:bun /repo/tools/ram0-migration/dist/migrate.js ./migrate.js
USER bun
EXPOSE 8000
CMD ["bun", "server.js"]
