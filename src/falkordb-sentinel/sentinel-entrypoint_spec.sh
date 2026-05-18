# shellcheck shell=bash

Describe "sentinel-entrypoint.sh helpers"
  Include ./sentinel-entrypoint.sh

  setup() {
    temp_dir=$(mktemp -d "${TMPDIR:-/tmp}/sentinel-entrypoint-spec.XXXXXX")
    DATA_DIR="$temp_dir/runtime/data"
    mkdir -p "$DATA_DIR"
    SENTINEL_CONF_FILE="$DATA_DIR/sentinel.conf"
    : > "$SENTINEL_CONF_FILE"
    NODE_HOST="sentinel-rp-0.instance-new.hc-new.us-central1.gcp.beef.cloud"
    TLS=false
    INSTANCE_ID=""
    DNS_SUFFIX=""
    ADMIN_PASSWORD="testpass"
    sleep() {
      SECONDS=$((SECONDS + 301))
    }

    sed() {
      if [[ "$1" == "-i" && "$2" == "-E" ]]; then
        perl -0pi -e "$3" "$4"
      elif [[ "$1" == "-i" ]]; then
        # Handle sed -i '/pattern/d' file (delete lines)
        if [[ "$2" =~ ^/ ]]; then
          perl -ni -e "print unless $2" "$3"
        else
          /usr/bin/sed "$@"
        fi
      else
        /usr/bin/sed "$@"
      fi
    }

    unset -f getent
  }
  BeforeEach 'setup'

  teardown() {
    rm -rf "$temp_dir"
    unset DATA_DIR SENTINEL_CONF_FILE NODE_HOST TLS INSTANCE_ID DNS_SUFFIX ADMIN_PASSWORD
    unset -f getent sed sleep
  }
  AfterEach 'teardown'

  Describe "read_secret_or_env()"
    It "reads value from a secret file when present"
      echo -n "secret_val" > "$temp_dir/secret_file"
      When call read_secret_or_env "$temp_dir/secret_file" "UNUSED_ENV"
      The status should be success
      The output should eq "secret_val"
    End

    It "falls back to environment variable when secret file missing"
      MY_TEST_VAR="env_val"
      When call read_secret_or_env "/nonexistent/path" "MY_TEST_VAR"
      The status should be success
      The output should eq "env_val"
      unset MY_TEST_VAR
    End

    It "returns empty when neither exists"
      When call read_secret_or_env "/nonexistent/path" "TOTALLY_MISSING_VAR"
      The status should be success
      The output should eq ""
    End

    It "ignores an empty secret file and falls back to env var"
      : > "$temp_dir/empty_secret"
      FALLBACK_VAR="fallback"
      When call read_secret_or_env "$temp_dir/empty_secret" "FALLBACK_VAR"
      The status should be success
      The output should eq "fallback"
      unset FALLBACK_VAR
    End
  End

  Describe "resolve_host_ip()"
    It "returns a literal IP without DNS lookup"
      When call resolve_host_ip "10.0.0.42"
      The status should be success
      The output should eq "10.0.0.42"
    End

    It "resolves a hostname via getent"
      getent() {
        if [[ "$2" == "sentinel.example.com" ]]; then
          echo "10.0.0.99 sentinel.example.com"
        else
          return 1
        fi
      }

      When call resolve_host_ip "sentinel.example.com"
      The status should be success
      The output should eq "10.0.0.99"
    End

    It "times out when a hostname never resolves"
      getent() { return 1; }
      sleep() { SECONDS=$((SECONDS + 301)); }

      When run resolve_host_ip "unreachable.host" "sentinel peer" 0
      The status should be failure
      The stderr should include "Timed out trying to resolve ip for sentinel peer: unreachable.host"
    End
  End

  Describe "fix_namespace_in_config_files()"
    It "rewrites namespace in sentinel.conf"
      INSTANCE_ID="instance-new"

      cat <<'EOF' > "$SENTINEL_CONF_FILE"
sentinel monitor master node-rp-0.instance-old.hc-old.us-central1.gcp.deadbeef.cloud 6379 2
sentinel known-replica master node-rp-1.instance-old.hc-old.us-central1.gcp.deadbeef.cloud 6379
EOF

      When call fix_namespace_in_config_files
      The status should be success
      The output should include "Current namespace: instance-new"
      The output should include "Checking sentinel.conf for namespace mismatches"
      The contents of file "$SENTINEL_CONF_FILE" should include "node-rp-0.instance-new"
      The contents of file "$SENTINEL_CONF_FILE" should include "node-rp-1.instance-new"
      The contents of file "$SENTINEL_CONF_FILE" should not include "instance-old"
    End

    It "rewrites DNS suffix in sentinel.conf"
      DNS_SUFFIX="hc-new.us-central1.gcp.beef.cloud"

      cat <<'EOF' > "$SENTINEL_CONF_FILE"
sentinel monitor master node-rp-0.instance-abc.hc-old.us-central1.gcp.deadbeef.cloud 6379 2
sentinel known-sentinel master sentinel-rp-0.instance-abc.hc-old.us-central1.gcp.deadbeef.cloud 26379 abc123
EOF

      When call fix_namespace_in_config_files
      The status should be success
      The output should include "Current DNS suffix: hc-new.us-central1.gcp.beef.cloud"
      The contents of file "$SENTINEL_CONF_FILE" should include "node-rp-0.instance-abc.hc-new.us-central1.gcp.beef.cloud"
      The contents of file "$SENTINEL_CONF_FILE" should include "sentinel-rp-0.instance-abc.hc-new.us-central1.gcp.beef.cloud"
      The contents of file "$SENTINEL_CONF_FILE" should not include "deadbeef"
    End

    It "rewrites both namespace and DNS suffix together"
      INSTANCE_ID="instance-new"
      DNS_SUFFIX="hc-new.us-central1.gcp.beef.cloud"

      cat <<'EOF' > "$SENTINEL_CONF_FILE"
sentinel monitor master node-rp-0.instance-old.hc-old.us-central1.gcp.deadbeef.cloud 6379 2
EOF

      When call fix_namespace_in_config_files
      The status should be success
      The output should include "Current namespace: instance-new"
      The contents of file "$SENTINEL_CONF_FILE" should include "node-rp-0.instance-new.hc-new.us-central1.gcp.beef.cloud"
    End

    It "skips when INSTANCE_ID and DNS_SUFFIX are not set"
      INSTANCE_ID=""
      DNS_SUFFIX=""

      When call fix_namespace_in_config_files
      The status should be success
      The output should include "INSTANCE_ID not set, skipping namespace fix"
      The output should include "DNS_SUFFIX not set, skipping DNS suffix fix"
    End

    It "does not modify node.conf (sentinel scope only)"
      INSTANCE_ID="instance-new"
      local node_conf="$DATA_DIR/node.conf"
      echo "cluster-announce-hostname node-rp-0.instance-old.hc-old.us-central1.gcp.deadbeef.cloud" > "$node_conf"

      When call fix_namespace_in_config_files
      The status should be success
      The output should include "Current namespace: instance-new"
      # node.conf should remain untouched by sentinel's fix_namespace_in_config_files
      The contents of file "$node_conf" should include "instance-old"
    End
  End

  Describe "strip_stale_sentinel_state()"
    It "removes known-replica and known-sentinel lines"
      cat <<'EOF' > "$SENTINEL_CONF_FILE"
sentinel monitor master node-rp-0.instance-new.hc-new.us-central1.gcp.beef.cloud 6379 2
sentinel known-replica master 10.0.0.2 6379
sentinel known-replica master 10.0.0.3 6379
sentinel known-sentinel master 10.0.0.4 26379 abc123def456
sentinel auth-pass master testpass
EOF

      When call strip_stale_sentinel_state
      The status should be success
      The output should include "Stripping stale sentinel state"
      The contents of file "$SENTINEL_CONF_FILE" should include "sentinel monitor master"
      The contents of file "$SENTINEL_CONF_FILE" should include "sentinel auth-pass master"
      The contents of file "$SENTINEL_CONF_FILE" should not include "known-replica"
      The contents of file "$SENTINEL_CONF_FILE" should not include "known-sentinel"
    End

    It "is safe when sentinel.conf has no stale entries"
      cat <<'EOF' > "$SENTINEL_CONF_FILE"
sentinel monitor master node-rp-0.instance-new.hc-new.us-central1.gcp.beef.cloud 6379 2
sentinel auth-pass master testpass
EOF

      When call strip_stale_sentinel_state
      The status should be success
      The output should include "Stripping stale sentinel state"
      The contents of file "$SENTINEL_CONF_FILE" should include "sentinel monitor master"
      The contents of file "$SENTINEL_CONF_FILE" should include "sentinel auth-pass master"
    End

    It "does nothing when sentinel.conf does not exist"
      rm -f "$SENTINEL_CONF_FILE"
      When call strip_stale_sentinel_state
      The status should be success
    End

    It "handles file containing only stale entries"
      cat <<'EOF' > "$SENTINEL_CONF_FILE"
sentinel known-replica master 10.0.0.2 6379
sentinel known-sentinel master 10.0.0.3 26379 abc123
EOF

      When call strip_stale_sentinel_state
      The status should be success
      The output should include "Stripping stale sentinel state"
      The contents of file "$SENTINEL_CONF_FILE" should not include "known-replica"
      The contents of file "$SENTINEL_CONF_FILE" should not include "known-sentinel"
    End
  End

  Describe "create_tls_rotation_job_script()"
    It "rebuilds the combined CA bundle before restarting sentinel"
      TLS=true
      RUN_SENTINEL=1
      BASE_ROOT_CA_PATH="/etc/ssl/certs/ca-certificates.crt"
      TLS_MOUNT_PATH="/etc/tls"
      SELFSIGNED_CA_PATH="$TLS_MOUNT_PATH/selfsigned-ca.crt"
      COMBINED_CA_PATH="$DATA_DIR/selfsigned-tls-combined.pem"

      When call create_tls_rotation_job_script
      The status should be success
      The output should include "Creating sentinel certificate rotation job."
      The contents of file "$DATA_DIR/cert_rotate_sentinel.sh" should include "cat \"$BASE_ROOT_CA_PATH\" \"$SELFSIGNED_CA_PATH\" > \"$COMBINED_CA_PATH\""
      The contents of file "$DATA_DIR/cert_rotate_sentinel.sh" should include "supervisorctl -c $DATA_DIR/supervisord.conf restart redis-sentinel"
    End
  End

  Describe "full namespace+DNS+stale restore pipeline"
    It "rewrites namespace, DNS suffix, and strips stale state in correct order"
      INSTANCE_ID="instance-new"
      DNS_SUFFIX="hc-new.us-central1.gcp.beef.cloud"

      cat <<'EOF' > "$SENTINEL_CONF_FILE"
sentinel monitor master node-rp-0.instance-old.hc-old.us-central1.gcp.deadbeef.cloud 6379 2
sentinel known-replica master 192.168.1.2 6379
sentinel known-sentinel master 192.168.1.3 26379 abc123
sentinel auth-pass master oldpass
EOF

      # Simulate the startup sequence: fix_namespace → strip_stale
      When call fix_namespace_in_config_files
      The status should be success
      The output should include "Current namespace: instance-new"
    End

    It "then strips stale state from rewritten config"
      # This test verifies strip_stale works on a conf that already went through namespace fix
      INSTANCE_ID="instance-new"
      DNS_SUFFIX="hc-new.us-central1.gcp.beef.cloud"

      cat <<'EOF' > "$SENTINEL_CONF_FILE"
sentinel monitor master node-rp-0.instance-new.hc-new.us-central1.gcp.beef.cloud 6379 2
sentinel known-replica master 192.168.1.2 6379
sentinel known-sentinel master 192.168.1.3 26379 abc123
sentinel auth-pass master testpass
EOF

      When call strip_stale_sentinel_state
      The status should be success
      The output should include "Stripping stale sentinel state"
      The contents of file "$SENTINEL_CONF_FILE" should include "node-rp-0.instance-new.hc-new.us-central1.gcp.beef.cloud"
      The contents of file "$SENTINEL_CONF_FILE" should include "sentinel auth-pass master"
      The contents of file "$SENTINEL_CONF_FILE" should not include "known-replica"
      The contents of file "$SENTINEL_CONF_FILE" should not include "known-sentinel"
    End
  End

  Describe "prepare_data_dir()"
    # Helper: mirrors prepare_data_dir logic but uses FAKE_VOLUME instead of /data
    # so we can test volume-mount behaviour without root access.
    _testable_prepare_data_dir() {
      if [[ $(basename "$DATA_DIR") != 'data' ]]; then
        DATA_DIR="$DATA_DIR/data"
      fi
      if [[ "$DATA_DIR" == "$FAKE_VOLUME" ]]; then
        return
      fi
      mkdir -p "$(dirname "$DATA_DIR")"
      if [[ -d "$FAKE_VOLUME" ]] && [[ ! -e "$DATA_DIR" ]]; then
        ln -s "$FAKE_VOLUME" "$DATA_DIR"
      elif [[ ! -e "$DATA_DIR" ]]; then
        mkdir -p "$DATA_DIR"
      fi
    }

    _readlink_data_dir() { readlink "$DATA_DIR"; }
    _is_symlink() { test -L "$1" && echo "yes" || echo "no"; }
    _path_exists() { test -e "$1" && echo "yes" || echo "no"; }

    # --- basename normalisation ---

    It "appends /data when basename is not data"
      DATA_DIR="$temp_dir/newdir"

      When call prepare_data_dir
      The status should be success
      The variable DATA_DIR should eq "${temp_dir}/newdir/data"
    End

    It "keeps DATA_DIR unchanged when basename is already data"
      DATA_DIR="$temp_dir/otherparent/data"
      mkdir -p "$DATA_DIR"

      When call prepare_data_dir
      The status should be success
      The variable DATA_DIR should eq "${temp_dir}/otherparent/data"
    End

    It "normalises the production compose value /var/lib/falkordb/data"
      DATA_DIR="$temp_dir/var/lib/falkordb/data"

      When call prepare_data_dir
      The status should be success
      The variable DATA_DIR should eq "${temp_dir}/var/lib/falkordb/data"
    End

    # --- /data short-circuit ---

    It "returns immediately when DATA_DIR is /data"
      DATA_DIR="/data"

      When call prepare_data_dir
      The status should be success
      The variable DATA_DIR should eq "/data"
    End

    # --- symlink creation (volume present) ---

    It "creates symlink DATA_DIR -> volume when volume mount exists"
      FAKE_VOLUME="$temp_dir/volume"
      mkdir -p "$FAKE_VOLUME"
      DATA_DIR="$temp_dir/falkordb/data"

      When call _testable_prepare_data_dir
      The status should be success
      The variable DATA_DIR should eq "${temp_dir}/falkordb/data"
      The path "$DATA_DIR" should be symlink
    End

    It "symlink target resolves to the volume mount"
      FAKE_VOLUME="$temp_dir/volume"
      mkdir -p "$FAKE_VOLUME"
      DATA_DIR="$temp_dir/falkordb/data"
      _testable_prepare_data_dir

      When call _readlink_data_dir
      The status should be success
      The output should eq "$FAKE_VOLUME"
    End

    It "files written through DATA_DIR land on the volume"
      FAKE_VOLUME="$temp_dir/volume"
      mkdir -p "$FAKE_VOLUME"
      DATA_DIR="$temp_dir/falkordb/data"
      _testable_prepare_data_dir
      echo "test-payload" > "$DATA_DIR/probe.txt"

      When call cat "$FAKE_VOLUME/probe.txt"
      The status should be success
      The output should eq "test-payload"
    End

    It "creates parent directories that do not exist yet"
      FAKE_VOLUME="$temp_dir/volume"
      mkdir -p "$FAKE_VOLUME"
      DATA_DIR="$temp_dir/deep/nested/path/data"

      When call _testable_prepare_data_dir
      The status should be success
      The path "${temp_dir}/deep/nested/path" should be directory
      The path "$DATA_DIR" should be symlink
    End

    # --- fallback (no volume) ---

    It "creates a real directory when no volume mount exists"
      FAKE_VOLUME="$temp_dir/nonexistent_volume"
      DATA_DIR="$temp_dir/falkordb/data"

      When call _testable_prepare_data_dir
      The status should be success
      The path "$DATA_DIR" should be directory
    End

    It "fallback directory is not a symlink"
      FAKE_VOLUME="$temp_dir/nonexistent_volume"
      DATA_DIR="$temp_dir/falkordb/data"
      _testable_prepare_data_dir

      When call _is_symlink "$DATA_DIR"
      The output should eq "no"
    End

    # --- idempotency ---

    It "leaves an existing directory untouched"
      FAKE_VOLUME="$temp_dir/volume"
      mkdir -p "$FAKE_VOLUME"
      DATA_DIR="$temp_dir/falkordb/data"
      mkdir -p "$DATA_DIR"
      echo "existing" > "$DATA_DIR/existing.txt"

      When call _testable_prepare_data_dir
      The status should be success
      The contents of file "$DATA_DIR/existing.txt" should eq "existing"
    End

    It "existing directory is not replaced by symlink"
      FAKE_VOLUME="$temp_dir/volume"
      mkdir -p "$FAKE_VOLUME"
      DATA_DIR="$temp_dir/falkordb/data"
      mkdir -p "$DATA_DIR"
      _testable_prepare_data_dir

      When call _is_symlink "$DATA_DIR"
      The output should eq "no"
    End

    It "leaves an existing symlink untouched"
      FAKE_VOLUME="$temp_dir/volume"
      mkdir -p "$FAKE_VOLUME"
      DATA_DIR="$temp_dir/falkordb/data"
      mkdir -p "$(dirname "$DATA_DIR")"
      ln -s "$FAKE_VOLUME" "$DATA_DIR"

      When call _testable_prepare_data_dir
      The status should be success
      The path "$DATA_DIR" should be symlink
    End

    It "existing symlink target is preserved"
      FAKE_VOLUME="$temp_dir/volume"
      mkdir -p "$FAKE_VOLUME"
      DATA_DIR="$temp_dir/falkordb/data"
      mkdir -p "$(dirname "$DATA_DIR")"
      ln -s "$FAKE_VOLUME" "$DATA_DIR"
      _testable_prepare_data_dir

      When call _readlink_data_dir
      The output should eq "$FAKE_VOLUME"
    End

    # --- regression: old bug created DATA_DIR as regular dir before symlinking ---

    It "REGRESSION: DATA_DIR is a symlink (not regular dir) when volume exists"
      FAKE_VOLUME="$temp_dir/volume"
      mkdir -p "$FAKE_VOLUME"
      DATA_DIR="$temp_dir/falkordb/data"

      When call _testable_prepare_data_dir
      The status should be success
      The path "$DATA_DIR" should be symlink
    End

    It "REGRESSION: no nested data/data child symlink exists"
      FAKE_VOLUME="$temp_dir/volume"
      mkdir -p "$FAKE_VOLUME"
      DATA_DIR="$temp_dir/falkordb/data"
      _testable_prepare_data_dir

      When call _path_exists "$DATA_DIR/data"
      The output should eq "no"
    End

    It "REGRESSION: only parent dir is created, not DATA_DIR itself before symlink"
      FAKE_VOLUME="$temp_dir/volume"
      mkdir -p "$FAKE_VOLUME"
      DATA_DIR="$temp_dir/lib/falkordb/data"
      _testable_prepare_data_dir

      When call _is_symlink "${temp_dir}/lib/falkordb"
      The output should eq "no"
    End

    It "REGRESSION: DATA_DIR itself is the symlink, not its parent"
      FAKE_VOLUME="$temp_dir/volume"
      mkdir -p "$FAKE_VOLUME"
      DATA_DIR="$temp_dir/lib/falkordb/data"

      When call _testable_prepare_data_dir
      The status should be success
      The path "${temp_dir}/lib/falkordb" should be directory
      The path "$DATA_DIR" should be symlink
    End
  End

  Describe "log()"
    It "outputs message when DEBUG is 1"
      DEBUG=1
      When call log "test message"
      The status should be success
      The output should eq "test message"
    End

    It "is silent when DEBUG is 0"
      DEBUG=0
      When call log "test message"
      The status should be success
      The output should eq ""
    End
  End
End
