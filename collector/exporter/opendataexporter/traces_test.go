package opendataexporter

// Tests for the traces signal added locally on top of upstream v0.5.0.

import (
	"bytes"
	"context"
	"testing"
	"time"

	"github.com/opendata-oss/opendata-go/buffer"
	"github.com/opendata-oss/opendata-go/objstore"
	"go.opentelemetry.io/collector/component"
	"go.opentelemetry.io/collector/component/componenttest"
	"go.opentelemetry.io/collector/exporter"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.uber.org/zap"
	"go.uber.org/zap/zaptest/observer"
)

func testTraces() ptrace.Traces {
	td := ptrace.NewTraces()
	rs := td.ResourceSpans().AppendEmpty()
	rs.Resource().Attributes().PutStr("service.name", "web")
	ss := rs.ScopeSpans().AppendEmpty()
	ss.Scope().SetName("opendataexporter-test")

	root := ss.Spans().AppendEmpty()
	root.SetTraceID(pcommon.TraceID([16]byte{1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16}))
	root.SetSpanID(pcommon.SpanID([8]byte{1, 1, 1, 1, 1, 1, 1, 1}))
	root.SetName("GET /chain")
	root.SetKind(ptrace.SpanKindServer)
	root.SetStartTimestamp(pcommon.Timestamp(1_700_000_000_000_000_000))
	root.SetEndTimestamp(pcommon.Timestamp(1_700_000_000_000_002_500))
	root.Status().SetCode(ptrace.StatusCodeError)
	root.Status().SetMessage("boom")

	child := ss.Spans().AppendEmpty()
	child.SetTraceID(root.TraceID())
	child.SetSpanID(pcommon.SpanID([8]byte{2, 2, 2, 2, 2, 2, 2, 2}))
	child.SetParentSpanID(root.SpanID())
	child.SetName("backend.Process")
	child.SetKind(ptrace.SpanKindClient)
	return td
}

func tracesConfig(manifest string) *Config {
	return &Config{
		ObjectStore:             ObjectStoreConfig{Type: objectStoreTypeS3, Bucket: "b", Region: "us-west-2"},
		DataPathPrefix:          "ingest/otel/traces/data",
		ManifestPath:            manifest,
		FlushInterval:           24 * time.Hour,
		FlushSizeBytes:          1,
		Compression:             compressionNone,
		UploadConcurrency:       1,
		EncodeConcurrency:       1,
		MaxInFlightBatches:      64,
		MaxInFlightBytes:        256 * 1024 * 1024,
		ManifestAppendBatchSize: 1,
	}
}

func TestEncodeMetadataTraces(t *testing.T) {
	got := EncodeMetadata(SignalTypeTraces, PayloadEncodingOTLP)
	want := []byte{MetadataVersion, 3, PayloadEncodingOTLP, 0}
	if !bytes.Equal(got, want) {
		t.Fatalf("EncodeMetadata traces = %v, want %v", got, want)
	}
}

func TestOpenDataExporterConsumeTracesRoundTrip(t *testing.T) {
	store := objstore.NewInMemory()
	cfg := tracesConfig("ingest/otel/traces/manifest")
	exp := newOpenDataExporterForSignal(cfg, SignalTypeTraces)
	exp.storeFactory = func(context.Context, ObjectStoreConfig) (objstore.ObjectStore, error) { return store, nil }

	ctx := context.Background()
	if err := exp.Start(ctx, nil); err != nil {
		t.Fatalf("Start: %v", err)
	}
	t.Cleanup(func() {
		if err := exp.Shutdown(context.Background()); err != nil {
			t.Fatalf("Shutdown: %v", err)
		}
	})

	original := testTraces()
	if err := exp.ConsumeTraces(ctx, original); err != nil {
		t.Fatalf("ConsumeTraces: %v", err)
	}

	manifest, err := store.Get(ctx, cfg.ManifestPath)
	if err != nil {
		t.Fatalf("Get manifest: %v", err)
	}
	entries, err := buffer.DecodeManifestEntries(manifest.Data)
	if err != nil {
		t.Fatalf("DecodeManifestEntries: %v", err)
	}
	if len(entries) != 1 || len(entries[0].Metadata) != 1 {
		t.Fatalf("expected 1 entry with 1 metadata range, got %d entries", len(entries))
	}
	// The envelope must tag the payload as traces so the ingestor routes it to the traces decoder.
	if got, want := entries[0].Metadata[0].Payload, EncodeMetadata(SignalTypeTraces, PayloadEncodingOTLP); !bytes.Equal(got, want) {
		t.Fatalf("metadata payload = %v, want %v", got, want)
	}

	batch, err := store.Get(ctx, entries[0].Location)
	if err != nil {
		t.Fatalf("Get batch: %v", err)
	}
	payloads, err := buffer.DecodeBatch(batch.Data)
	if err != nil || len(payloads) != 1 {
		t.Fatalf("DecodeBatch: payloads=%d err=%v", len(payloads), err)
	}
	roundTripped, err := (&ptrace.ProtoUnmarshaler{}).UnmarshalTraces(payloads[0])
	if err != nil {
		t.Fatalf("UnmarshalTraces: %v", err)
	}
	if got := roundTripped.SpanCount(); got != 2 {
		t.Fatalf("round-tripped span count = %d, want 2", got)
	}
	m := &ptrace.ProtoMarshaler{}
	a, _ := m.MarshalTraces(original)
	b, _ := m.MarshalTraces(roundTripped)
	if !bytes.Equal(a, b) {
		t.Fatal("traces round trip mismatch")
	}
}

func TestOpenDataExporterConsumeTracesBeforeStart(t *testing.T) {
	exp := newOpenDataExporterForSignal(&Config{ObjectStore: ObjectStoreConfig{Type: objectStoreTypeS3}}, SignalTypeTraces)
	if err := exp.ConsumeTraces(context.Background(), testTraces()); err == nil {
		t.Fatal("expected an error before Start")
	}
}

func TestConsumeTracesRejectedOnOtherSignalsAndViceVersa(t *testing.T) {
	logsExp := newOpenDataExporterForSignal(&Config{ObjectStore: ObjectStoreConfig{Type: objectStoreTypeS3}}, SignalTypeLogs)
	if err := logsExp.ConsumeTraces(context.Background(), testTraces()); err == nil {
		t.Fatal("ConsumeTraces on a logs exporter must error")
	}
	tracesExp := newOpenDataExporterForSignal(&Config{ObjectStore: ObjectStoreConfig{Type: objectStoreTypeS3}}, SignalTypeTraces)
	if err := tracesExp.ConsumeLogs(context.Background(), testLogs()); err == nil {
		t.Fatal("ConsumeLogs on a traces exporter must error")
	}
	if err := tracesExp.ConsumeMetrics(context.Background(), testMetrics()); err == nil {
		t.Fatal("ConsumeMetrics on a traces exporter must error")
	}
}

func TestFactoryRegistersTracesAndCreatesExporter(t *testing.T) {
	f := NewFactory()
	if got := f.TracesStability(); got != component.StabilityLevelAlpha {
		t.Fatalf("traces stability = %s, want alpha", got)
	}
	exp, err := f.CreateTraces(context.Background(), exporter.Settings{
		ID:                component.NewID(componentType),
		TelemetrySettings: componenttest.NewNopTelemetrySettings(),
	}, createDefaultConfig())
	if err != nil || exp == nil {
		t.Fatalf("CreateTraces: exp=%v err=%v", exp, err)
	}
}

func TestApplyTracesPathDefaultsSwapsOnlyMetricsDefaultsAndWarns(t *testing.T) {
	core, logs := observer.New(zap.WarnLevel)
	c := createDefaultConfig().(*Config) // metrics-flavoured defaults
	applyTracesPathDefaults(c, zap.New(core))
	if c.DataPathPrefix != defaultTracesDataPathPrefix || c.ManifestPath != defaultTracesManifestPath {
		t.Fatalf("defaults not swapped: %q %q", c.DataPathPrefix, c.ManifestPath)
	}
	if logs.Len() != 2 {
		t.Fatalf("expected 2 warnings, got %d", logs.Len())
	}

	custom := createDefaultConfig().(*Config)
	custom.DataPathPrefix, custom.ManifestPath = "my/data", "my/manifest"
	core2, logs2 := observer.New(zap.WarnLevel)
	applyTracesPathDefaults(custom, zap.New(core2))
	if custom.DataPathPrefix != "my/data" || custom.ManifestPath != "my/manifest" || logs2.Len() != 0 {
		t.Fatal("explicit paths must be left untouched and silent")
	}
}

func TestCreateTracesExporterDoesNotMutateSharedConfig(t *testing.T) {
	cfg := createDefaultConfig().(*Config)
	f := NewFactory()
	if _, err := f.CreateTraces(context.Background(), exporter.Settings{
		ID:                component.NewID(componentType),
		TelemetrySettings: componenttest.NewNopTelemetrySettings(),
	}, cfg); err != nil {
		t.Fatalf("CreateTraces: %v", err)
	}
	if cfg.DataPathPrefix != defaultMetricsDataPathPrefix || cfg.ManifestPath != defaultMetricsManifestPath {
		t.Fatal("createTracesExporter mutated the shared config (a later metrics exporter would inherit traces paths)")
	}
}
