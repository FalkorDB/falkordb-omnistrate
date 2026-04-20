# shellcheck shell=bash

Describe "cluster-entrypoint.sh helpers"
  Include ./cluster-entrypoint.sh

  setup() {
    temp_dir=$(mktemp -d "${TMPDIR:-/tmp}/cluster-entrypoint-spec.XXXXXX")
    DATA_DIR="$temp_dir/runtime/data"
    mkdir -p "$DATA_DIR"
    NODE_CONF_FILE="$DATA_DIR/node.conf"
    : > "$NODE_CONF_FILE"
    NODE_HOST="cluster-sz-0.instance-new.hc-new.us-central1.gcp.beef.cloud"
    NODE_PORT=6379
    BUS_PORT=16379
    TLS=false
    POD_IP="10.0.0.10"
    INSTANCE_ID=""
    DNS_SUFFIX=""
    ADMIN_PASSWORD="testpass"
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
    unset DATA_DIR NODE_CONF_FILE NODE_HOST NODE_PORT BUS_PORT TLS POD_IP INSTANCE_ID DNS_SUFFIX ADMIN_PASSWORD
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
      getent() {
        return 1
      }

      sleep() {
        SECONDS=$((SECONDS + 301))
      }

      When run resolve_host_ip "cluster-sz-1.internal" "peer node" 0
      The status should be failure
      The stderr should include "Timed out trying to resolve ip for peer node: cluster-sz-1.internal"
    End
  End

  Describe "fix_namespace_in_config_files()"
    It "rewrites namespace in both node.conf and nodes.conf"
      INSTANCE_ID="instance-new"

      cat <<'EOF' > "$NODE_CONF_FILE"
cluster-announce-hostname cluster-sz-0.instance-old.hc-old.us-central1.gcp.deadbeef.cloud
EOF
      cat <<'EOF' > "$DATA_DIR/nodes.conf"
07c37dfeb2352e66 192.168.1.10:6379@16379,cluster-sz-0.instance-old.hc-old.us-central1.gcp.deadbeef.cloud myself,master - 0 0 1 connected
EOF

      When call fix_namespace_in_config_files
      The status should be success
      The output should include "Current namespace: instance-new"
      The contents of file "$NODE_CONF_FILE" should include "instance-new"
      The contents of file "$NODE_CONF_FILE" should not include "instance-old"
      The contents of file "$DATA_DIR/nodes.conf" should include "instance-new"
    End

    It "rewrites DNS suffix in both node.conf and nodes.conf"
      DNS_SUFFIX="hc-new.us-central1.gcp.beef.cloud"

      cat <<'EOF' > "$NODE_CONF_FILE"
cluster-announce-hostname cluster-sz-0.instance-abc.hc-old.us-central1.gcp.deadbeef.cloud
EOF
      cat <<'EOF' > "$DATA_DIR/nodes.conf"
07c37dfeb2352e66 192.168.1.10:6379@16379,cluster-sz-0.instance-abc.hc-old.us-central1.gcp.deadbeef.cloud myself,master - 0 0 1 connected
EOF

      When call fix_namespace_in_config_files
      The status should be success
      The output should include "Current DNS suffix:"
      The contents of file "$NODE_CONF_FILE" should include "hc-new.us-central1.gcp.beef.cloud"
      The contents of file "$DATA_DIR/nodes.conf" should include "hc-new.us-central1.gcp.beef.cloud"
      The contents of file "$DATA_DIR/nodes.conf" should not include "deadbeef"
    End

    It "skips when INSTANCE_ID and DNS_SUFFIX are not set"
      When call fix_namespace_in_config_files
      The status should be success
      The output should include "INSTANCE_ID not set, skipping namespace fix"
      The output should include "DNS_SUFFIX not set, skipping DNS suffix fix"
    End

    It "is idempotent when DNS suffix is already correct"
      DNS_SUFFIX="hc-new.us-central1.gcp.beef.cloud"

      cat <<'EOF' > "$NODE_CONF_FILE"
cluster-announce-hostname cluster-sz-0.instance-abc.hc-new.us-central1.gcp.beef.cloud
EOF

      When call fix_namespace_in_config_files
      The status should be success
      The output should include "Current DNS suffix:"
      The contents of file "$NODE_CONF_FILE" should include "cluster-sz-0.instance-abc.hc-new.us-central1.gcp.beef.cloud"
    End

    It "rewrites both namespace and DNS suffix simultaneously"
      INSTANCE_ID="instance-new"
      DNS_SUFFIX="hc-new.us-central1.gcp.beef.cloud"

      cat <<'EOF' > "$NODE_CONF_FILE"
cluster-announce-hostname cluster-sz-0.instance-old.hc-old.us-central1.gcp.deadbeef.cloud
EOF
      cat <<'EOF' > "$DATA_DIR/nodes.conf"
07c37dfeb2352e66 192.168.1.10:6379@16379,cluster-sz-0.instance-old.hc-old.us-central1.gcp.deadbeef.cloud myself,master - 0 0 1 connected
EOF

      When call fix_namespace_in_config_files
      The status should be success
      The output should include "Current namespace: instance-new"
      The output should include "Current DNS suffix: hc-new.us-central1.gcp.beef.cloud"
      The contents of file "$NODE_CONF_FILE" should include "cluster-sz-0.instance-new.hc-new.us-central1.gcp.beef.cloud"
      The contents of file "$DATA_DIR/nodes.conf" should include "cluster-sz-0.instance-new.hc-new.us-central1.gcp.beef.cloud"
    End
  End

  Describe "prepare_node_files_for_startup()"
    It "rewrites namespace and DNS suffix before resolving node IPs"
      INSTANCE_ID="instance-new"
      DNS_SUFFIX="hc-new.us-central1.gcp.beef.cloud"

      cat <<'EOF' > "$NODE_CONF_FILE"
cluster-announce-hostname cluster-sz-0.instance-old.hc-old.us-central1.gcp.deadbeef.cloud
EOF

      cat <<'EOF' > "$DATA_DIR/nodes.conf"
07c37dfeb2352e66 192.168.1.10:6379@16379,cluster-sz-0.instance-old.hc-old.us-central1.gcp.deadbeef.cloud myself,master - 0 0 1 connected
2a2c0f54d8c4aa11 192.168.1.11:6379@16379,cluster-sz-1.instance-old.hc-old.us-central1.gcp.deadbeef.cloud master - 0 0 2 connected
EOF

      getent() {
        case "$2" in
          cluster-sz-0.instance-new.hc-new.us-central1.gcp.beef.cloud)
            echo "10.0.0.10 $2"
            ;;
          cluster-sz-1.instance-new.hc-new.us-central1.gcp.beef.cloud)
            echo "10.0.0.11 $2"
            ;;
          *)
            return 1
            ;;
        esac
      }

      When call prepare_node_files_for_startup
      The status should be success
      The output should include "Updating IP for node cluster-sz-1.instance-new.hc-new.us-central1.gcp.beef.cloud"
      The contents of file "$NODE_CONF_FILE" should include "cluster-announce-hostname cluster-sz-0.instance-new.hc-new.us-central1.gcp.beef.cloud"
      The contents of file "$DATA_DIR/nodes.conf" should include "10.0.0.10:6379@16379,cluster-sz-0.instance-new.hc-new.us-central1.gcp.beef.cloud myself"
      The contents of file "$DATA_DIR/nodes.conf" should include "10.0.0.11:6379@16379,cluster-sz-1.instance-new.hc-new.us-central1.gcp.beef.cloud"
    End

    It "returns success when nodes.conf does not exist"
      rm -f "$DATA_DIR/nodes.conf"

      When call prepare_node_files_for_startup
      The status should be success
      The output should include "First time running the node.."
    End
  End

  Describe "update_ips_in_nodes_conf()"
    It "resolves the current node hostname when POD_IP is not set"
      unset POD_IP

      cat <<'EOF' > "$DATA_DIR/nodes.conf"
07c37dfeb2352e66 192.168.1.10:6379@16379,cluster-sz-0.instance-new.hc-new.us-central1.gcp.beef.cloud myself,master - 0 0 1 connected
2a2c0f54d8c4aa11 192.168.1.11:6379@16379,cluster-sz-1.instance-new.hc-new.us-central1.gcp.beef.cloud master - 0 0 2 connected
EOF

      getent() {
        case "$2" in
          cluster-sz-0.instance-new.hc-new.us-central1.gcp.beef.cloud)
            echo "10.0.0.20 $2"
            ;;
          cluster-sz-1.instance-new.hc-new.us-central1.gcp.beef.cloud)
            echo "10.0.0.21 $2"
            ;;
          *)
            return 1
            ;;
        esac
      }

      When call update_ips_in_nodes_conf
      The status should be success
      The output should include "Updating local node address: 192.168.1.10:6379@16379 -> 10.0.0.20:6379@16379"
      The contents of file "$DATA_DIR/nodes.conf" should include "10.0.0.20:6379@16379,cluster-sz-0.instance-new.hc-new.us-central1.gcp.beef.cloud myself"
      The contents of file "$DATA_DIR/nodes.conf" should include "10.0.0.21:6379@16379,cluster-sz-1.instance-new.hc-new.us-central1.gcp.beef.cloud"
    End

    It "uses port 0 for the current node when TLS is enabled"
      TLS=true

      cat <<'EOF' > "$DATA_DIR/nodes.conf"
07c37dfeb2352e66 192.168.1.10:6379@16379,cluster-sz-0.instance-new.hc-new.us-central1.gcp.beef.cloud myself,master - 0 0 1 connected
2a2c0f54d8c4aa11 192.168.1.11:6379@16379,cluster-sz-1.instance-new.hc-new.us-central1.gcp.beef.cloud master - 0 0 2 connected
EOF

      getent() {
        case "$2" in
          cluster-sz-1.instance-new.hc-new.us-central1.gcp.beef.cloud)
            echo "10.0.0.11 $2"
            ;;
          *)
            return 1
            ;;
        esac
      }

      When call update_ips_in_nodes_conf
      The status should be success
      The output should include "Updating local node address: 192.168.1.10:6379@16379 -> 10.0.0.10:0@16379"
      The contents of file "$DATA_DIR/nodes.conf" should include "10.0.0.10:0@16379,cluster-sz-0.instance-new.hc-new.us-central1.gcp.beef.cloud myself"
    End

    It "fails when a peer hostname never resolves"
      cat <<'EOF' > "$DATA_DIR/nodes.conf"
07c37dfeb2352e66 192.168.1.10:6379@16379,cluster-sz-0.instance-new.hc-new.us-central1.gcp.beef.cloud myself,master - 0 0 1 connected
2a2c0f54d8c4aa11 192.168.1.11:6379@16379,cluster-sz-1.instance-new.hc-new.us-central1.gcp.beef.cloud master - 0 0 2 connected
EOF

      getent() {
        return 1
      }

      sleep() {
        SECONDS=$((SECONDS + 301))
      }

      When run update_ips_in_nodes_conf
      The status should be failure
      The stdout should include "Updating local node address: 192.168.1.10:6379@16379 -> 10.0.0.10:6379@16379"
      The stderr should include "Timed out trying to resolve ip for cluster node hostname: cluster-sz-1.instance-new.hc-new.us-central1.gcp.beef.cloud"
    End

    It "skips lines with no resolvable hostname"
      cat <<'EOF' > "$DATA_DIR/nodes.conf"
07c37dfeb2352e66 10.0.0.10:6379@16379,cluster-sz-0.instance-new.hc-new.us-central1.gcp.beef.cloud myself,master - 0 0 1 connected
2a2c0f54d8c4aa11 192.168.1.11:6379@16379 master - 0 0 2 connected
EOF

      getent() {
        case "$2" in
          cluster-sz-0.instance-new.hc-new.us-central1.gcp.beef.cloud)
            echo "10.0.0.10 $2"
            ;;
          *)
            return 1
            ;;
        esac
      }

      When call update_ips_in_nodes_conf
      The status should be success
      The output should include "No resolvable hostname found for node with addr: 192.168.1.11:6379@16379"
      The contents of file "$DATA_DIR/nodes.conf" should include "192.168.1.11:6379@16379"
    End

    It "preserves comment and header lines"
      cat <<'EOF' > "$DATA_DIR/nodes.conf"
# Some comment
07c37dfeb2352e66 10.0.0.10:6379@16379,cluster-sz-0.instance-new.hc-new.us-central1.gcp.beef.cloud myself,master - 0 0 1 connected
vars currentEpoch 1 lastVoteEpoch 0
EOF

      getent() {
        return 1
      }

      When call update_ips_in_nodes_conf
      The status should be success
      The output should include "Updating local node address"
      The contents of file "$DATA_DIR/nodes.conf" should include "# Some comment"
      The contents of file "$DATA_DIR/nodes.conf" should include "vars currentEpoch"
    End

    It "does not change IPs that are already correct"
      cat <<'EOF' > "$DATA_DIR/nodes.conf"
07c37dfeb2352e66 10.0.0.10:6379@16379,cluster-sz-0.instance-new.hc-new.us-central1.gcp.beef.cloud myself,master - 0 0 1 connected
2a2c0f54d8c4aa11 10.0.0.11:6379@16379,cluster-sz-1.instance-new.hc-new.us-central1.gcp.beef.cloud master - 0 0 2 connected
EOF

      getent() {
        case "$2" in
          cluster-sz-1.instance-new.hc-new.us-central1.gcp.beef.cloud)
            echo "10.0.0.11 $2"
            ;;
          *)
            return 1
            ;;
        esac
      }

      When call update_ips_in_nodes_conf
      The status should be success
      # Should NOT print "Updating IP" for the peer since IP is already 10.0.0.11
      The output should not include "Updating IP for node"
    End

    It "returns early for empty nodes.conf"
      : > "$DATA_DIR/nodes.conf"

      When call update_ips_in_nodes_conf
      The status should be success
      The output should include "First time running the node.."
    End

    It "updates only the myself line when there are no peers"
      cat <<'EOF' > "$DATA_DIR/nodes.conf"
07c37dfeb2352e66 192.168.1.10:6379@16379,cluster-sz-0.instance-new.hc-new.us-central1.gcp.beef.cloud myself,master - 0 0 1 connected
EOF

      When call update_ips_in_nodes_conf
      The status should be success
      The output should include "Updating local node address"
      The contents of file "$DATA_DIR/nodes.conf" should include "10.0.0.10:6379@16379,cluster-sz-0.instance-new.hc-new.us-central1.gcp.beef.cloud myself"
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

  Describe "get_host()"
    It "returns hostname for a given index in single-zone mode"
      NODE_HOST="cluster-sz-0.instance-abc.hc-new.us-central1.gcp.beef.cloud"
      NODE_INDEX=0
      IS_MULTI_ZONE=0

      When call get_host 1
      The status should be success
      The output should eq "cluster-sz-1.instance-abc.hc-new.us-central1.gcp.beef.cloud"
    End

    It "returns hostname for a given index in multi-zone mode"
      NODE_HOST="cluster-mz-0.instance-abc.hc-new.us-central1.gcp.beef.cloud"
      NODE_INDEX=0
      IS_MULTI_ZONE=1

      When call get_host 3
      The status should be success
      The output should eq "cluster-mz-3.instance-abc.hc-new.us-central1.gcp.beef.cloud"
    End

    It "replaces only the matching node index"
      NODE_HOST="cluster-sz-2.instance-abc.hc-new.us-central1.gcp.beef.cloud"
      NODE_INDEX=2
      IS_MULTI_ZONE=0

      When call get_host 5
      The status should be success
      The output should eq "cluster-sz-5.instance-abc.hc-new.us-central1.gcp.beef.cloud"
    End
  End

  Describe "prepare_data_dir()"
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

    It "skips mkdir when DATA_DIR is /data"
      DATA_DIR="/data"

      When call prepare_data_dir
      The status should be success
      The variable DATA_DIR should eq "/data"
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

    It "skips creation when script already exists"
      FALKORDB_HOME="$temp_dir/falkordb_home"
      mkdir -p "$FALKORDB_HOME"
      echo "#!/bin/bash" > "$FALKORDB_HOME/run_bgrewriteaof"

      When call ensure_run_bgrewriteaof_script
      The status should be success
      The output should include "run_bgrewriteaof script already exists"
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