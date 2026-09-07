## Context

See proposal.md for motivation. The current `backtest_extreme_fvg.py` processes all discovered LTF FVG setups regardless of when they formed, including low-volume overnight Asian sessions and weekends. Adding optional NY session (13:00–22:00 UTC) and weekday-only filtering as config-driven toggles enables A/B testing of session-constrained performance.

## Goals / Non-Goals

**Goals:**
- Config-driven session/weekday filtering via env vars and CLI args
- Seamless integration with existing backtest flow without breaking changes
- Clear reporting of active filter state in backtest results
- Filters apply only to LTF FVG c3 close timestamp (setup formation time)

**Non-Goals:**
- Changes to live strategy engine (backtest-only for now)
- Complex session definitions beyond NY hours
- Timezone conversion or non-UTC session windows
- Performance optimization of filter logic

## Decisions

### Filter Logic Placement
**Decision**: Apply filters in `run_extreme_backtest` function immediately after LTF FVG discovery, before trade simulation.

**Rationale**: This approach:
- Maintains clean separation between setup discovery and filtering
- Preserves existing trade simulation logic unchanged
- Makes filter impact clear in debug output and trade counts
- Allows easy toggling without restructuring the main loop

**Alternative considered**: Filter during LTF FVG discovery in `find_unmitigated_ltf_fvgs` - rejected because it couples filtering logic with FVG detection, making the function more complex.

### Configuration Interface
**Decision**: Dual configuration via environment variables and CLI arguments with CLI taking precedence.

**Rationale**: 
- Env vars: `EXTREME_SESSION_FILTER_ENABLED`, `EXTREME_WEEKDAY_FILTER_ENABLED` for persistent config
- CLI args: `--session-filter`, `--weekday-filter` for one-off testing
- Follows existing pattern used by `EXTREME_LTF_TIMEFRAME` and `EXTREME_USE_CLOSE_INVALIDATION`
- CLI override enables quick A/B testing without editing `.env`

**Alternative considered**: CLI-only configuration - rejected because it doesn't support persistent deployment config.

### Session Boundary Definition
**Decision**: NY session defined as 13:00:00 UTC (inclusive) to 22:00:00 UTC (exclusive), weekdays as Monday–Friday (UTC).

**Rationale**:
- Aligns with traditional NY trading session (8am-5pm EST, accounting for DST variations by using fixed UTC)
- Inclusive start, exclusive end prevents ambiguity at boundaries
- UTC-based to avoid DST complexity and match existing timestamp handling
- Monday–Friday captures traditional weekdays, avoiding crypto weekend noise

### Function Signature Changes
**Decision**: Add optional `session_filter: bool = False` and `weekday_filter: bool = False` parameters to `run_extreme_backtest`.

**Rationale**:
- Maintains backward compatibility (defaults to existing behavior)
- Explicit boolean parameters are self-documenting
- Propagates naturally to `print_backtest_report` for display

**Alternative considered**: Single `filters: dict` parameter - rejected as less type-safe and harder to use.

## Risks / Trade-offs

**Risk**: Session filter may significantly reduce trade sample size → Mitigation: Make filters optional and report filtered vs total counts so users understand impact

**Risk**: Hard-coded UTC session boundaries don't account for DST → Mitigation: Document the fixed UTC approach; future enhancement could add timezone-aware sessions if needed

**Risk**: Filter logic adds complexity to backtest loop → Mitigation: Keep filter functions simple and well-tested; extract to helper functions for clarity

**Trade-off**: Adding parameters to `run_extreme_backtest` increases API surface → Acceptable because the parameters are optional and self-explanatory

## Migration Plan

1. **Phase 1**: Add filter logic to `backtest_extreme_fvg.py`
   - Add helper functions `is_in_ny_session()` and `is_weekday()`  
   - Modify `run_extreme_backtest` signature and implementation
   - Update `main()` CLI parsing and `print_backtest_report`

2. **Phase 2**: Add environment variable support
   - Add env var reading in `main()` 
   - Document new env vars in `.env.example`

3. **Phase 3**: Update backtest report
   - Add filter status display to `ExtremeBacktestReport`
   - Show active filters in report output

4. **Validation**: Run BTC backtest with filters enabled/disabled to verify behavior

**Rollback strategy**: The changes are additive with default behavior unchanged. If issues arise, simply avoid passing the new parameters or setting the env vars to revert to original behavior.

## Open Questions

None - the approach is straightforward and implementation details are well-defined.