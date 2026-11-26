#!/bin/sh
# One-off: store the Cloudflare API token the RDN pipeline uses (chapters,
# embeddings, the R2 bucket for the clip renderer). A macOS box asks for it;
# it's added to ~/.hca-worker.env, readable only by you, never in the repo.
#
# Make the token in the Cloudflare dashboard signed in as hello@ (tokens made
# as mike@ see an empty account): My Profile > API Tokens > Create Token >
# Custom token, Account permissions:
#   Workers AI: Edit, Vectorize: Edit, Workers R2 Storage: Edit,
#   Workers Scripts: Edit, Containers: Edit, Account Settings: Read
set -e
FILE="$HOME/.hca-worker.env"
ACCOUNT=44f8bdda2dafeb8cbd86d910abcd04df
TOKEN=$(osascript -e 'text returned of (display dialog "Paste the Cloudflare API token (hello@ account)" default answer "" with hidden answer with title "RDN Made Clear" buttons {"Cancel", "Save"} default button "Save")' 2>/dev/null) || {
  printf 'Cloudflare API token: '; stty -echo; read -r TOKEN; stty echo; echo; }
TOKEN=$(printf '%s' "$TOKEN" | tr -d '[:space:]')
[ ${#TOKEN} -ge 30 ] || { echo "That doesn't look like a Cloudflare token. Nothing saved."; exit 1; }
OK=$(curl -s "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT/tokens/verify" -H "Authorization: Bearer $TOKEN" | grep -c '"success":true' || true)
if [ "$OK" = 0 ]; then
  OK=$(curl -s https://api.cloudflare.com/client/v4/user/tokens/verify -H "Authorization: Bearer $TOKEN" | grep -c '"success":true' || true)
fi
[ "$OK" != 0 ] || { echo "Cloudflare says that token isn't valid. Nothing saved."; exit 1; }
# R2's S3 keys come from the same token: the key ID is the token's ID and the
# secret is the SHA-256 of the token (Cloudflare's documented scheme).
TID=$(curl -s https://api.cloudflare.com/client/v4/user/tokens/verify -H "Authorization: Bearer $TOKEN" | sed -n 's/.*"id":"\([0-9a-f]*\)".*/\1/p')
SECRET=$(printf '%s' "$TOKEN" | shasum -a 256 | cut -d' ' -f1)
umask 077
touch "$FILE"
grep -v -E '^(CF_ACCOUNT_ID|CF_API_TOKEN|R2_ACCOUNT_ID|R2_ACCESS_KEY_ID|R2_SECRET_ACCESS_KEY|R2_BUCKET)=' "$FILE" > "$FILE.tmp" || true
printf 'CF_ACCOUNT_ID=%s\nCF_API_TOKEN=%s\n' "$ACCOUNT" "$TOKEN" >> "$FILE.tmp"
[ -n "$TID" ] && printf 'R2_ACCOUNT_ID=%s\nR2_ACCESS_KEY_ID=%s\nR2_SECRET_ACCESS_KEY=%s\nR2_BUCKET=hca-masters\n' "$ACCOUNT" "$TID" "$SECRET" >> "$FILE.tmp"
mv "$FILE.tmp" "$FILE"; chmod 600 "$FILE"
echo "Saved to $FILE. Token is valid${TID:+; R2 keys derived from it}."
