# Local observability stack: lifecycle, reset and teardown.
#
#   make help          list the targets
#   make start / stop  run state: start the containers; stop them (containers, volumes and data are kept)
#   make status        containers (also stopped ones) and the row count of every table
#   make setup         from nothing to ready: start the stack and provision HyperDX (idempotent)
#   make teardown      remove everything: containers, network, volumes and generated state (asks first; IMAGES=1 also removes images)
#   make reset         wipe all telemetry data, then rebuild the HyperDX content (asks first; YES=1 skips the prompt)
#   make reset-data    empty the Buffer (S3 bucket), the ClickHouse tables (logs, traces, metrics) and the webhook sink list
#   make reset-setup   wipe HyperDX's own store and re-create dashboards, alerts and saved searches
#   make provision     (re)create the HyperDX content only; idempotent, wipes nothing
#
# Runs against the compose project "obs" in stack/ (Docker; set DOCKER=podman for Podman).
# Local learning stack only: it deletes data. Do not point it at anything else.

DOCKER  ?= docker
PROJECT ?= obs
COMPOSE  = $(DOCKER) compose --project-directory stack -f stack/compose.yml -p $(PROJECT) --profile app
CH_USER ?= ingestor
CH_PASS ?= ingestor
BUCKET  ?= observability-buffer
TABLES   = otel_logs otel_traces otel_metrics_gauge otel_metrics_sum otel_metrics_histogram
# Services that write to the Buffer or read from it, plus the webhook sink, whose list of received alert
# deliveries lives in its memory. All are stopped while the data is wiped and started again afterwards.
PIPELINE = web backend otel-collector ingestor-logs ingestor-traces ingestor-metrics webhook-sink

.DEFAULT_GOAL := help
# The destructive steps must run in order, after the confirmation, even under `make -j`.
.NOTPARALLEL:
.PHONY: help start stop status confirm setup teardown provision reset reset-data reset-setup stop-pipeline wipe-buffer truncate-tables start-pipeline

help:
	@echo "make start         start the stack and the test app"
	@echo "make stop          stop the containers; keeps containers, volumes and data"
	@echo "make setup         start the stack and provision HyperDX (from nothing to ready)"
	@echo "make teardown      remove containers, network, volumes and generated state (asks first; IMAGES=1 also removes images)"
	@echo "make status        show the containers and the row count of every table"
	@echo "make reset         reset-data, then reset-setup (asks first; YES=1 skips the prompt)"
	@echo "make reset-data    empty the Buffer, the ClickHouse tables (logs, traces, metrics) and the webhook sink list"
	@echo "make reset-setup   wipe HyperDX and re-create dashboards, alerts and saved searches"
	@echo "make provision     re-create the HyperDX content only (idempotent, wipes nothing)"

start:
	$(COMPOSE) up -d

stop:
	$(COMPOSE) stop

# The images are built beforehand (stack/README.md, "Run it"). setup does not build them.
setup: start provision

# Everything the stack created: containers, the compose network, the three volumes (all data and the HyperDX
# store) and the ids provision.py cached. IMAGES=1 also removes the images the services use, built and pulled.
teardown: WHAT = This removes the containers, network and volumes of the local stack, so all telemetry data and the HyperDX content are lost (IMAGES=1 also removes the images).
teardown: confirm
	$(COMPOSE) down --volumes --remove-orphans $(if $(filter 1,$(IMAGES)),--rmi all)
	rm -f stack/hyperdx/state.json

status:
	$(COMPOSE) ps -a --format 'table {{.Name}}\t{{.Status}}'
	@for t in $(TABLES); do \
	  printf '%-28s' $$t; $(COMPOSE) exec -T clickhouse clickhouse-client --user $(CH_USER) --password $(CH_PASS) -q "SELECT count() FROM otel.$$t" 2>/dev/null || echo "(not reachable)"; \
	done

# Guard for the destructive targets. Run with YES=1 to skip the question.
# WHAT says what the calling target deletes.
confirm:
ifneq ($(YES),1)
	@printf '$(WHAT) Continue? [y/N] ' && read a && case "$$a" in y|Y|yes) ;; *) false;; esac || { echo aborted; exit 1; }
endif

reset: WHAT = This deletes all telemetry data and rebuilds the HyperDX content (dashboards, alerts, saved searches, and the HyperDX user) of the local stack.
reset: confirm
	@$(MAKE) --no-print-directory YES=1 reset-data
	@$(MAKE) --no-print-directory YES=1 reset-setup
	@echo "reset complete: empty buffer and tables, fresh HyperDX content"

# ---- data: Buffer and ClickHouse ------------------------------------------------------------

reset-data: WHAT = This deletes all telemetry data (the Buffer and the ClickHouse tables) of the local stack.
reset-data: confirm stop-pipeline wipe-buffer truncate-tables start-pipeline
	@echo "data reset: Buffer bucket '$(BUCKET)' and tables $(TABLES) are empty"

stop-pipeline:
	$(COMPOSE) stop $(PIPELINE)

# The Buffer queue and its manifests live under the bucket; an empty bucket is the state of a fresh stack.
wipe-buffer:
	$(COMPOSE) up -d --wait s3
	$(COMPOSE) run --rm --no-deps --entrypoint aws s3-init --endpoint-url http://s3:9000 s3 rm s3://$(BUCKET) --recursive

truncate-tables:
	$(COMPOSE) up -d --wait clickhouse
	@for t in $(TABLES); do \
	  echo "truncate otel.$$t"; \
	  $(COMPOSE) exec -T clickhouse clickhouse-client --user $(CH_USER) --password $(CH_PASS) -q "TRUNCATE TABLE otel.$$t" || exit 1; \
	done

# Starting the app again also restarts its cumulative metric counters from zero.
start-pipeline:
	$(COMPOSE) up -d $(PIPELINE)

# ---- setup: HyperDX dashboards, alerts, saved searches --------------------------------------

# HyperDX keeps users, sources, dashboards, alerts and saved searches in Mongo. Dropping that volume and
# running provision.py again gives the content exactly as the script defines it (it also creates the dev user
# and waits for HyperDX to come up).
reset-setup: WHAT = This wipes HyperDX (its user, dashboards, alerts and saved searches) and recreates the content.
reset-setup: confirm
	$(COMPOSE) stop hyperdx mongo
	$(COMPOSE) rm -f hyperdx mongo
	$(DOCKER) volume rm $(PROJECT)_mongo-data 2>/dev/null || ! $(DOCKER) volume inspect $(PROJECT)_mongo-data >/dev/null 2>&1
	$(COMPOSE) up -d mongo hyperdx
	python3 stack/hyperdx/provision.py

provision:
	python3 stack/hyperdx/provision.py
