#!/bin/bash

# This script downloads the debug version of falkordb.so from the GitHub releases page
# and replaces the existing falkordb.so file in the current directory.
# It also creates a backup of the original falkordb.so file.
# If the script is run with the --remove option, it will restore the original falkordb.so file
# and remove the downloaded debug version.

# Variables
RELEASE_URL="https://github.com/FalkorDB/falkordb/releases/download"
LIB_PATH="./falkordb.so"
DEBUG_LIB_NAME="falkordb.debug-x64.so"
DOWNLOAD_DIR="./data"

VERSION=""
# Functions
get_falkordb_version() {
  echo "Fetching falkordb version..."
  local module_info
  module_info=$(redis-cli info modules | grep "module:name=graph")
  if [ -z "$module_info" ]; then
    echo "Error: Unable to determine falkordb version."
    exit 1
  fi

  # Extract version from the module info
  local version
  version=$(echo "$module_info" | grep -o "ver=\d\d\d\d\d" | cut -d'=' -f2)
  if [ -z "$version" ]; then
    echo "Error: Failed to parse version."
    exit 1
  fi

  # Convert version to semantic format (e.g., 40800 -> 4.8.0)
  VERSION="$((version / 10000)).$(((version % 10000) / 100)).$((version % 100))"
}

download_debug_so() {
  get_falkordb_version
  local debug_url="${RELEASE_URL}/v${VERSION}/${DEBUG_LIB_NAME}"
  echo "Downloading ${DEBUG_LIB_NAME} for version ${VERSION}..."
  curl -s -L -o "$DOWNLOAD_DIR/$DEBUG_LIB_NAME" "$debug_url"
  if [ $? -ne 0 ]; then
    echo "Error: Failed to download ${DOWNLOAD_DIR}/${DEBUG_LIB_NAME} for version."
    exit 1
  fi
}

replace_so() {
  echo "Replacing ${LIB_PATH} with ${DEBUG_LIB_NAME}..."
  mv "$LIB_PATH" "$DOWNLOAD_DIR/falkordb.so.bak"
  cp "$DOWNLOAD_DIR/$DEBUG_LIB_NAME" "$LIB_PATH"
  echo "Replacement complete. Backup saved as $DOWNLOAD_DIR/falkordb.so.bak."
}

remove_debug_so() {
  echo "Restoring original ${LIB_PATH}..."
  mv "$DOWNLOAD_DIR/falkordb.so.bak" "$LIB_PATH"
  rm "$DOWNLOAD_DIR/$DEBUG_LIB_NAME"
  echo "Restoration complete."
}

# Main
if [ "$1" == "--remove" ]; then
  remove_debug_so
else
  download_debug_so
  replace_so
fi
