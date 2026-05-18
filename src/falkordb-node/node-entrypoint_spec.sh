# shellcheck shell=bash

Describe "node-entrypoint.sh helpers"
  Include ./node-entrypoint.sh

  setup() {
    temp_dir=$(mktemp -d "${TMPDIR:-/tmp}/node-entrypoint-spec.XXXXXX")
    DATA_DIR="$temp_dir/runtime/data"
    mkdir -p "$DATA_DIR"
    NODE_CONF_FILE="$DATA_DIR/node.conf"
    : > "$NODE_CONF_FILE"
    NODE_HOST="node-rp-0.instance-new.hc-new.us-central1.gcp.beef.cloud"
    NODE_PORT=6379
    TLS=false
    INSTANCE_ID=""
    DNS_SUFFIX=""
    ADMIN_PASSWORD="testpass"
    PERSISTENCE_RDB_CONFIG_INPUT="low"
    PERSISTENCE_RDB_CONFIG=""
    MEMORY_LIMIT=""
    FALKORDB_QUERY_MEM_CAPACITY=0
    FALKORDB_TIMEOUT_MAX=0
    FALKORDB_TIMEOUT_DEFAULT=0
    sleep() {
      SECONDS=$((SECONDS + 301))
    }

    sed() {
      if [[ "$1" == "-i" && "$2" == "-E" ]]; then
        perl -0pi -e "$3" "$4"
      else
        /usr/bin/sed "$@"
      fi
    }

    unset -f getent
  }
  BeforeEach 'setup'

  teardown() {
    rm -rf "$temp_dir"
    unset DATA_DIR NODE_CONF_FILE NODE_HOST NODE_PORT TLS INSTANCE_ID DNS_SUFFIX
    unset ADMIN_PASSWORD PERSISTENCE_RDB_CONFIG_INPUT PERSISTENCE_RDB_CONFIG MEMORY_LIMIT
    unset FALKORDB_QUERY_MEM_CAPACITY FALKORDB_TIMEOUT_MAX FALKORDB_TIMEOUT_DEFAULT
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

    It "returns empty when neither secret file nor env var exists"
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
        if [[ "$2" == "myhost.example.com" ]]; then
          echo "10.0.0.99 myhost.example.com"
        else
          return 1
        fi
      }

      When call resolve_host_ip "myhost.example.com"
      The status should be success
      The output should eq "10.0.0.99"
    End

    It "times out when a hostname never resolves"
      getent() { return 1; }
      sleep() { SECONDS=$((SECONDS + 301)); }

      When run resolve_host_ip "unresolvable.host" "peer node" 0
      The status should be failure
      The stderr should include "Timed out trying to resolve ip for peer node: unresolvable.host"
    End
  End

  Describe "normalize_optional_config_values()"
    It "converts <nil> values to 0"
      FALKORDB_QUERY_MEM_CAPACITY="<nil>"
      FALKORDB_TIMEOUT_MAX="<nil>"
      FALKORDB_TIMEOUT_DEFAULT="<nil>"

      When call normalize_optional_config_values
      The status should be success
      The variable FALKORDB_QUERY_MEM_CAPACITY should eq "0"
      The variable FALKORDB_TIMEOUT_MAX should eq "0"
      The variable FALKORDB_TIMEOUT_DEFAULT should eq "0"
    End

    It "leaves numeric values unchanged"
      FALKORDB_QUERY_MEM_CAPACITY=100
      FALKORDB_TIMEOUT_MAX=200
      FALKORDB_TIMEOUT_DEFAULT=300

      When call normalize_optional_config_values
      The status should be success
      The variable FALKORDB_QUERY_MEM_CAPACITY should eq "100"
      The variable FALKORDB_TIMEOUT_MAX should eq "200"
      The variable FALKORDB_TIMEOUT_DEFAULT should eq "300"
    End
  End

  Describe "create_tls_rotation_job_script()"
    It "rebuilds the combined CA bundle before reloading TLS config"
      TLS=true
      RUN_NODE=1
      ROOT_CA_PATH="$DATA_DIR/selfsigned-tls-combined.pem"
      BASE_ROOT_CA_PATH="/etc/ssl/certs/ca-certificates.crt"
      TLS_MOUNT_PATH="/etc/tls"
      SELFSIGNED_CA_PATH="$TLS_MOUNT_PATH/selfsigned-ca.crt"
      COMBINED_CA_PATH="$DATA_DIR/selfsigned-tls-combined.pem"
      TLS_CONNECTION_STRING="--tls --cacert $ROOT_CA_PATH"

      When call create_tls_rotation_job_script
      The status should be success
      The contents of file "$DATA_DIR/cert_rotate_node.sh" should include "cat \"$BASE_ROOT_CA_PATH\" \"$SELFSIGNED_CA_PATH\" > \"$COMBINED_CA_PATH\""
      The contents of file "$DATA_DIR/cert_rotate_node.sh" should include "TLS_CONNECTION_STRING=\"--tls --cacert \$tls_ca_path\""
      The contents of file "$DATA_DIR/cert_rotate_node.sh" should include "CONFIG SET tls-ca-cert-file \$tls_ca_path"
    End
  End

  Describe "set_persistence_config()"
    It "sets low persistence config"
      PERSISTENCE_RDB_CONFIG_INPUT="low"
      When call set_persistence_config
      The status should be success
      The variable PERSISTENCE_RDB_CONFIG should eq "86400 1 21600 100 3600 10000"
    End

    It "sets medium persistence config"
      PERSISTENCE_RDB_CONFIG_INPUT="medium"
      When call set_persistence_config
      The status should be success
      The variable PERSISTENCE_RDB_CONFIG should eq "21600 1 3600 100 300 10000"
    End

    It "sets high persistence config"
      PERSISTENCE_RDB_CONFIG_INPUT="high"
      When call set_persistence_config
      The status should be success
      The variable PERSISTENCE_RDB_CONFIG should eq "3600 1 300 100 60 10000"
    End

    It "defaults to low for unknown values"
      PERSISTENCE_RDB_CONFIG_INPUT="unknown"
      When call set_persistence_config
      The status should be success
      The variable PERSISTENCE_RDB_CONFIG should eq "86400 1 21600 100 3600 10000"
    End
  End

  Describe "get_memory_limit()"
    It "converts 1200M to 1G"
      MEMORY_LIMIT="1200M"
      When call get_memory_limit
      The status should be success
      The variable MEMORY_LIMIT should eq "1G"
      The output should include "Memory Limit: 1G"
    End

    It "converts 2200M to 2G"
      MEMORY_LIMIT="2200M"
      When call get_memory_limit
      The status should be success
      The variable MEMORY_LIMIT should eq "2G"
      The output should include "Memory Limit: 2G"
    End

    It "keeps other M values unchanged"
      MEMORY_LIMIT="500M"
      When call get_memory_limit
      The status should be success
      The variable MEMORY_LIMIT should eq "500M"
      The output should include "Memory Limit: 500M"
    End

    It "keeps G values unchanged"
      MEMORY_LIMIT="4G"
      When call get_memory_limit
      The status should be success
      The variable MEMORY_LIMIT should eq "4G"
      The output should include "Memory Limit: 4G"
    End

    It "calls get_default_memory_limit when MEMORY_LIMIT is empty"
      MEMORY_LIMIT=""
      get_default_memory_limit() { echo "100MB"; }

      When call get_memory_limit
      The status should be success
      The variable MEMORY_LIMIT should eq "100MB"
      The output should include "Memory Limit: 100MB"
    End
  End

  Describe "fix_namespace_in_config_files()"
    It "rewrites namespace in node.conf"
      INSTANCE_ID="instance-new"

      cat <<'EOF' > "$NODE_CONF_FILE"
cluster-announce-hostname node-rp-0.instance-old.hc-old.us-central1.gcp.deadbeef.cloud
replica-announce-ip node-rp-0.instance-old.hc-old.us-central1.gcp.deadbeef.cloud
EOF

      When call fix_namespace_in_config_files
      The status should be success
      The output should include "Current namespace: instance-new"
      The output should include "Checking node.conf for namespace mismatches"
      The contents of file "$NODE_CONF_FILE" should include "node-rp-0.instance-new.hc-old.us-central1.gcp.deadbeef.cloud"
      The contents of file "$NODE_CONF_FILE" should not include "instance-old"
    End

    It "rewrites DNS suffix in node.conf"
      DNS_SUFFIX="hc-new.us-central1.gcp.beef.cloud"

      cat <<'EOF' > "$NODE_CONF_FILE"
cluster-announce-hostname node-rp-0.instance-abc.hc-old.us-central1.gcp.deadbeef.cloud
replica-announce-ip node-rp-0.instance-abc.hc-old.us-central1.gcp.deadbeef.cloud
EOF

      When call fix_namespace_in_config_files
      The status should be success
      The output should include "Current DNS suffix: hc-new.us-central1.gcp.beef.cloud"
      The contents of file "$NODE_CONF_FILE" should include "node-rp-0.instance-abc.hc-new.us-central1.gcp.beef.cloud"
      The contents of file "$NODE_CONF_FILE" should not include "deadbeef"
    End

    It "rewrites both namespace and DNS suffix together"
      INSTANCE_ID="instance-new"
      DNS_SUFFIX="hc-new.us-central1.gcp.beef.cloud"

      cat <<'EOF' > "$NODE_CONF_FILE"
cluster-announce-hostname node-rp-0.instance-old.hc-old.us-central1.gcp.deadbeef.cloud
replicaof node-rp-0.instance-old.hc-old.us-central1.gcp.deadbeef.cloud 6379
EOF

      When call fix_namespace_in_config_files
      The status should be success
      The output should include "Current namespace: instance-new"
      The contents of file "$NODE_CONF_FILE" should include "node-rp-0.instance-new.hc-new.us-central1.gcp.beef.cloud"
    End

    It "skips when INSTANCE_ID and DNS_SUFFIX are not set"
      INSTANCE_ID=""
      DNS_SUFFIX=""

      When call fix_namespace_in_config_files
      The status should be success
      The output should include "INSTANCE_ID not set, skipping namespace fix"
      The output should include "DNS_SUFFIX not set, skipping DNS suffix fix"
    End

    It "is idempotent when DNS suffix is already correct"
      DNS_SUFFIX="hc-new.us-central1.gcp.beef.cloud"

      cat <<'EOF' > "$NODE_CONF_FILE"
cluster-announce-hostname node-rp-0.instance-abc.hc-new.us-central1.gcp.beef.cloud
EOF

      When call fix_namespace_in_config_files
      The status should be success
      The output should include "Current DNS suffix:"
      The contents of file "$NODE_CONF_FILE" should include "node-rp-0.instance-abc.hc-new.us-central1.gcp.beef.cloud"
    End
  End

  Describe "get_self_host_ip()"
    It "resolves hostname via resolve_host_ip"
      NODE_HOST="node-rp-0.instance-abc.hc-new.us-central1.gcp.beef.cloud"
      getent() {
        echo "10.0.0.50 $2"
      }

      When call get_self_host_ip
      The status should be success
      The variable NODE_HOST_IP should eq "10.0.0.50"
    End

    It "fails when hostname does not resolve"
      NODE_HOST="node-rp-0.unresolvable.host"
      getent() { return 1; }
      sleep() { SECONDS=$((SECONDS + 301)); }

      When run get_self_host_ip
      The status should be failure
      The output should include "Failed to resolve self node host"
      The stderr should include "Timed out trying to resolve ip for self node host"
    End
  End

  Describe "check_admin_password_change()"
    It "sets RESET_ADMIN_PASSWORD when password differs"
      ADMIN_PASSWORD="newpass"
      echo 'requirepass "oldpass"' > "$NODE_CONF_FILE"
      RESET_ADMIN_PASSWORD=0

      When call check_admin_password_change
      The status should be success
      The variable RESET_ADMIN_PASSWORD should eq 1
    End

    It "does not set RESET_ADMIN_PASSWORD when password matches"
      ADMIN_PASSWORD="testpass"
      echo 'requirepass "testpass"' > "$NODE_CONF_FILE"
      RESET_ADMIN_PASSWORD=0

      When call check_admin_password_change
      The status should be success
      The variable RESET_ADMIN_PASSWORD should eq 0
    End

    It "does nothing when node.conf does not exist"
      rm -f "$NODE_CONF_FILE"
      RESET_ADMIN_PASSWORD=0

      When call check_admin_password_change
      The status should be success
      The variable RESET_ADMIN_PASSWORD should eq 0
    End

    It "handles unquoted password in node.conf"
      ADMIN_PASSWORD="mypass"
      echo 'requirepass mypass' > "$NODE_CONF_FILE"
      RESET_ADMIN_PASSWORD=0

      When call check_admin_password_change
      The status should be success
      The variable RESET_ADMIN_PASSWORD should eq 0
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
      # The old code did mkdir -p "$DATA_DIR" first, creating a regular dir,
      # then ln -s /data "$DATA_DIR" would create a CHILD symlink
      # "$DATA_DIR/data -> /data" instead of making DATA_DIR itself a symlink.
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
      # Parent must be a real directory, not a symlink
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

  Describe "add_ldap_config_to_conf()"
    It "appends LDAP module configuration to node.conf"
      LDAP_AUTH_SERVER_URL="ldaps://ldap-auth-service.ldap-auth.svc.cluster.local:3390"
      LDAP_AUTH_CA_CERT_PATH="$DATA_DIR/ldap-ca-cert.crt"
      LDAP_AUTH_PASSWORD="ldap-secret"
      INSTANCE_ID="instance-abc"
      : > "$NODE_CONF_FILE"

      When call add_ldap_config_to_conf
      The status should be success
      The output should include "Adding LDAP module to node.conf"
      The contents of file "$NODE_CONF_FILE" should include "loadmodule /var/lib/falkordb/bin/valkey_ldap.so"
      The contents of file "$NODE_CONF_FILE" should include "ldap.servers"
      The contents of file "$NODE_CONF_FILE" should include "ldap.auth_mode bind"
      The contents of file "$NODE_CONF_FILE" should include "ldap.tls_ca_cert_path"
      The contents of file "$NODE_CONF_FILE" should include "ldap.bind_dn_suffix"
      The contents of file "$NODE_CONF_FILE" should include "ou=instance-abc,dc=falkordb,dc=cloud"
      The contents of file "$NODE_CONF_FILE" should include "ldap.search_bind_passwd"
      The contents of file "$NODE_CONF_FILE" should include "ldap.exempted_users_regex"
      The contents of file "$NODE_CONF_FILE" should include "ldap.acl_fallback_enabled yes"
      The contents of file "$NODE_CONF_FILE" should include "ldap.tls_skip_verify yes"
    End

    It "does not duplicate LDAP config when already present"
      LDAP_AUTH_SERVER_URL="ldaps://ldap-auth-service.ldap-auth.svc.cluster.local:3390"
      LDAP_AUTH_CA_CERT_PATH="$DATA_DIR/ldap-ca-cert.crt"
      LDAP_AUTH_PASSWORD="ldap-secret"
      INSTANCE_ID="instance-abc"
      echo "loadmodule /var/lib/falkordb/bin/valkey_ldap.so" > "$NODE_CONF_FILE"

      When call add_ldap_config_to_conf
      The status should be success
      The output should include "LDAP module already present in node.conf"
    End
  End

  Describe "ensure_run_bgrewriteaof_script()"
    It "creates the script file in DATA_DIR"
      FALKORDB_HOME="$temp_dir/falkordb_home"
      mkdir -p "$FALKORDB_HOME"

      When call ensure_run_bgrewriteaof_script
      The status should be success
      The output should include "Creating run_bgrewriteaof script"
      The output should include "run_bgrewriteaof script created"
      The file "$DATA_DIR/run_bgrewriteaof" should be exist
      The file "$DATA_DIR/run_bgrewriteaof" should be executable
    End

    It "creates a symlink in FALKORDB_HOME"
      FALKORDB_HOME="$temp_dir/falkordb_home"
      mkdir -p "$FALKORDB_HOME"

      When call ensure_run_bgrewriteaof_script
      The status should be success
      The output should include "run_bgrewriteaof script created"
      The path "$FALKORDB_HOME/run_bgrewriteaof" should be exist
      The path "$FALKORDB_HOME/run_bgrewriteaof" should be symlink
    End

    It "generates a script with correct shebang and content"
      FALKORDB_HOME="$temp_dir/falkordb_home"
      mkdir -p "$FALKORDB_HOME"

      When call ensure_run_bgrewriteaof_script
      The status should be success
      The output should include "run_bgrewriteaof script created"
      The contents of file "$DATA_DIR/run_bgrewriteaof" should include "#!/bin/bash"
      The contents of file "$DATA_DIR/run_bgrewriteaof" should include "set -e"
      The contents of file "$DATA_DIR/run_bgrewriteaof" should include "AOF_FILE_SIZE_TO_MONITOR"
      The contents of file "$DATA_DIR/run_bgrewriteaof" should include "BGREWRITEAOF"
      The contents of file "$DATA_DIR/run_bgrewriteaof" should include "appendonlydir"
    End
  End

  Describe "LDAP_ENABLED feature flag"
    It "defaults to false in initialize_defaults"
      When call initialize_defaults
      The status should be success
      The variable LDAP_ENABLED should eq "false"
    End

    It "respects explicit true value"
      LDAP_ENABLED=true
      When call initialize_defaults
      The status should be success
      The variable LDAP_ENABLED should eq "true"
    End

    It "forces LDAP_ENABLED to false when RUN_SENTINEL=1"
      LDAP_ENABLED=true
      RUN_SENTINEL=1
      When call initialize_defaults
      The status should be success
      The variable LDAP_ENABLED should eq "false"
      The output should include "LDAP is not supported with RUN_SENTINEL=1"
    End

    It "keeps LDAP_ENABLED false when RUN_SENTINEL=1 and LDAP_ENABLED was already false"
      LDAP_ENABLED=false
      RUN_SENTINEL=1
      When call initialize_defaults
      The status should be success
      The variable LDAP_ENABLED should eq "false"
    End
  End

  Describe "sync_ldap_server_url()"
    It "migrates ldap.servers when it matches the old default port 3389"
      redis-cli() { printf 'ldap.servers\nldaps://ldap-auth-service.ldap-auth.svc.cluster.local:3389\n'; }
      config_rewrite() { :; }
      LDAP_AUTH_SERVER_URL="ldaps://ldap-auth-service.ldap-auth.svc.cluster.local:3390"
      AUTH_CONNECTION_STRING="-a testpass --no-auth-warning"
      TLS_CONNECTION_STRING=""

      When call sync_ldap_server_url
      The status should be success
      The output should include "Migrating ldap.servers"
    End

    It "does not migrate when ldap.servers already has the new URL"
      redis-cli() { printf 'ldap.servers\nldaps://ldap-auth-service.ldap-auth.svc.cluster.local:3390\n'; }
      config_rewrite() { :; }
      LDAP_AUTH_SERVER_URL="ldaps://ldap-auth-service.ldap-auth.svc.cluster.local:3390"
      AUTH_CONNECTION_STRING="-a testpass --no-auth-warning"
      TLS_CONNECTION_STRING=""

      When call sync_ldap_server_url
      The status should be success
      The output should include "already up to date"
    End

    It "handles redis-cli connection failure gracefully"
      redis-cli() { echo "ERR Connection refused" >&2; return 1; }
      LDAP_AUTH_SERVER_URL="ldaps://ldap-auth-service.ldap-auth.svc.cluster.local:3390"
      AUTH_CONNECTION_STRING="-a testpass --no-auth-warning"
      TLS_CONNECTION_STRING=""

      When call sync_ldap_server_url
      The status should be success
      The output should include "Could not read ldap.servers"
    End

    It "handles ERR response in config output gracefully"
      redis-cli() { printf 'ERR unknown command CONFIG\n'; }
      config_rewrite() { :; }
      LDAP_AUTH_SERVER_URL="ldaps://ldap-auth-service.ldap-auth.svc.cluster.local:3390"
      AUTH_CONNECTION_STRING="-a testpass --no-auth-warning"
      TLS_CONNECTION_STRING=""

      When call sync_ldap_server_url
      The status should be success
      The output should include "not set or error"
    End
  End
End
