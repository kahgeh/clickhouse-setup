// Command peek reads an OpenData Buffer manifest from object storage and prints each queued entry: where its batch
// object lives, the envelope metadata bytes, and the OTLP payload decoded to JSON. It only reads. Used to capture the
// real payload that sits between the collector and the ingestors.
//
//	peek --bucket b --region r [--endpoint http://s3:9000] --manifest otel/traces/manifest
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"time"

	awscfg "github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/opendata-oss/opendata-go/buffer"
	"github.com/opendata-oss/opendata-go/objstore"
	"go.opentelemetry.io/collector/pdata/plog"
	"go.opentelemetry.io/collector/pdata/pmetric"
	"go.opentelemetry.io/collector/pdata/ptrace"
)

func main() {
	bucket := flag.String("bucket", "", "S3 bucket")
	region := flag.String("region", "us-east-1", "AWS region")
	endpoint := flag.String("endpoint", "", "S3 endpoint override")
	manifest := flag.String("manifest", "", "manifest path")
	limit := flag.Int("limit", 1, "entries to decode")
	flag.Parse()
	if *bucket == "" || *manifest == "" {
		fail("--bucket and --manifest are required")
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

	m, err := store.Get(ctx, *manifest)
	if err != nil {
		fail("get manifest: %v", err)
	}
	entries, err := buffer.DecodeManifestEntries(m.Data)
	if err != nil {
		fail("decode manifest: %v", err)
	}
	out := map[string]any{"manifest": *manifest, "manifest_bytes": len(m.Data), "queued_entries": len(entries), "entries": []any{}}
	for i, e := range entries {
		if i >= *limit {
			break
		}
		obj, err := store.Get(ctx, e.Location)
		if err != nil {
			fail("get batch %s: %v", e.Location, err)
		}
		payloads, err := buffer.DecodeBatch(obj.Data)
		if err != nil {
			fail("decode batch: %v", err)
		}
		item := map[string]any{"batch_object": e.Location, "batch_object_bytes": len(obj.Data), "payloads_in_batch": len(payloads)}
		if len(e.Metadata) > 0 {
			meta := e.Metadata[0].Payload
			item["metadata_bytes"] = meta
			if len(meta) >= 2 && len(payloads) > 0 {
				item["envelope"] = map[string]any{"version": meta[0], "signal_type": meta[1], "signal": map[byte]string{1: "metrics", 2: "logs", 3: "traces"}[meta[1]], "encoding": meta[2]}
				item["otlp_payload_bytes"] = len(payloads[0])
				var js []byte
				switch meta[1] {
				case 3:
					if td, err := (&ptrace.ProtoUnmarshaler{}).UnmarshalTraces(payloads[0]); err == nil {
						js, _ = (&ptrace.JSONMarshaler{}).MarshalTraces(td)
					}
				case 2:
					if ld, err := (&plog.ProtoUnmarshaler{}).UnmarshalLogs(payloads[0]); err == nil {
						js, _ = (&plog.JSONMarshaler{}).MarshalLogs(ld)
					}
				case 1:
					if md, err := (&pmetric.ProtoUnmarshaler{}).UnmarshalMetrics(payloads[0]); err == nil {
						js, _ = (&pmetric.JSONMarshaler{}).MarshalMetrics(md)
					}
				}
				if js != nil {
					var v any
					_ = json.Unmarshal(js, &v)
					item["otlp_payload_decoded"] = v
				}
			}
		}
		out["entries"] = append(out["entries"].([]any), item)
	}
	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	_ = enc.Encode(out)
}

func fail(f string, a ...any) {
	fmt.Fprintf(os.Stderr, "peek: "+f+"\n", a...)
	os.Exit(1)
}
