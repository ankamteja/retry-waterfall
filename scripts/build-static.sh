#!/usr/bin/env bash
#
# Build the static console for deployment.
#
# The whole evaluation runs here, at deploy time, rather than shipping its
# output in git. That is deliberate: it means every number on the live page
# was computed from the code in this commit, so the page cannot drift away
# from the repository the way a committed artifact silently can. It takes
# about two minutes, which is a fair price for that guarantee.
#
# The project is standard library only, so there is nothing to install.
# `explain_exceptions.py` is NOT run: it is the one script that needs a model
# endpoint, and its output is committed precisely so that a build, a clone or
# a deployment never needs a key.

set -euo pipefail

echo "python: $(python3 --version)"

cd src
python3 eval_harness.py
python3 pipeline_stats.py
python3 generate_dashboard.py
cd ..

mkdir -p public
cp data/dashboard.html public/index.html

# The page is one self-contained file: fonts, Three.js and the force-graph
# library are inlined, so there is nothing else to copy and no request the
# deployed page makes to any other host.
bytes=$(wc -c < public/index.html)
echo "built public/index.html (${bytes} bytes)"

# A page that silently built empty would deploy green and look broken, so
# fail the build instead. 1MB is far below the ~2.1MB real output and far
# above anything a failed template expansion would produce.
if [ "$bytes" -lt 1000000 ]; then
  echo "ERROR: built page is implausibly small; refusing to deploy" >&2
  exit 1
fi

for marker in 'data-tab="decision"' 'data-tab="how"' 'id="graph"' 'ForceGraph3D'; do
  if ! grep -q "$marker" public/index.html; then
    echo "ERROR: built page is missing '$marker'; refusing to deploy" >&2
    exit 1
  fi
done

echo "build ok"
