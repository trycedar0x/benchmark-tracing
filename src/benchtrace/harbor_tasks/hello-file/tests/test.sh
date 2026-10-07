#!/bin/bash
if [ "$(cat /app/hello.txt 2>/dev/null)" = "Hello, benchtrace!" ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
