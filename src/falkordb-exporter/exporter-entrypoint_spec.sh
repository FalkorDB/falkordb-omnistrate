# shellcheck shell=bash

Describe "exporter-entrypoint.sh"
  setup() {
    temp_dir=$(mktemp -d "${TMPDIR:-/tmp}/exporter-entrypoint-spec.XXXXXX")
    mkdir -p "$temp_dir/bin"

    cat <<'SCRIPT' > "$temp_dir/bin/redis_exporter"
#!/usr/bin/env bash
printf '%s\n' "$@"
SCRIPT
    chmod +x "$temp_dir/bin/redis_exporter"
  }
  BeforeEach 'setup'

  teardown() {
    rm -rf "$temp_dir"
  }
  AfterEach 'teardown'

  It "passes graph memory exclusion flag when running node metrics"
    When run env \
      DATA_DIR="$temp_dir/data" \
      RUN_METRICS=1 \
      PERSISTENCE_AOF_CONFIG=everysec \
      TLS=false \
      PATH="$temp_dir/bin:$PATH" \
      bash ./exporter-entrypoint.sh 6379 9121 1 ""
    The status should be success
    The output should include "Starting Metrics Exporter on 0.0.0.0:9121 for Redis at redis://localhost:6379"
    The output should include "--include-falkordb-graph-memory"
    The output should include "--exclude-falkordb-graph-memory-attrs"
    The output should include "-include-aof-file-size"
  End
End
