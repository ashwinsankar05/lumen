# Measured results

Measured results only. Every entry carries its date, hardware and conditions (room light,
subject distance, display brightness, camera settings and whether exposure/white balance were
locked), the session ids, and the analysis settings (band, segment length, K). Simulated numbers
never go here (PROJECT.md §4). Results that fail to reproduce are recorded here too, not retuned
away (§12).

Template for an entry:

```
## YYYY-MM-DD — <what was measured>

- Hardware: <laptop, camera index and backend, resolution, measured fps, display, brightness>
- Conditions: <light sources, time of day, distance, pose, probe layout and amplitude, duration>
- Camera: exposure locked? white balance locked? (from the capture report)
- Sessions: <ids and labels>

| Session | Face coh. | Neck coh. | Floor (K) | Gain | Cheek contrast | Lag | Verdict |
|---|---|---|---|---|---|---|---|

Reading: <what the numbers do and do not show; limits>
```

---

*No measurements yet (project started 2026-10-05). First: Milestone 1, `docs/PROTOCOL.md`.*
