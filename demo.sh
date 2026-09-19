#!/bin/sh
# demo.sh — reproduce every proof in PROOFS.md. Ports die with the script.
set -e
cd "$(dirname "$0")"
CPORT=${CPORT:-18111}
BPORT=${BPORT:-19111}
python3 calculator/server.py $CPORT & CSRV=$!
python3 bproto/bserve bproto/www $BPORT & BSRV=$!
trap "kill $CSRV $BSRV 2>/dev/null; wait 2>/dev/null; true" EXIT INT TERM
sleep 1
echo "=== calculator marking (one socket) ==="
python3 calculator/test_mark.py localhost $CPORT
echo "=== bserve single ==="
python3 bproto/bcurl localhost:$BPORT/index.html
echo ""
echo "=== bserve multi (one connection, two files) ==="
python3 bproto/bcurl localhost:$BPORT/hello.txt localhost:$BPORT/index.html
echo ""
echo "=== bserve 404 (exit non-zero) ==="
if python3 bproto/bcurl localhost:$BPORT/nope.html; then echo "UNEXPECTED 0"; else echo "exit=1 as required"; fi
echo "=== DEMO PASS ==="
