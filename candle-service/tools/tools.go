//go:build tools

package tools

import (
	_ "github.com/aws/aws-sdk-go-v2/feature/s3/manager"
	_ "github.com/aws/aws-sdk-go-v2/service/s3"
	_ "github.com/parquet-go/parquet-go"
	_ "github.com/prometheus/client_golang/prometheus"
	_ "github.com/questdb/go-questdb-client/v3"
	_ "github.com/redis/go-redis/v9"
	_ "github.com/stretchr/testify/assert"
)
