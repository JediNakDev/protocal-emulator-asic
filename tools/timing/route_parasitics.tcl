# Hook for LibreLane's per-corner STA in the timing workflow
# (STA_EXTRA_CORNER_TCL_FILE). The global routes of the earlier routing step
# are not kept in the database, so LibreLane estimates wire parasitics from
# placement, which misses detours and reads several nanoseconds optimistic.
# Route globally again in this process, with the flow's layers and
# adjustment, and estimate the parasitics from those routes.
set_routing_layers -signal Metal2-Metal4
set_global_routing_layer_adjustment * 0.3
global_route -allow_congestion
estimate_parasitics -global_routing
