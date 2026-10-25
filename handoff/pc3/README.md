# Patches for PC3's flashcrowd-model (applied on fc-core 2026-10-25)

- `gemini_labeler.py`: two-stage labeller. Snowflake Cortex `AI_SUMMARIZE_AGG` summarises each burst's human edit
  summaries in the warehouse (one query for all bursts), then Gemini classifies up to 12 bursts per call
  (JSON schema, temperature 0). Same cache keys, same CAUSE_LABELS output, `label()` kept for the tests.
  Default model is now `gemini-flash-lite-latest` (2.5-flash is not available to new keys).
- `forecast_job.py`: the live job labels bursts whenever GEMINI_API_KEY is set (not only when the model uses
  cause features) and passes the store so the Snowflake summary stage runs.

Result (run 20261025T033239-6a69f6, 803 labelled bursts 17-25 Oct): cause features off/on CV PR-AUC h2
0.110 / 0.109, test 0.067 / 0.058, so the gate kept them off. Labels still separate bursts sharply:
a surge followed within 3 h for 53.5% of breaking_news bursts vs 0.4% of vandalism bursts.
