@echo off
rem The codal backfill campaign: six paced hours a day toward the 1394-depth
rem fundamental panel (RESEARCH_OPTIONS.md Option 4). Fully resumable -- the
rem queue and per-company search state live in sirat_market.db, so each run
rem continues where the last stopped, and the coverage-first ordering means
rem an interrupted run has already bought the most valuable filings first.
rem
rem 09:00 keeps it clear of the 19:00 evening fetch and of codal_sync's own
rem daily statements, so the two never share codal.ir's throttle budget.
rem
rem Scheduled daily 09:00 by: schtasks /TN SIRAT-codal-backfill
cd /d C:\SIRAT-AI
set PY=C:\Users\gholizadeh-t\AppData\Local\Programs\Python\Python314\python.exe
rem Persian in the output: without this a print of a symbol crashes the run.
set PYTHONIOENCODING=utf-8
rem Bounded: past 2 MB the log becomes .log.1 (log_rotate.py).
"%PY%" log_rotate.py logs\task_backfill.log
echo ===== %date% %time% ===== >> logs\task_backfill.log
rem 23.5 hours since 2026-11-05: a six-hour day left the queue (~21,000
rem statements) idle eighteen hours out of twenty-four, and the campaign's
rem own back-off already handles Codal's firewall. Ends before the next 09:00.
"%PY%" codal_backfill.py --hours 23.5 --priority-date 1399/01/01 >> logs\task_backfill.log 2>&1
set RC=%ERRORLEVEL%
rem A run that finished ends with a "done" line and its exit code; one
rem that stops without it was killed (added 2026-11-23).
echo ===== done %date% %time%: codal-backfill exit %RC% ===== >> logs\task_backfill.log
exit /b %RC%
