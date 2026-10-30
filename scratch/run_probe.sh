#!/bin/bash
# Run the probe and save its JSON to a file inside the workspace (readable back).
set -u
cd "$(dirname "$0")/.."
python3 scratch/probe.py > scratch/probe.out.json 2> scratch/probe.err.txt
echo "exit=$?"
wc -c scratch/probe.out.json scratch/probe.err.txt