-- PC3: read EDITS_CLEAN, not EDITS. Backfill reconnects can re-send events; this removes exact duplicates.
CREATE OR REPLACE VIEW FLASHCROWD.CORE.EDITS_CLEAN AS
SELECT DISTINCT ts, wiki, title, namespace, type, user_name, is_bot, is_revert, delta_bytes, comment
FROM FLASHCROWD.CORE.EDITS;
