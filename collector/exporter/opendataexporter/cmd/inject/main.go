// Command inject appends raw payloads straight into an OpenData Buffer queue, bypassing the
// collector. It exists for fault-injection tests: OTLP clients cannot produce a malformed
// protobuf, but a bug or a hostile writer could, and the consumer must survive it.
//
//	inject --bucket b --region r [--endpoint http://s3:9000] --manifest otel/traces/manifest \
//	       --prefix otel/traces/data --signal traces --payload "not protobuf" --count 3
package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"time"

	awscfg "github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/opendata-oss/opendata-go/buffer"
	"github.com/opendata-oss/opendata-go/exporter/opendataexporter"
	"github.com/opendata-oss/opendata-go/objstore"
)

func main() {
	bucket := flag.String("bucket", "", "S3 bucket")
	region := flag.String("region", "us-east-1", "AWS region")
	endpoint := flag.String("endpoint", "", "S3 endpoint override (local object stores)")
	manifest := flag.String("manifest", "", "Buffer manifest path")
	prefix := flag.String("prefix", "", "Buffer data path prefix")
	signal := flag.String("signal", "traces", "envelope signal: logs|metrics|traces")
	payload := flag.String("payload", "this is not an OTLP protobuf message", "raw payload bytes")
	count := flag.Int("count", 1, "number of entries to append")
	flag.Parse()

	var sig uint8
	switch *signal {
	case "metrics":
		sig = opendataexporter.SignalTypeMetrics
	case "logs":
		sig = opendataexporter.SignalTypeLogs
	case "traces":
		sig = opendataexporter.SignalTypeTraces
	default:
		fail("unknown --signal %q", *signal)
	}
	if *bucket == "" || *manifest == "" || *prefix == "" {
		fail("--bucket, --manifest and --prefix are required")
	}

	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	awsCfg, err := awscfg.LoadDefaultConfig(ctx, awscfg.WithRegion(*region))
	if err != nil {
		fail("aws config: %v", err)
	}
	client := s3.NewFromConfig(awsCfg, func(o *s3.Options) {
		if *endpoint != "" {
			o.BaseEndpoint = endpoint
		}
	})
	var store objstore.ObjectStore = objstore.NewS3(client, *bucket)

	cfg := buffer.DefaultProducerConfig()
	cfg.DataPathPrefix, cfg.ManifestPath = *prefix, *manifest
	cfg.FlushInterval = 50 * time.Millisecond
	cfg.FlushSizeBytes = 1
	cfg.BatchCompression = buffer.CompressionNone
	p := buffer.NewProducer(store, cfg)
	meta := opendataexporter.EncodeMetadata(sig, opendataexporter.PayloadEncodingOTLP)
	for i := 0; i < *count; i++ {
		h, err := p.Append([][]byte{[]byte(*payload)}, meta)
		if err != nil {
			fail("append: %v", err)
		}
		if err := h.Watcher.AwaitDurable(ctx); err != nil {
			fail("await durable: %v", err)
		}
	}
	if err := p.Close(ctx); err != nil {
		fail("close: %v", err)
	}
	fmt.Printf("injected %d malformed %s entries into %s\n", *count, *signal, *manifest)
}

func fail(f string, a ...any) {
	fmt.Fprintf(os.Stderr, "inject: "+f+"\n", a...)
	os.Exit(1)
}
