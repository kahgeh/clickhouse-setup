package opendataexporter

import "go.opentelemetry.io/collector/component"

const (
	componentTypeStr = "opendata"

	// MetadataVersion is the version byte for OpenData queue metadata payloads.
	MetadataVersion uint8 = 1
	// SignalTypeMetrics identifies metrics payloads in OpenData queue metadata.
	SignalTypeMetrics uint8 = 1
	// SignalTypeLogs identifies logs payloads in OpenData queue metadata.
	SignalTypeLogs uint8 = 2
	// SignalTypeTraces identifies traces payloads in OpenData queue metadata.
	// (Local patch: upstream v0.5.0 supports only metrics and logs. The value
	// matches SignalType::Traces in opendata-contrib's ingest envelope.)
	SignalTypeTraces uint8 = 3
	// PayloadEncodingOTLP identifies OTLP protobuf-encoded payloads.
	PayloadEncodingOTLP uint8 = 1
)

var componentType = component.MustNewType(componentTypeStr)

// EncodeMetadata encodes the OpenData queue metadata header.
func EncodeMetadata(signalType, encoding uint8) []byte {
	return []byte{MetadataVersion, signalType, encoding, 0}
}
