#!/bin/sh

instances=$(omnistrate-ctl instance list -f service:FalkorDB,environment:Prod,plan:"FalkorDB Free",status:STOPPED -o json | jq -r '.[].instance_id')

for instance in $instances; do
  described_instance=$(omnistrate-ctl instance describe "$instance" -o json)
  last_modified=$(echo "$described_instance" | jq -r '.consumptionResourceInstanceResult.last_modified_at')
  status=$(echo "$described_instance" | jq -r '.consumptionResourceInstanceResult.status')
  # Convert ISO 8601 timestamp to epoch time using awk (BusyBox-compatible)
  last_modified_epoch=$(echo "$last_modified" | awk -F'[-:TZ]' '{
    year=$1; month=$2; day=$3; hour=$4; min=$5; sec=$6
    
    # Adjust for January and February
    if (month <= 2) {
      year = year - 1
      month = month + 12
    }
    
    # Calculate Julian Day Number using the standard algorithm
    A = int(year / 100)
    B = 2 - A + int(A / 4)
    JD = int(365.25 * (year + 4716)) + int(30.6001 * (month + 1)) + day + B - 1524.5
    
    # Convert to Unix epoch (seconds since 1970-01-01 00:00:00 UTC)
    # JD for Unix epoch is 2440587.5
    epoch_seconds = (JD - 2440587.5) * 86400 + hour * 3600 + min * 60 + sec
    print int(epoch_seconds)
  }')
  current_epoch=$(date +"%s")
  diff=$(( (current_epoch - last_modified_epoch) / 86400 ))
  if [ "$diff" -ge 7 ] && [ "$status" = "STOPPED" ]; then
    echo "Deleting unused stopped free instance: $instance (last modified $diff days ago - $last_modified)"
    omnistrate-ctl instance delete "$instance" --yes
  else
    echo "Instance $instance was modified $diff days ago. Skipping."
  fi
done