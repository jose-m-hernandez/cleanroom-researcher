# Recipe scaler

Usage: `sh scale.sh RECIPE_FILE [SERVINGS]`

The recipe file starts with a comment line, then lists one ingredient per line.
The script multiplies every amount by the ratio of the new servings to the
recipe's own servings. When SERVINGS is missing it uses four.

The kitchen team prints the result on a card and clips it above the stove.
