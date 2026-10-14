# ADR-001: Separate the Discrete Render Clock from Experiment Time

## Status

Accepted and implemented for the finite virtual-scene source, evidence runner and preview. Complete device/fusion pipeline adoption remains future work.

## Context

The available device cannot render every camera at native rate while also
running measurements and monitoring. Simulation speed is not an evaluation
target: exposure timestamps, logical snapshots, event processing timings and
truth outputs are. A preview pacing loop must therefore not rewrite experiment
time or silently turn a display drop into a measurement drop.

## Decision

Keep `ExperimentClock` as the only authority for scenario timestamps. Add a
separate discrete render clock that schedules preview production by wall-clock
ticks. Its frequency is an explicit runtime setting (`--render-fps`, default
10 Hz). The render clock may slow the worker, but it does not alter exposure
times, frame IDs, event sequencing or metric timestamps. Generation timing is
reported independently with a bounded p50/p95/max window.

The source samples logical scenes, frame truth and ordinary virtual events for
every exposure group. A per-camera cadence selects pixel submissions; each step
records skipped frame IDs and stage timings. The finite runner writes all
logical evidence and only selected PNG frames. Preview keeps one latest frame
per camera role and never advances either clock from polling.

## Options Considered

| Option | Benefit | Cost | Decision |
| --- | --- | --- | --- |
| Native-rate wall-clock loop | Simple | Cannot hold a stable frame budget on slow hosts | Rejected |
| Drop experiment steps to meet display FPS | Low compute | Corrupts exposure coverage and performance statistics | Rejected |
| Independent discrete render clock | Preserves timestamps and enables bounded display cadence | Requires explicit ownership and later render/sample split | Chosen |

## Consequences

- Preview playback can be slower than real time while remaining deterministic.
- Monitoring refresh and HTTP polling remain consumers; neither advances either
  clock.
- p50/p95/max generation measurements expose tail cost instead of hiding it in
  a display-rate number.
- The implemented scheduler preserves a complete logical/event stream for
  evaluation, even when it renders only selected snapshots. Custom arbitrary
  per-camera cadence policies remain a future extension; the current policy is
  derived independently for each camera from `render-every`.
- `render-every` is a pixel policy, not an experiment FPS setting. Image-path
  algorithm evaluation requires `render-every=1`; skipped frames have no
  fabricated pixels or detections.

## Center development extension (2026-10-14)

`CenterSession` now initializes the full declared module group, including explicit
`algorithm_unimplemented` health for business shells. Pixels are disabled by
default and can be resumed between steps. With pixels disabled, no pixel/GPU
resources are allocated; logical sampling, ordinary events, protected truth,
center input/progress and stage measurements continue.

Each synchronous step completes one experiment exposure group and the supplied
center hook before accepting another step. A separate finite-end step delivers
terminal progress. Monitoring does not advance the experiment; wall-clock pacing
and human pauses are excluded from center processing durations. The default hook
only checks input, and does not implement fusion or scoring. The generic
`SystemRunner` and complete image algorithm pipeline remain future work.

Preview with pixels enabled still steps to the next rendered publication. Its
`--no-video` mode publishes each logical group; algorithm debugging uses the
center tool instead. See [the JSON/tool guide](../../scenarios/README.md).

## Revisit Triggers

- Implementing device detection, calibration and fusion in the same run loop.
- Needing a render cadence above 240 Hz or user-authored cadence policies beyond
  the current independent per-camera selection.
- Evidence that GPU work, rather than pacing, is the dominant tail latency.
