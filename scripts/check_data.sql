-- Data integrity checks (each should return 0). Run after any reload.
SELECT 'pageview dup rows' chk, COUNT(*) n FROM (SELECT 1 FROM PAGEVIEWS_HOURLY GROUP BY hour_utc, wiki, title HAVING COUNT(*) > 1)
UNION ALL SELECT 'pageview hours missing (10 Oct -> latest)', DATEDIFF(hour, MIN(hour_utc), MAX(hour_utc)) + 1 - COUNT(DISTINCT hour_utc) FROM PAGEVIEWS_HOURLY
UNION ALL SELECT 'main page dup rows', COUNT(*) FROM (SELECT 1 FROM MAIN_PAGE_EVENTS GROUP BY ts, wiki, title, slot HAVING COUNT(*) > 1);
