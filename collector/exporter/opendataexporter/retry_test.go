package opendataexporter

// Tests for the retry_on_failure setting added locally on top of upstream v0.5.0.

import (
	"testing"
	"time"

	"go.opentelemetry.io/collector/config/configretry"
)

func TestDefaultConfigRetriesFailedExports(t *testing.T) {
	cfg := createDefaultConfig().(*Config)
	if !cfg.RetryOnFailure.Enabled {
		t.Fatal("retry_on_failure must be enabled by default: dropping a batch on a transient object-store error loses data")
	}
	if cfg.RetryOnFailure.MaxElapsedTime < time.Minute {
		t.Fatalf("default max_elapsed_time %s is too short to ride out an object-store blip", cfg.RetryOnFailure.MaxElapsedTime)
	}
	cfg.ObjectStore.Bucket, cfg.ObjectStore.Region = "b", "us-west-2" // the defaults leave these to the user
	if err := cfg.Validate(); err != nil {
		t.Fatalf("default retry settings must validate: %v", err)
	}
}

func TestInvalidRetryConfigIsRejected(t *testing.T) {
	cfg := createDefaultConfig().(*Config)
	cfg.ObjectStore.Bucket, cfg.ObjectStore.Region = "b", "us-west-2"
	cfg.RetryOnFailure = configretry.BackOffConfig{Enabled: true, InitialInterval: -1}
	if err := cfg.Validate(); err == nil {
		t.Fatal("expected a negative initial_interval to be rejected")
	}
}
