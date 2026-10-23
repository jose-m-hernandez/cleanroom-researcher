#!/bin/sh
# Compare the script's output on the sample with the expected file.
sh tides.sh data/harbour-sample.txt | diff - data/expected-sample.txt && echo "sample ok"
