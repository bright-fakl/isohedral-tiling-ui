#!/usr/bin/env bash
# Wait until a Hugging Face Space runs a given commit and its app answers HTTP 200; fail with the reported stage otherwise.
# Usage: HF_TOKEN=... ci/wait_space.sh <owner/space> <commit sha of the Space repository> [timeout seconds]
set -u
space=$1
sha=${2:?the commit sha of the Space repository is required (did the push step run?)}
timeout=${3:-1800}
host=$(echo "$space" | tr 'A-Z/_' 'a-z--').hf.space
url="https://$host/app"
start=$(date +%s)
last=""
while true; do
  info=$(curl -s -H "Authorization: Bearer $HF_TOKEN" "https://huggingface.co/api/spaces/$space")
  read -r stage rsha err < <(echo "$info" | python3 -c "
import json, sys
try:
    r = json.load(sys.stdin).get('runtime') or {}
except ValueError:
    r = {}
print(r.get('stage', 'UNKNOWN'), r.get('sha', '-'), (r.get('errorMessage') or '-').replace('\n', ' '))
")
  case "$stage" in
    BUILD_ERROR|RUNTIME_ERROR|NO_APP_FILE|CONFIG_ERROR|PAUSED|STOPPED|DELETING)
      echo "::error::Space $space is in stage $stage: $err"
      case "$err" in *quota*) echo "::error::The account's quota for running Spaces is reached (the free plan runs one cpu-basic Docker Space at a time).";; esac
      exit 1;;
  esac
  code=$(curl -s -o /dev/null -w '%{http_code}' "$url")
  line="stage=$stage sha=${rsha:0:7} (want ${sha:0:7}) app=HTTP $code"
  if [ "$line" != "$last" ]; then echo "$(( $(date +%s) - start )) s: $line"; last=$line; fi
  if [ "$stage" = RUNNING ] && [ "$rsha" = "$sha" ] && [ "$code" = 200 ]; then
    echo "Space $space runs ${sha:0:7} and $url answers HTTP 200"
    exit 0
  fi
  if [ $(( $(date +%s) - start )) -gt "$timeout" ]; then
    echo "::error::Space $space did not run ${sha:0:7} with HTTP 200 within $timeout s (last: $line)"
    exit 1
  fi
  sleep 15
done
