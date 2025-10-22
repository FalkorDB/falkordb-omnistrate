#!/bin/sh

instances=$(omnistrate-ctl instance list -f service:FalkorDB,environment:Prod,plan:"FalkorDB Free",status:STOPPED -o json | jq -r '.[].instance_id')

for instance in $instances; do
  described_instance=$(omnistrate-ctl instance describe "$instance" -o json)
  last_modified=$(echo "$described_instance" | jq -r '.consumptionResourceInstanceResult.last_modified_at')
  status=$(echo "$described_instance" | jq -r '.consumptionResourceInstanceResult.status')
  last_modified_epoch=$(date -j -f "%Y-%m-%dT%H:%M:%SZ" "$last_modified" +"%s")
  current_epoch=$(date +"%s")
  diff=$(( (current_epoch - last_modified_epoch) / 86400 ))
  if [ "$diff" -ge 14 ] && [ "$status" = "STOPPED" ]; then
    echo "Deleting unused stopped free instance: $instance (last modified $diff days ago - $last_modified)"
    omnistrate-ctl instance delete "$instance" --yes
  else
    echo "Instance $instance was modified $diff days ago. Skipping."
  fi
done