# Tide table

Usage: `sh tides.sh READINGS_FILE`

The readings file has one line per reading: a date, a time and a height in
metres. The script groups the lines by day and prints the highest and lowest
reading of each day together with the time it happened.

Sample files live in `data/`. Staff at the harbour office print the output and
pin it to the notice board by the slipway.
