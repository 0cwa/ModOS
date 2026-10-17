#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Run in a clean gh-pages checkout. Deletion deliberately yields no usable
# update document; never fabricate empty/signed metadata or redirect to a
# build-only candidate. Historical Releases and every Magisk path are untouched.
set -euo pipefail
[[ "$(git branch --show-current)" == gh-pages ]]
[[ -z "$(git status --porcelain)" ]]
shopt -s nullglob
paths=(rootless/pdx235.json rootless/XQ-DC72.json
  variants/lineageos/rootless/pdx235-*.json)
git rm --ignore-unmatch -- "${paths[@]}"
if ! git diff --cached --quiet; then
  git -c user.name=github-actions\[bot\] \
    -c user.email=41898282+github-actions\[bot\]@users.noreply.github.com \
    commit -m 'hold(pdx235): withdraw pre-repair rootless Custota pointers'
  git push origin HEAD:gh-pages
fi
# Fail if any targeted pointer survived, even on an idempotent invocation.
for path in "${paths[@]}"; do
  [[ ! -e "${path}" ]]
done
