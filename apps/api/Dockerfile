# syntax=docker/dockerfile:1.7
# Build context: repository root. Every install uses the lockfile (--frozen-lockfile), so the
# image runs exactly the dependency versions CI tested.
FROM node:24-slim AS base
ENV PNPM_HOME=/pnpm PATH=/pnpm:$PATH
RUN corepack enable
WORKDIR /src

FROM base AS build
COPY . .
RUN --mount=type=cache,id=pnpm,target=/pnpm/store \
    pnpm install --frozen-lockfile --filter "@firra/api..." \
 && pnpm --filter "@firra/api..." run build

FROM base AS prod-deps
COPY . .
RUN --mount=type=cache,id=pnpm,target=/pnpm/store \
    pnpm install --frozen-lockfile --prod --filter "@firra/api..."

# Distroless: no shell or package manager; runs as the built-in non-root user.
FROM gcr.io/distroless/nodejs24-debian12:nonroot AS runtime
ENV NODE_ENV=production PORT=8080
WORKDIR /app
# pnpm's node_modules are relative symlinks into /app/node_modules/.pnpm; keep the layout.
COPY --from=prod-deps /src/node_modules ./node_modules
COPY --from=prod-deps /src/apps/api/node_modules ./apps/api/node_modules
COPY --from=prod-deps /src/packages/contracts/node_modules ./packages/contracts/node_modules
COPY --from=build /src/packages/contracts/package.json ./packages/contracts/
COPY --from=build /src/packages/contracts/dist ./packages/contracts/dist
COPY --from=build /src/apps/api/package.json ./apps/api/
COPY --from=build /src/apps/api/dist ./apps/api/dist
WORKDIR /app/apps/api
USER 65532:65532
EXPOSE 8080
CMD ["dist/index.js"]
