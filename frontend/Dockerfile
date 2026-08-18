FROM node:20-slim AS build

WORKDIR /srv
ENV NEXT_TELEMETRY_DISABLED=1

COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY tsconfig.json next.config.ts eslint.config.mjs ./
COPY src ./src
RUN npm run build

FROM node:20-slim AS runtime

WORKDIR /srv
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1

COPY --from=build /srv/package.json /srv/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund

COPY --from=build /srv/.next ./.next
COPY --from=build /srv/next.config.ts ./next.config.ts

USER node
EXPOSE 3000
# Bind all interfaces: the default localhost binding is unreachable from
# outside the container.
CMD ["npx", "next", "start", "-H", "0.0.0.0", "-p", "3000"]
