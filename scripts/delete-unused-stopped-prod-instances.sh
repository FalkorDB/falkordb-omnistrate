#!/bin/sh
set -e

# Delete paid (non-Free) instances that have been continuously STOPPED for at
# least AGE_THRESHOLD_MINUTES minutes. Free tier instances are handled separately
# by delete-unused-stopped-free-instances.sh.
#
# The target environment is configurable so the job can be tested against QA
# before being pointed at Prod:
#   OMNISTRATE_ENVIRONMENT_FILTER   - omnistrate-ctl environment name (e.g. dev, Prod)
#   OMNISTRATE_INTERNAL_ENVIRONMENT - environment id used in fleet API calls
#
# AGE_THRESHOLD_MINUTES defaults to 14 days (20160 minutes) for production; set a
# small value (e.g. 15) to test the flow without waiting 14 days.
#
# Safety: DRY_RUN defaults to "true" so the job only reports candidates. Set
# DRY_RUN=false to actually disable deletion protection, delete instances and
# notify customers.

OMNISTRATE_API_BASE_URL="https://api.omnistrate.cloud/2022-09-01-00"
OMNISTRATE_INTERNAL_SERVICE_ID="${OMNISTRATE_INTERNAL_SERVICE_ID:-s-KgFDwg5vBS}"
OMNISTRATE_ENVIRONMENT_FILTER="${OMNISTRATE_ENVIRONMENT_FILTER:-Prod}"
OMNISTRATE_INTERNAL_ENVIRONMENT="${OMNISTRATE_INTERNAL_ENVIRONMENT:-se-1iyXYFtYfA}"
BREVO_API_URL="https://api.brevo.com/v3/smtp/email"
BREVO_TEMPLATE_ID="${BREVO_TEMPLATE_ID:-2}"
# Default threshold is 14 days expressed in minutes (14 * 24 * 60 = 20160).
AGE_THRESHOLD_MINUTES="${AGE_THRESHOLD_MINUTES:-20160}"
DRY_RUN="${DRY_RUN:-true}"

echo "Targeting environment '${OMNISTRATE_ENVIRONMENT_FILTER}', threshold: ${AGE_THRESHOLD_MINUTES} minutes"
if [ "$DRY_RUN" = "true" ]; then
  echo "Running in DRY_RUN mode: no instances will be deleted and no emails will be sent."
fi

auth_token=""
if [ -n "${OMNISTRATE_USERNAME:-}" ] && [ -n "${OMNISTRATE_PASSWORD:-}" ]; then
  auth_token=$(curl -sS "${OMNISTRATE_API_BASE_URL}/signin" \
    -H "Content-Type: application/json" \
    --data-raw "{\"email\":\"${OMNISTRATE_USERNAME}\",\"password\":\"${OMNISTRATE_PASSWORD}\"}" \
    | jq -r '.jwtToken // empty')
fi

# List all stopped instances in the target environment. Free tier instances are
# skipped in the loop below since they are handled by a separate job.
instances=$(omnistrate-ctl instance list -f "service:FalkorDB,environment:${OMNISTRATE_ENVIRONMENT_FILTER},status:STOPPED" -o json | jq -r '.[].instance_id')

if [ -z "$instances" ]; then
  echo "No stopped instances found in environment '${OMNISTRATE_ENVIRONMENT_FILTER}'. Nothing to do."
  exit 0
fi

instance_count=$(printf '%s\n' "$instances" | grep -c .)
echo "Found ${instance_count} stopped instance(s) in environment '${OMNISTRATE_ENVIRONMENT_FILTER}':"
printf '  - %s\n' $instances

for instance in $instances; do
  described_instance=$(omnistrate-ctl instance describe "$instance" -o json)
  plan=$(echo "$described_instance" | jq -r '.consumptionResourceInstanceResult.productTierName // .plan // ""')
  last_modified=$(echo "$described_instance" | jq -r '.consumptionResourceInstanceResult.last_modified_at')
  status=$(echo "$described_instance" | jq -r '.consumptionResourceInstanceResult.status')
  deletion_protection=$(echo "$described_instance" | jq -r '.consumptionResourceInstanceResult.resourceInstanceMetadata.deletionProtection // false')

  # Skip free tier instances; these are handled by delete-unused-stopped-free-instances.sh
  if echo "$plan" | grep -qi "free"; then
    echo "Instance $instance is on a Free plan ($plan). Skipping (handled by free tier job)."
    continue
  fi

  # Convert ISO 8601 timestamp to epoch time (BusyBox date supports -D flag)
  last_modified_epoch=$(date -D "%Y-%m-%dT%H:%M:%SZ" -d "$last_modified" +"%s")
  current_epoch=$(date +"%s")
  diff=$(( (current_epoch - last_modified_epoch) / 60 ))

  if [ "$diff" -ge "$AGE_THRESHOLD_MINUTES" ] && [ "$status" = "STOPPED" ]; then
    if [ "$DRY_RUN" = "true" ]; then
      echo "[DRY_RUN] Would delete stopped instance: $instance (plan: $plan, stopped $diff minutes ago - $last_modified)"
      continue
    fi

    if [ "$deletion_protection" = "true" ]; then
      if [ -z "$auth_token" ]; then
        echo "Deletion protection is enabled for $instance but no API token is available; skipping."
        continue
      fi

      echo "Disabling deletion protection for instance: $instance"
      curl -sS --fail "${OMNISTRATE_API_BASE_URL}/fleet/service/${OMNISTRATE_INTERNAL_SERVICE_ID}/environment/${OMNISTRATE_INTERNAL_ENVIRONMENT}/instance/${instance}/metadata" \
        -X PATCH \
        -H "Authorization: Bearer ${auth_token}" \
        -H "Content-Type: application/json" \
        --data-raw '{"deletionProtection":false}' >/dev/null
    fi

    echo "Deleting unused stopped instance: $instance (plan: $plan, stopped $diff minutes ago - $last_modified)"
    omnistrate-ctl instance delete "$instance" --yes

    # Send termination email to subscription owners via Brevo (best-effort)
    if [ -n "${BREVO_API_KEY:-}" ] && [ -n "$auth_token" ]; then
      subscription_id=$(echo "$described_instance" | jq -r '.subscriptionId // empty')
      if [ -n "$subscription_id" ]; then
        users_response=$(curl -sS "${OMNISTRATE_API_BASE_URL}/fleet/service/${OMNISTRATE_INTERNAL_SERVICE_ID}/environment/${OMNISTRATE_INTERNAL_ENVIRONMENT}/users?subscriptionId=${subscription_id}" \
          -H "Authorization: Bearer ${auth_token}" \
          -H "Content-Type: application/json") || true
        to_array=$(echo "$users_response" | jq -c '[.users[]? | {email: .email, name: .userName}]' 2>/dev/null) || true
        if [ -n "$to_array" ] && [ "$to_array" != "[]" ]; then
          echo "Sending termination email to: $(echo "$to_array" | jq -r '.[].email' | tr '\n' ', ')"
          curl -sS "$BREVO_API_URL" \
            -X POST \
            -H "api-key: ${BREVO_API_KEY}" \
            -H "Content-Type: application/json" \
            --data-raw "{\"templateId\":${BREVO_TEMPLATE_ID},\"to\":${to_array},\"params\":{\"instance_id\":\"${instance}\"}}" >/dev/null || echo "Warning: failed to send termination email for instance $instance"
        fi
      fi
    fi
  else
    echo "Instance $instance (plan: $plan) does NOT meet the deletion requirement (stopped for >= ${AGE_THRESHOLD_MINUTES} minutes): status is '$status', stopped $diff minutes ago ($last_modified). Skipping."
  fi
done
