#!/bin/sh
# Scale a recipe to a new number of servings.
# Usage: sh scale.sh RECIPE_FILE [SERVINGS]
file="$1"
want="${2:-4}"
[ -r "$file" ] || { echo "cannot read recipe file" >&2; exit 2; }
base=$(head -1 "$file" | sed 's/.*: *//')
awk -v want="$want" -v base="$base" '
  NR == 1 { print; next }
  {
    amount = $1 * want / base
    printf "%d %s", amount + 0.5, $2
    for (i = 3; i <= NF; i++) printf " %s", $i
    printf "\n"
  }
' "$file"
