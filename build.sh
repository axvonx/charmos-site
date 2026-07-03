#!/usr/bin/env bash
#
# Build the charmOS documentation site.
#
#   ./build.sh              incremental — regenerate docs + build the site
#   ./build.sh --rebuild    clean rebuild — wipe every generated artifact first
#                           (use this when you're unsure you're seeing the latest)
#   ./build.sh --serve      after building, serve a local preview
#   ./build.sh --rebuild --serve
#
# The clang source browser is generated automatically when the codebrowser
# binaries are found (in .tools/woboq/ or on PATH, or via WOBOQ_GENERATOR);
# otherwise symbols link to GitHub source instead.

set -euo pipefail
cd "$(dirname "$0")"

REBUILD=0
SERVE=0
for arg in "$@"; do
  case "$arg" in
    --rebuild|-r) REBUILD=1 ;;
    --serve|-s)   SERVE=1 ;;
    -h|--help)
      grep '^#' "$0" | sed '1d;s/^#\s\{0,1\}//'
      exit 0 ;;
    *) echo "unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

if [ "$REBUILD" = 1 ]; then
  echo "==> clean rebuild: wiping generated artifacts"
  rm -rf docs json_output clang_index.json \
         charmos/_ccdb \
         site/.astro site/dist \
         site/src/content/docs \
         site/public/source
fi

echo "==> [1/2] generating docs (clang index, source browser, MDX)"
python3 generate.py

echo "==> [2/2] building Astro site"
cd site
[ -d node_modules ] || npm install
npm run build

echo "==> done — output in site/dist"
if [ "$SERVE" = 1 ]; then
  echo "==> serving preview (ctrl-c to stop)"
  npm run preview
fi
