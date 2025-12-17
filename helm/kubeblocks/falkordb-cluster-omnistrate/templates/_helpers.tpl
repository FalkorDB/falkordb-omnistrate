
{{/*
Define falkordb cluster shardingSpec with ComponentDefinition.
*/}}
{{- define "falkordb-cluster.shardingSpec" }}
- name: shard
  shards: {{ .Values.falkordbCluster.shardCount }}
  template:
    name: falkordb
    componentDef: falkordb-cluster
    replicas: {{ .Values.replicas }}
    {{- if .Values.hostNetworkEnabled }}
    network:
      hostPorts:
        {{ toYaml .Values.hostPorts | nindent 8 }}
    {{- end }}
    {{- if .Values.podAntiAffinityEnabled }}
    {{- include "falkordb-cluster.shardingSchedulingPolicy" . | indent 4 }}
    {{- end }}
    {{- include "falkordb-cluster.exporter" . | indent 4 }}
    {{- if and .Values.nodePortEnabled (not .Values.hostNetworkEnabled)  (not .Values.fixedPodIPEnabled) (not .Values.loadBalancerEnabled) }}
    services:
    - name: falkordb-advertised
      serviceType: NodePort
      podService: true
    {{- end }}
    {{- if and .Values.loadBalancerEnabled (not .Values.fixedPodIPEnabled) (not .Values.hostNetworkEnabled) (not .Values.nodePortEnabled) }}
    services:
    - name: falkordb-lb-advertised
      serviceType: LoadBalancer
      podService: true
      {{- include "kblib.loadBalancerAnnotations" . | indent 4 }}
    env:
    - name: LOAD_BALANCER_ENABLED
      value: "true"
    {{- end }}
    {{- if and .Values.fixedPodIPEnabled (not .Values.nodePortEnabled) (not .Values.hostNetworkEnabled) (not .Values.loadBalancerEnabled) }}
    env:
      - name: FIXED_POD_IP_ENABLED
        value: "true"
    {{- end }}
    serviceVersion: {{ .Values.version }}
    systemAccounts:
    - name: default
      {{- if and .Values.falkordbCluster.customSecretName .Values.falkordbCluster.customSecretNamespace }}
      secretRef:
        name: {{ .Values.falkordbCluster.customSecretName }}
        namespace: {{ .Values.falkordbCluster.customSecretNamespace }}
      {{- else }}
      passwordConfig:
        length: 10
        numDigits: 5
        numSymbols: 0
        letterCase: MixedCases
        seed: {{ include "kblib.clusterName" . }}
      {{- end }}
    {{- include "falkordb-cluster.computedResources" . | indent 4 }}
    volumeClaimTemplates:
      - name: data
        spec:
          accessModes:
            - ReadWriteOnce
          resources:
            requests:
              storage: {{ print .Values.storage "Gi" }}
{{- end }}

{{/*
Expand the name of the chart.
*/}}
{{- define "falkordb-cluster.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "falkordb-cluster.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Map instance type to CPU and memory resources
Subtracts overhead for system processes and cluster coordination:
- Small instances (<8Gi): 20% overhead
- Medium instances (8-32Gi): 15% overhead  
- Large instances (>32Gi): 10% overhead
CPU overhead: ~100-200m for system components
*/}}
{{- define "falkordb-cluster.instanceTypeResources" -}}
{{- $instanceType := .Values.instanceType -}}
{{- if eq $instanceType "low" }}
cpu: "700m"
memory: "100Mi"
{{- else if eq $instanceType "e2-medium" }}
cpu: "700m"
memory: "2800Mi"
{{- else if eq $instanceType "e2-standard-2" }}
cpu: "1500m"
memory: "6000Mi"
{{- else if eq $instanceType "e2-standard-4" }}
cpu: "3500m"
memory: "13000Mi"
{{- else if eq $instanceType "e2-custom-4-8192" }}
cpu: "3500m"
memory: "6000Mi"
{{- else if eq $instanceType "e2-custom-8-16384" }}
cpu: "7500m"
memory: "13000Mi"
{{- else if eq $instanceType "e2-custom-16-32768" }}
cpu: "15900m"
memory: "27800Mi"
{{- else if eq $instanceType "e2-custom-32-65536" }}
cpu: "31900m"
memory: "59392Mi"
{{- else if eq $instanceType "t2.medium" }}
cpu: "1500m"
memory: "2800Mi"
{{- else if eq $instanceType "m6i.large" }}
cpu: "1500m"
memory: "6000Mi"
{{- else if eq $instanceType "m6i.xlarge" }}
cpu: "3900m"
memory: "13900Mi"
{{- else if eq $instanceType "c6i.xlarge" }}
cpu: "3900m"
memory: "6800Mi"
{{- else if eq $instanceType "c6i.2xlarge" }}
cpu: "7900m"
memory: "13900Mi"
{{- else if eq $instanceType "c6i.4xlarge" }}
cpu: "15900m"
memory: "27800Mi"
{{- else if eq $instanceType "c6i.8xlarge" }}
cpu: "31900m"
memory: "59392Mi"
{{- else if .Values.cpu }}
cpu: {{ .Values.cpu | quote }}
memory: {{ print .Values.memory "Gi" | quote }}
{{- else }}
cpu: "900m"
memory: "3200Mi"
{{- end }}
{{- end }}

{{/*
Compute resources - use instanceType mapping if available, otherwise use cpu/memory values
*/}}
{{- define "falkordb-cluster.computedResources" }}
{{- if .Values.instanceType }}
resources:
  limits:
    {{- include "falkordb-cluster.instanceTypeResources" . | nindent 4 }}
  requests:
    {{- include "falkordb-cluster.instanceTypeResources" . | nindent 4 }}
{{- else if .Values.cpu }}
resources:
  limits:
    cpu: {{ .Values.cpu | quote }}
    memory: {{ .Values.memory | quote }}
  requests:
    cpu: {{ .Values.cpu | quote }}
    memory: {{ .Values.memory | quote }}
{{- end }}
{{- end }}

{{/*
Generate FALKORDB_ARGS environment variable value
*/}}
{{- define "falkordb-cluster.falkordbArgs" -}}
{{- $args := list -}}
{{- if .Values.falkordbConfig.cacheSize }}
{{- $args = append $args (printf "CACHE_SIZE %s" (.Values.falkordbConfig.cacheSize | toString)) -}}
{{- end }}
{{- if .Values.falkordbConfig.nodeCreationBuffer }}
{{- $args = append $args (printf "NODE_CREATION_BUFFER %s" (.Values.falkordbConfig.nodeCreationBuffer | toString)) -}}
{{- end }}
{{- if .Values.falkordbConfig.maxQueuedQueries }}
{{- $args = append $args (printf "MAX_QUEUED_QUERIES %s" (.Values.falkordbConfig.maxQueuedQueries | toString)) -}}
{{- end }}
{{- if .Values.falkordbConfig.timeoutMax }}
{{- $args = append $args (printf "TIMEOUT_MAX %s" (.Values.falkordbConfig.timeoutMax | toString)) -}}
{{- end }}
{{- if .Values.falkordbConfig.timeoutDefault }}
{{- $args = append $args (printf "TIMEOUT_DEFAULT %s" (.Values.falkordbConfig.timeoutDefault | toString)) -}}
{{- end }}
{{- if .Values.falkordbConfig.resultSetSize }}
{{- $args = append $args (printf "RESULTSET_SIZE %s" (.Values.falkordbConfig.resultSetSize | toString)) -}}
{{- end }}
{{- if .Values.falkordbConfig.queryMemCapacity }}
{{- $args = append $args (printf "QUERY_MEM_CAPACITY %s" (.Values.falkordbConfig.queryMemCapacity | toString)) -}}
{{- end }}
{{- join " " $args -}}
{{- end }}

{{/*
Generate RDB persistence configuration
*/}}
{{- define "falkordb-cluster.rdbConfig" -}}
{{- if eq .Values.persistence.rdbConfig "low" }}
save 900 1 300 10
{{- else if eq .Values.persistence.rdbConfig "medium" }}
save 900 1 300 10 60 10000
{{- else if eq .Values.persistence.rdbConfig "high" }}
save 900 1 300 10 60 10000 15 100000
{{- else }}
save 900 1 300 10
{{- end }}
{{- end }}

{{/*
Generate AOF persistence configuration
*/}}
{{- define "falkordb-cluster.aofConfig" -}}
{{- if eq .Values.persistence.aofConfig "always" }}
appendfsync always
{{- else }}
appendfsync everysec
{{- end }}
{{- end }}

{{/*
Generate ACL command to create limited user
*/}}
{{- define "falkordb-cluster.aclCreateCommand" -}}
{{- $username := .Values.falkordbUser.username -}}
{{- $password := .Values.falkordbUser.password -}}
{{- if and $username $password }}
ACL SETUSER {{ $username }} on >{{ $password }} ~* +INFO +CLIENT +DBSIZE +PING +HELLO +AUTH +RESTORE +DUMP +DEL +EXISTS +UNLINK +TYPE +FLUSHALL +TOUCH +EXPIRE +PEXPIREAT +TTL +PTTL +EXPIRETIME +RENAME +RENAMENX +SCAN +DISCARD +EXEC +MULTI +UNWATCH +WATCH +ECHO +SLOWLOG +WAIT +WAITAOF +GET +SET +GRAPH.INFO +GRAPH.LIST +GRAPH.QUERY +GRAPH.RO_QUERY +GRAPH.EXPLAIN +GRAPH.PROFILE +GRAPH.DELETE +GRAPH.CONSTRAINT +GRAPH.SLOWLOG +GRAPH.BULK +GRAPH.CONFIG +GRAPH.COPY +CLUSTER +COMMAND +GRAPH.MEMORY +MEMORY +BGREWRITEAOF
{{- else }}
{{- fail "falkordbUser.username and falkordbUser.password are required for user creation" }}
{{- end }}
{{- end }}
{{- define "falkordb-cluster.aclSentinelCreateCommand" -}}
{{- $username := .Values.falkordbUser.username -}}
{{- $password := .Values.falkordbUser.password -}}
{{- if and $username $password }}
ACL SETUSER {{ $username }} on >{{ $password }} ~* +INFO +SENTINEL|get-master-addr-by-name +SENTINEL|remove +SENTINEL|flushconfig +SENTINEL|monitor
{{- else }}
{{- fail "falkordbUser.username and falkordbUser.password are required for user creation" }}
{{- end }}
{{- end }}


{{- define "falkordb-cluster.aclCreateCommandReplicas" -}}
{{- if eq .Values.mode "cluster" -}}
{{ mul .Values.replicas 3 }}
{{- else }}
{{ default .Values.replicas 1 -}}
{{- end }}
{{- end }}

{{/*
Define falkordb cluster shardingSpec with ComponentDefinition.
*/}}

{{/*
Define falkordb ComponentSpec with ComponentDefinition.
*/}}
{{- define "falkordb-cluster.componentSpec" }}
- name: falkordb
  {{- include "falkordb-cluster.replicaCount" . | indent 2 }}
  {{- include "falkordb-cluster.exporter" . | indent 2 }}
  {{- if .Values.podAntiAffinityEnabled }}
  {{- include "falkordb-cluster.schedulingPolicy" . | indent 2 }}
  {{- end }}
  {{- if .Values.hostNetworkEnabled }}
  network:
    hostPorts:
      {{ toYaml .Values.hostPorts | nindent 8 }}
  {{- end }}
  {{- if and .Values.nodePortEnabled (not .Values.hostNetworkEnabled) (not .Values.fixedPodIPEnabled) (not .Values.loadBalancerEnabled)}}
  services:
  - name: falkordb-advertised
    serviceType: NodePort
    podService: true
  {{- end }}
  {{- if and .Values.loadBalancerEnabled (not .Values.fixedPodIPEnabled) (not .Values.hostNetworkEnabled) (not .Values.nodePortEnabled) }}
  services:
  - name: falkordb-lb-advertised
    serviceType: LoadBalancer
    podService: true
    {{- include "kblib.loadBalancerAnnotations" . | indent 4 }}
  {{- end }}
  env:
  {{- if include "falkordb-cluster.falkordbArgs" . }}
  - name: FALKORDB_ARGS
    value: {{ include "falkordb-cluster.falkordbArgs" . | quote }}
  {{- end }}
  {{- if and .Values.sentinel (hasKey .Values.sentinel "customMasterName") .Values.sentinel.customMasterName }}
  - name: CUSTOM_SENTINEL_MASTER_NAME
    value: {{ .Values.sentinel.customMasterName }}
  {{- end }}
  {{- if and .Values.fixedPodIPEnabled (not .Values.nodePortEnabled) (not .Values.hostNetworkEnabled) (not .Values.loadBalancerEnabled) }}
  - name: FIXED_POD_IP_ENABLED
    value: "true"
  {{- end }}
  {{- if and .Values.loadBalancerEnabled (not .Values.fixedPodIPEnabled) (not .Values.hostNetworkEnabled) (not .Values.nodePortEnabled) }}
  - name: LOAD_BALANCER_ENABLED
    value: "true"
  {{- end }}
  serviceVersion: {{ .Values.version }}
  {{- if and .Values.customSecretName .Values.customSecretNamespace }}
  systemAccounts:
    - name: default
      secretRef:
        name: {{ .Values.customSecretName }}
        namespace: {{ .Values.customSecretNamespace }}
  {{- end }}
  {{- include "falkordb-cluster.computedResources" . | indent 2 }}
  {{- include "kblib.componentStorages" . | indent 2 }}
{{- end }}

{{/*
Define falkordb sentinel ComponentSpec with ComponentDefinition.
*/}}
{{- define "falkordb-cluster.sentinelComponentSpec" }}
- name: falkordb-sent
  replicas: {{ .Values.sentinel.replicas }}
  {{- if .Values.podAntiAffinityEnabled }}
  {{- include "falkordb-cluster.sentinelschedulingPolicy" . | indent 2 }}
  {{- end }}
  {{- if .Values.hostNetworkEnabled }}
  network:
    hostPorts:
      {{ toYaml .Values.hostPorts | nindent 8 }}
  {{- end }}
  {{- if and .Values.nodePortEnabled (not .Values.hostNetworkEnabled) (not .Values.fixedPodIPEnabled) (not .Values.loadBalancerEnabled)  }}
  services:
  - name: sentinel-advertised
    serviceType: NodePort
    podService: true
  {{- end }}
  {{- if and .Values.fixedPodIPEnabled (not .Values.nodePortEnabled) (not .Values.hostNetworkEnabled) (not .Values.loadBalancerEnabled)  }}
  env:
  - name: FIXED_POD_IP_ENABLED
    value: "true"
  {{- end }}
  {{- if and .Values.loadBalancerEnabled (not .Values.fixedPodIPEnabled) (not .Values.hostNetworkEnabled) (not .Values.nodePortEnabled) (hasPrefix "5." .Values.version) }}
  services:
  - name: sentinel-lb-advertised
    serviceType: LoadBalancer
    podService: true
    {{- include "kblib.loadBalancerAnnotations" . | indent 4 }}
  env:
  - name: LOAD_BALANCER_ENABLED
    value: "true"
  {{- end }}
  serviceVersion: {{ .Values.version }}
  {{- if and .Values.sentinel.customSecretName .Values.sentinel.customSecretNamespace }}
  systemAccounts:
    - name: default
      secretRef:
        name: {{ .Values.sentinel.customSecretName }}
        namespace: {{ .Values.sentinel.customSecretNamespace }}
  {{- end }}
  resources:
    limits:
      cpu: {{ .Values.sentinel.cpu | quote }}
      memory:  {{ print .Values.sentinel.memory "Gi" | quote }}
    requests:
      cpu: {{ .Values.sentinel.cpu | quote }}
      memory:  {{ print .Values.sentinel.memory "Gi" | quote }}
  volumeClaimTemplates:
    - name: data
      spec:
        accessModes:
          - ReadWriteOnce
        storageClassName: {{ .Values.sentinel.storageClassName }}
        resources:
          requests:
            storage: {{ print .Values.sentinel.storage "Gi" }}
{{- end }}

{{/*
Define replica count.
standalone mode: 1
replication mode: 2
*/}}
{{- define "falkordb-cluster.replicaCount" }}
{{- if eq .Values.mode "standalone" }}
replicas: 1
{{- else if eq .Values.mode "replication" }}
replicas: {{ max .Values.replicas 2 }}
{{- end }}
{{- end }}

{{/*
Define falkordb cluster sharding count.
*/}}
{{- define "falkordb-cluster.shards" }}
shards: {{ max .Values.falkordbCluster.shardCount 3 }}
{{- end }}


{{/*
Define falkordb cluster exporter.
*/}}
{{- define "falkordb-cluster.exporter" }}
{{- if or ( not .Values.extra.disableExporter ) }}
disableExporter: false
{{- else }}
disableExporter: true
metrics:
  image:
    repository: falkordb/redis_exporter
    tag: v1.70.2-alpine
{{- end }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "falkordb-cluster.selectorLabels" -}}
app.kubernetes.io/instance: {{ include "kblib.clusterName" . | quote }}
app.kubernetes.io/managed-by: "kubeblocks"
apps.kubeblocks.io/component-name: "falkordb"
{{- end }}

{{/*
falkordb Cluster sharding schedulingPolicy
*/}}
{{- define "falkordb-cluster.shardingSchedulingPolicy" }}
schedulingPolicy:
  affinity:
    {{- if .Values.nodeAffinity }}
    nodeAffinity:
      {{ .Values.nodeAffinity | toYaml | nindent 6 }}
    {{- end }}
    podAntiAffinity:
      preferredDuringSchedulingIgnoredDuringExecution:
      - podAffinityTerm:
          labelSelector:
            matchLabels:
              app.kubernetes.io/instance: {{ include "kblib.clusterName" . | quote }}
              app.kubernetes.io/managed-by: "kubeblocks"
              kubeblocks.io/role: primary
          topologyKey: kubernetes.io/hostname
        weight: 100
  {{- if .Values.multiZoneEnabled }}
  topologySpreadConstraints:
  - maxSkew: 1
    topologyKey: topology.kubernetes.io/zone
    whenUnsatisfiable: DoNotSchedule
    labelSelector:
      matchLabels:
        app.kubernetes.io/instance: {{ include "kblib.clusterName" . | quote }}
        app.kubernetes.io/managed-by: "kubeblocks"
  {{- end }}
{{- end -}}


{{/*
falkordb schedulingPolicy
*/}}
{{- define "falkordb-cluster.schedulingPolicy" }}
schedulingPolicy:
  affinity:
    {{- if .Values.nodeAffinity }}
    nodeAffinity:
      {{ .Values.nodeAffinity | toYaml | nindent 6 }}
    {{- end }}
    podAntiAffinity:
      preferredDuringSchedulingIgnoredDuringExecution:
      - podAffinityTerm:
          labelSelector:
            matchLabels:
              app.kubernetes.io/instance: {{ include "kblib.clusterName" . | quote }}
              app.kubernetes.io/managed-by: "kubeblocks"
              apps.kubeblocks.io/component-name: "falkordb"
          topologyKey: kubernetes.io/hostname
        weight: 100
  {{- if .Values.multiZoneEnabled }}
  topologySpreadConstraints:
  - maxSkew: 1
    topologyKey: topology.kubernetes.io/zone
    whenUnsatisfiable: DoNotSchedule
    labelSelector:
      matchLabels:
        app.kubernetes.io/instance: {{ include "kblib.clusterName" . | quote }}
        app.kubernetes.io/managed-by: "kubeblocks"
  {{- end }}
{{- end -}}

{{/*
falkordb sentinel schedulingPolicy
*/}}
{{- define "falkordb-cluster.sentinelschedulingPolicy" }}
schedulingPolicy:
  affinity:
    {{- if .Values.nodeAffinity }}
    nodeAffinity:
      {{ .Values.nodeAffinity | toYaml | nindent 6 }}
    {{- end }}
    podAntiAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        - labelSelector:
            matchLabels:
              app.kubernetes.io/instance: {{ include "kblib.clusterName" . | quote }}
              app.kubernetes.io/managed-by: "kubeblocks"
              apps.kubeblocks.io/component-name: "falkordb-sent"
          topologyKey: kubernetes.io/hostname
  {{- if .Values.multiZoneEnabled }}
  topologySpreadConstraints:
  - maxSkew: 1
    topologyKey: topology.kubernetes.io/zone
    whenUnsatisfiable: DoNotSchedule
    labelSelector:
      matchLabels:
        app.kubernetes.io/instance: {{ include "kblib.clusterName" . | quote }}
        app.kubernetes.io/managed-by: "kubeblocks"
        apps.kubeblocks.io/component-name: "falkordb-sent"
  {{- end }}
{{- end -}}

{{/*
Define common fileds of cluster object
*/}}
{{- define "falkordb-cluster.clusterCommon" }}
apiVersion: apps.kubeblocks.io/v1
kind: Cluster
metadata:
  name: {{ include "kblib.clusterName" . }}
  namespace: {{ .Release.Namespace }}
  labels: {{ include "kblib.clusterLabels" . | nindent 4 }}
  annotations:
    apps.kubeblocks.io/mode: {{ .Values.mode }}
  {{- if and .Values.podAntiAffinityEnabled (eq .Values.mode "cluster") }}
    apps.kubeblocks.io/shard-pod-anti-affinity: "shard"
  {{- end }}
spec:
  terminationPolicy: {{ .Values.extra.terminationPolicy }}
{{- end }}