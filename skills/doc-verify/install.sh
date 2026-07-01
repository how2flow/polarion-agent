#!/bin/bash
set -e
source "$(cd "$(dirname "$0")"; while [ ! -f .polarion-root ]; do cd ..; done; pwd)/scripts/params.sh"

if [ "$1" = "--remove" ]; then
    uninstall_skill "doc-verify"
else
    install_skill "doc-verify"
fi
