#!/usr/bin/env bash

# PulseWatch — Umbrel exports.sh
# This file is sourced by Umbrel before starting the app.
# Export any environment variables your app needs here.

# The app data directory — all persistent files go here
export APP_DATA_DIR="${UMBREL_APP_DATA_DIR}/pulsewatch"

# Ensure the data directory exists
mkdir -p "${APP_DATA_DIR}/data"

# Set permissions so the container can write to it
chmod 755 "${APP_DATA_DIR}"
chmod 755 "${APP_DATA_DIR}/data"
