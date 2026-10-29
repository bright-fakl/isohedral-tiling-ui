#!/usr/bin/env bash
# Push a single snapshot commit of the checked-out tree to a Hugging Face Space (force push to its main).
# Usage: HF_TOKEN=... ci/sync_space.sh <owner/space> <sha>   (writes snapshot=<commit> to $GITHUB_OUTPUT)
# The snapshot has no tests/ and ci/: the hub rejects the binary test fixtures.
# A Space that does not exist (HTTP 404) is skipped with a notice; any other failure fails the job.
set -eu
space=$1
sha=$2

status=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $HF_TOKEN" "https://huggingface.co/api/spaces/$space")
case "$status" in
  200) ;;
  404)
    echo "::notice::The Space $space does not exist or the token cannot see it (HTTP 404); nothing was pushed."
    exit 0
    ;;
  *)
    echo "::error::Checking the Space $space returned HTTP $status (token missing, expired or without access?)"
    exit 1
    ;;
esac

git config user.name "github-actions"
git config user.email "github-actions@users.noreply.github.com"
git checkout --orphan hf-deploy
git rm -r -q --cached tests ci
git commit -q -m "Deploy $sha"
git remote add hf "https://user:$HF_TOKEN@huggingface.co/spaces/$space"
git push hf hf-deploy:main --force
snapshot=$(git rev-parse HEAD)
if [ -n "${GITHUB_OUTPUT:-}" ]; then echo "snapshot=$snapshot" >> "$GITHUB_OUTPUT"; fi
echo "Pushed $sha to $space"
