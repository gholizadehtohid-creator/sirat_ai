@echo off
rem The evening machine. Fetches the day's data headlessly, then appends the
rem forward prediction -- the entry that makes R-FORWARD-2027 a PROSPECTIVE
rem test. An evening this does not run is an admissible forward prediction
rem lost permanently, which is why it is a scheduled task and not a habit.
rem
rem The second line is deliberate belt-and-braces: bootstrap's own ensure()
rem already appends the log after a fetch, but when everything is fresh
rem bootstrap exits before fetching and only the explicit call runs. The log
rem is append-once per session and OS-locked, so running it twice is free.
rem
rem Scheduled daily 19:00 by: schtasks /TN SIRAT-evening
cd /d C:\SIRAT-AI
set PY=C:\Users\gholizadeh-t\AppData\Local\Programs\Python\Python314\python.exe
rem Persian in the output: without this a print of a symbol crashes the run.
set PYTHONIOENCODING=utf-8
rem Bounded: past 2 MB the log becomes .log.1 (log_rotate.py).
"%PY%" log_rotate.py logs\task_evening.log
echo ===== %date% %time% ===== >> logs\task_evening.log
"%PY%" bootstrap.py >> logs\task_evening.log 2>&1
"%PY%" examples\forward_prediction_log.py >> logs\task_evening.log 2>&1
rem Gold funds are not equities, so the history step never fetches them;
rem until 2026-11-05 only a panel button did, and they went stale from 08-29
rem while the allocation now leans on them (E28). 60 bars covers any gap the
rem evening schedule can leave, at 14 requests.
"%PY%" -c "import gold; gold.fetch_history(count=60, verbose=False)" >> logs\task_evening.log 2>&1
rem Monthly activity reports (ن-۳۰), since 2026-11-05. Only companies whose
rem report for the last closed month is not yet stored are searched, so after
rem the first pass this is a few dozen requests a night.
rem Capped at 120 companies: the two-hourly feed watch (codal_watch.py) is
rem what brings new reports in; this only fills gaps, and 781 searches in one
rem evening got the search host to block the watch itself (2026-11-05).
"%PY%" codal_monthly.py --hours 1.5 --limit 120 >> logs\task_evening.log 2>&1
rem News for the intelligence report (gold, oil, dollar, TSE). Only the panel
rem button collected it, and the report sat on 2026-09-17 for seven weeks
rem (found 2026-11-06). ~23 RSS feeds, ~15 s; report-only, never a signal.
"%PY%" -c "from event_intelligence import intelligence as ei; r = ei.collect(); print('news: %%d sources ok, %%d failed, %%d new articles' %% (r['sources_ok'], r['sources_failed'], r['inserted']))" >> logs\task_evening.log 2>&1
