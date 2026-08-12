FROM node:20-alpine AS base
# Modified for Ram0; see NOTICE and repository history.
RUN apk add --no-cache libc6-compat
WORKDIR /app

# Dependencies
FROM base AS deps
COPY server/dashboard/package.json server/dashboard/yarn.lock* server/dashboard/package-lock.json* server/dashboard/pnpm-lock.yaml* ./
RUN \
  if [ -f yarn.lock ]; then yarn --frozen-lockfile --network-timeout 600000; \
  elif [ -f package-lock.json ]; then npm ci; \
  elif [ -f pnpm-lock.yaml ]; then corepack enable pnpm && pnpm i; \
  else npm install; \
  fi

# Build
FROM base AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY server/dashboard/ .

ENV NEXT_TELEMETRY_DISABLED=1
# Placeholder values replaced at runtime by entrypoint.sh
ENV NEXT_PUBLIC_API_URL=NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_INSTANCE_NAME=NEXT_PUBLIC_INSTANCE_NAME

RUN npm run build

# Production
FROM base AS runner
WORKDIR /app

ARG VCS_REF
ARG SOURCE_URL=https://github.com/olhapi/ram0
LABEL org.opencontainers.image.source=$SOURCE_URL \
      org.opencontainers.image.revision=$VCS_REF \
      org.opencontainers.image.licenses=Apache-2.0

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

RUN addgroup --system --gid 1001 nodejs && \
    adduser --system --uid 1001 nextjs

COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --chown=nextjs:nodejs server/dashboard/entrypoint.sh /home/nextjs/entrypoint.sh
COPY --from=builder /app/public/legal/fonts /usr/share/licenses/ram0/fonts
COPY LICENSE NOTICE /usr/share/licenses/ram0/
RUN chmod +x /home/nextjs/entrypoint.sh

USER nextjs

EXPOSE 3000
ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

ENTRYPOINT ["/home/nextjs/entrypoint.sh"]
CMD ["node", "server.js"]
