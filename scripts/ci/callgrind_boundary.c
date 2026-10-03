/* The counted boundary: one client request that zeroes callgrind's counters.
 *
 * The journey budget counts a child from `exec`, so interpreter startup and
 * the compile of its whole import closure are inside the number, on the
 * harness child's own thread, which no journey excludes. Measured with ASLR
 * pinned on the import alone, adding 15,470 comment bytes to
 * `tests/_journey_typed.py` and changing nothing else moved it by 931,947
 * instructions; a recorded count is `median(kept_journey - kept_bridge_only)`,
 * so that movement lands on the net multiplied by the baseline's ratio to the
 * residual it corrects.
 *
 * The request has to be issued from a language that can hold `asm volatile`,
 * because `CALLGRIND_ZERO_STATS` is a GNU statement expression in
 * /usr/include/valgrind/valgrind.h -- which is the whole reason this file
 * exists instead of a line in `tests/_journeys.py`. It is four lines and the
 * harness loads it through ctypes, so what the measurement depends on is
 * reviewable here rather than inlined in a workflow step.
 *
 * It lives beside the journey measurement's own tooling rather than in the
 * package: nothing outside the `journey-budget` job builds or loads it, and
 * the job names the compiled result in `DAEDALUS_CALLGRIND_BOUNDARY`.
 */
#include <valgrind/callgrind.h>

void daedalus_cg_zero_stats(void) { CALLGRIND_ZERO_STATS; }
