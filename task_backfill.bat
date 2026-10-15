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
echo ===== %date% %time% ===== >> logs\task_backfill.log
"%PY%" codal_backfill.py --hours 6 --priority-date 1399/01/01 >> logs\task_backfill.log 2>&1
