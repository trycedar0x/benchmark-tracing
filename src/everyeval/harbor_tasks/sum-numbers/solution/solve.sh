#!/bin/bash
awk "{s+=\$1} END {print s}" /app/numbers.txt > /app/answer.txt
