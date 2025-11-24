"""Examine profiler output."""

import pstats
import cProfile

file = "logs/scan-prices-profile.cprof"

stats = pstats.Stats(file)
stats.sort_stats("cumulative")
stats.print_stats(50)
