#!/bin/bash
set -e
source "$(cd "$(dirname "$0")"; while [ ! -f .polarion-root ]; do cd ..; done; pwd)/scripts/params.sh"

if [ "$1" = "--remove" ]; then
    teardown_environment
else
    setup_environment
fi
