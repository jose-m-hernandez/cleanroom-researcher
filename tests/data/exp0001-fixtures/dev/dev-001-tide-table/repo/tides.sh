#!/bin/sh
# Print high and low water for each day in a readings file.
# Usage: sh tides.sh READINGS_FILE
file="$1"
[ -r "$file" ] || { echo "cannot read readings file" >&2; exit 2; }
awk '
  {
    day = $1
    if (!(day in hi) || $3 > hi[day]) { hi[day] = $3; hit[day] = $2 }
    if (!(day in lo) || $3 < lo[day]) { lo[day] = $3; lot[day] = $2 }
    if (!(day in seen)) { seen[day] = 1; order[++n] = day }
  }
  END {
    for (i = 1; i <= n; i++) {
      d = order[i]
      printf "%s\n  high %s (%s m)\n  low  %s (%s m)\n", d, hit[d], hi[d], lot[d], lo[d]
    }
  }
' "$file"
