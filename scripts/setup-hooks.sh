#!/bin/sh
# Points git at the repo's hooks (.githooks), so commits without a ticket key are rejected.
set -eu
cd "$(git rev-parse --show-toplevel)"
git config core.hooksPath .githooks
chmod +x .githooks/*
echo "Git hooks enabled from .githooks"
