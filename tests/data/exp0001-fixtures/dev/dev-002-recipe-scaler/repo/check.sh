#!/bin/sh
# Scale the pancake recipe to double servings and compare.
sh scale.sh recipes/pancakes.txt 8 | diff - recipes/expected-pancakes-x2.txt && echo "pancakes ok"
