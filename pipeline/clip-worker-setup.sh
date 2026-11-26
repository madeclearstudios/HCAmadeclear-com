#!/bin/sh
# One-off: store the Supabase secret key for the Mac render worker.
#
# Get the key from the Supabase dashboard: project hca-made-clear ->
# Project Settings -> API Keys -> Secret keys (starts sb_secret_). A macOS
# password box asks for it (falls back to a hidden Terminal prompt). It's saved
# to ~/.hca-worker.env, readable only by your user, never in the repo.
set -e
FILE="$HOME/.hca-worker.env"
KEY=$(osascript -e 'text returned of (display dialog "Paste the Supabase secret key for hca-made-clear (starts sb_secret_)" default answer "" with hidden answer with title "HCA clip worker" buttons {"Cancel", "Save"} default button "Save")' 2>/dev/null) || {
  printf 'Supabase secret key: '; stty -echo; read -r KEY; stty echo; echo; }
KEY=$(printf '%s' "$KEY" | tr -d '[:space:]')
case "$KEY" in
  sb_secret_*|eyJ*) ;;
  *) echo "That doesn't look like a Supabase secret key (sb_secret_...). Nothing saved."; exit 1 ;;
esac
umask 077
touch "$FILE"   # keep whatever else is in there (the Cloudflare and R2 keys)
grep -v -E '^(SUPABASE_URL|SUPABASE_SECRET_KEY)=' "$FILE" > "$FILE.tmp" || true
printf 'SUPABASE_URL=https://bgwnjdbezxhprpmvueae.supabase.co\nSUPABASE_SECRET_KEY=%s\n' "$KEY" >> "$FILE.tmp"
mv "$FILE.tmp" "$FILE"; chmod 600 "$FILE"
echo "Saved to $FILE ($(printf '%s' "$KEY" | cut -c1-10)...). Start the worker with: python3 pipeline/clip_worker.py"
