#!/bin/sh
set -e

OMNISTRATE_API_BASE_URL="https://api.omnistrate.cloud/2022-09-01-00"
OMNISTRATE_INTERNAL_SERVICE_ID="${OMNISTRATE_INTERNAL_SERVICE_ID:-s-KgFDwg5vBS}"
OMNISTRATE_INTERNAL_PROD_ENVIRONMENT="${OMNISTRATE_INTERNAL_PROD_ENVIRONMENT:-se-1iyXYFtYfA}"

auth_token=""
if [ -n "${OMNISTRATE_USERNAME:-}" ] && [ -n "${OMNISTRATE_PASSWORD:-}" ]; then
  auth_token=$(curl -sS "${OMNISTRATE_API_BASE_URL}/signin" \
    -H "Content-Type: application/json" \
    --data-raw "{\"email\":\"${OMNISTRATE_USERNAME}\",\"password\":\"${OMNISTRATE_PASSWORD}\"}" \
    | jq -r '.jwtToken // empty')
fi

instances=$(omnistrate-ctl instance list -f service:FalkorDB,environment:Prod,plan:"FalkorDB Free",status:STOPPED -o json | jq -r '.[].instance_id')

for instance in $instances; do
  described_instance=$(omnistrate-ctl instance describe "$instance" -o json)
  last_modified=$(echo "$described_instance" | jq -r '.consumptionResourceInstanceResult.last_modified_at')
  status=$(echo "$described_instance" | jq -r '.consumptionResourceInstanceResult.status')
  deletion_protection=$(echo "$described_instance" | jq -r '.consumptionResourceInstanceResult.resourceInstanceMetadata.deletionProtection // false')
  # Convert ISO 8601 timestamp to epoch time (BusyBox date supports -D flag)
  last_modified_epoch=$(date -D "%Y-%m-%dT%H:%M:%SZ" -d "$last_modified" +"%s")
  current_epoch=$(date +"%s")
  diff=$(( (current_epoch - last_modified_epoch) / 86400 ))
  if [ "$diff" -ge 7 ] && [ "$status" = "STOPPED" ]; then
    if [ "$deletion_protection" = "true" ]; then
      if [ -z "$auth_token" ]; then
        echo "Deletion protection is enabled for $instance but no API token is available; skipping."
        continue
      fi

      echo "Disabling deletion protection for instance: $instance"
      curl -sS --fail "${OMNISTRATE_API_BASE_URL}/fleet/service/${OMNISTRATE_INTERNAL_SERVICE_ID}/environment/${OMNISTRATE_INTERNAL_PROD_ENVIRONMENT}/instance/${instance}/metadata" \
        -X PATCH \
        -H "Authorization: Bearer ${auth_token}" \
        -H "Content-Type: application/json" \
        --data-raw '{"deletionProtection":false}' >/dev/null
    fi

    echo "Deleting unused stopped free instance: $instance (last modified $diff days ago - $last_modified)"
    omnistrate-ctl instance delete "$instance" --yes
  else
    echo "Instance $instance was modified $diff days ago. Skipping."
  fi
done
