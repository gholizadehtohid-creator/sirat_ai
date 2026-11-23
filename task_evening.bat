@echo off
rem The evening machine. Fetches the day's data headlessly, then appends the
rem forward prediction -- the entry that makes R-FORWARD-2027 a PROSPECTIVE
rem test. An evening this does not run is an admissible forward prediction
rem lost permanently, which is why it is a scheduled task and not a habit.
rem
rem The forward-log step is deliberate belt-and-braces: bootstrap's own
rem ensure() already appends the log after a fetch, but when everything is
rem fresh bootstrap exits before fetching and only the explicit call runs.
rem The log is append-once per session and OS-locked, so running it twice
rem is free.
rem
rem ORDER (2026-11-23): what is quick and leaves Codal alone comes first, the
rem long Codal passes last. Evenings get cut short -- the laptop sleeps, a
rem console window is closed -- and the news used to sit behind two hours of
rem Codal and went with it.
rem
rem EVERY STEP'S EXIT CODE goes to the log ("step <name>: exit N") and a run
rem that finished ends with "===== done ... =====". A run that stops without
rem that line was killed; until 2026-11-23 a killed run and a finished one
rem looked the same. The task's own result is the number of steps that
rem failed (0 = all well), not just the last command's code.
rem
rem Scheduled daily 19:00 by: schtasks /TN SIRAT-evening
setlocal
cd /d C:\SIRAT-AI
set PY=C:\Users\gholizadeh-t\AppData\Local\Programs\Python\Python314\python.exe
rem Persian in the output: without this a print of a symbol crashes the run.
set PYTHONIOENCODING=utf-8
set LOG=logs\task_evening.log
set FAILED=0
rem Bounded: past 2 MB the log becomes .log.1 (log_rotate.py).
"%PY%" log_rotate.py %LOG%
echo ===== %date% %time% ===== >> %LOG%

rem News for the intelligence report (gold, oil, dollar, TSE). Only the panel
rem button collected it, and the report sat on 2026-09-17 for seven weeks
rem (found 2026-11-06). ~23 RSS feeds, ~15 s; report-only, never a signal.
rem First since 2026-11-23: it needs nothing the fetch brings.
"%PY%" -c "from event_intelligence import intelligence as ei; r = ei.collect(); print('news: %%d sources ok, %%d failed, %%d new articles' %% (r['sources_ok'], r['sources_failed'], r['inserted']))" >> %LOG% 2>&1
call :step news %ERRORLEVEL%

rem The day's data. On an evening whose bars or flow are not all published
rem yet, bootstrap retries by itself until 22:30 (bootstrap.retry_deferred).
"%PY%" bootstrap.py >> %LOG% 2>&1
call :step bootstrap %ERRORLEVEL%
"%PY%" examples\forward_prediction_log.py >> %LOG% 2>&1
call :step forward-log %ERRORLEVEL%

rem The weekly universe snapshot (sirat/experiments/universe.py). Survivorship
rem is the one defect only elapsed time can fix, and the snapshot was taken by
rem hand -- 12 days overdue on 2026-11-23. Local and quick; it captures only
rem when the newest snapshot is 7 days old.
"%PY%" -c "import sys; from sirat.experiments import universe; sys.exit(universe.main(['--if-due']))" >> %LOG% 2>&1
call :step universe-snapshot %ERRORLEVEL%

rem The market's aggregate P/E, point-in-time, for the "cheap or dear" card
rem (2026-11-10). Reads only what is stored; after the first build it adds
rem the evening's session and any missing 21-session grid point.
"%PY%" market_valuation.py --update --quiet >> %LOG% 2>&1
call :step valuation %ERRORLEVEL%

rem The nightly backup (backup.py, 2026-11-23): the three databases, the
rem forward log and the small state files, copied to C:\SIRAT-AI-backups;
rem the newest 7 nights are kept. Before the Codal passes, so an evening cut
rem short still leaves one.
"%PY%" backup.py >> %LOG% 2>&1
call :step backup %ERRORLEVEL%

rem Gold funds are not equities, so the history step never fetches them;
rem until 2026-11-05 only a panel button did, and they went stale from 08-29
rem while the allocation now leans on them (E28). 60 bars covers any gap the
rem evening schedule can leave, at 14 requests.
"%PY%" -c "import gold; gold.fetch_history(count=60, verbose=False)" >> %LOG% 2>&1
call :step gold %ERRORLEVEL%

rem Monthly activity reports (ن-۳۰), since 2026-11-05. Only companies whose
rem report for the last closed month is not yet stored are searched, so after
rem the first pass this is a few dozen requests a night.
rem Capped at 120 companies: the two-hourly feed watch (codal_watch.py) is
rem what brings new reports in; this only fills gaps, and 781 searches in one
rem evening got the search host to block the watch itself (2026-11-05).
"%PY%" codal_monthly.py --hours 1.5 --limit 120 >> %LOG% 2>&1
call :step codal-monthly %ERRORLEVEL%

rem Investment companies' monthly portfolio reports (ن-۳۱), for P/NAV. Added
rem 2026-11-12 at the user's request: every NAV had dated from the end of Tir.
rem After codal_monthly, never beside it, and capped below it (45 min, 60
rem companies) so the 20:52 watch is not blinded by a throttle. Only
rem companies whose newest portfolio is behind are searched; three refused
rem downloads in a row end the pass, and a cool-off past the time limit ends
rem it rather than waiting. The watch brings new reports in; this fills gaps.
"%PY%" codal_portfolio.py --hours 0.75 --limit 60 >> %LOG% 2>&1
call :step codal-portfolio %ERRORLEVEL%

rem Dividends per share, weekly (approved 2026-11-23). The watch reads each
rem new AGM decision (ن-52, and since 2026-11-23 the late ones, ن-55); this
rem pass fills what it missed, stale companies first. --min-days 7 makes it
rem weekly by the last pass's date, so an evening the laptop was off moves
rem the pass to the next evening instead of skipping a week. Last and
rem smallest of the Codal steps: 40 companies, half an hour at most.
"%PY%" codal_announcements.py --backfill-dps --min-days 7 --limit 40 --hours 0.5 >> %LOG% 2>&1
call :step dividends %ERRORLEVEL%

echo ===== done %date% %time%: %FAILED% step(s) failed ===== >> %LOG%
exit /b %FAILED%

:step
rem %1 the step's name, %2 its exit code.
echo step %1: exit %2 >> %LOG%
if not "%2"=="0" set /a FAILED+=1
exit /b 0
