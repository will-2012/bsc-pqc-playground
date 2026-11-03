import {performance} from 'node:perf_hooks';

// Cumulative wall-time of completed stage calls; concurrent durations overlap.
export class Timings {
  constructor() { this.values = {}; }
  async measure(stage, call) {
    const start = performance.now();
    let failed = false;
    try { return await call(); }
    catch (error) { failed = true; throw error; }
    finally {
      const ms = performance.now() - start;
      const row = this.values[stage] ||= {count: 0, failed: 0, totalMs: 0, maxMs: 0};
      row.count++; row.failed += Number(failed); row.totalMs += ms; row.maxMs = Math.max(row.maxMs, ms);
    }
  }
}
export function mergeTimings(total, current, previous) {
  for (const [stage, row] of Object.entries(current || {})) {
    const old = previous[stage] || {count: 0, failed: 0, totalMs: 0};
    const out = total[stage] ||= {count: 0, failed: 0, totalMs: 0, maxMs: 0};
    out.failed += row.failed - old.failed;
    out.count += row.count - old.count; out.totalMs += row.totalMs - old.totalMs;
    out.maxMs = Math.max(out.maxMs, row.maxMs);
    out.meanMs = out.count ? out.totalMs / out.count : 0;
  }
}
