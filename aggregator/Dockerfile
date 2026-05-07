# Stage 1: build
FROM golang:1.24-alpine AS builder
WORKDIR /build

# Download dependencies separately so this layer is cached when source changes.
COPY go.mod go.sum ./
RUN go mod download

COPY . .

ARG VERSION=dev
ARG GIT_SHA=unknown
ARG BUILD_TIME=unknown

RUN CGO_ENABLED=0 go build \
    -ldflags="-s -w \
      -X main.version=${VERSION} \
      -X main.gitSHA=${GIT_SHA} \
      -X main.buildTime=${BUILD_TIME}" \
    -o /aggregator ./cmd/aggregator/

# Stage 2: minimal runtime image — no Go toolchain
FROM gcr.io/distroless/static-debian12
COPY --from=builder /aggregator /aggregator
USER nonroot:nonroot
ENTRYPOINT ["/aggregator"]
