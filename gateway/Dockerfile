FROM golang:1.26.3-alpine AS builder
WORKDIR /build
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o /gateway ./cmd/gateway/

FROM gcr.io/distroless/static-debian12
COPY --from=builder /gateway /gateway
USER nonroot:nonroot
ENTRYPOINT ["/gateway"]
