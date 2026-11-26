#!/bin/sh
# Once: create a Cloudflare Stream signing key for /api/play (signed playback).
# Never put a token in this file. Export it in Terminal first:
#   export CF_ACCOUNT_ID=44f8bdda2dafeb8cbd86d910abcd04df
#   export CF_API_TOKEN=<token with Stream: Edit>
#   sh pipeline/08-create-signing-key.sh
# Copy "id" into Netlify as STREAM_KEY_ID and "pem" as STREAM_KEY_PEM.
set -e
[ -n "$CF_ACCOUNT_ID" ] && [ -n "$CF_API_TOKEN" ] || { echo "Set CF_ACCOUNT_ID and CF_API_TOKEN first."; exit 1; }
curl -sS -X POST "https://api.cloudflare.com/client/v4/accounts/$CF_ACCOUNT_ID/stream/keys" \
  -H "Authorization: Bearer $CF_API_TOKEN"
echo
