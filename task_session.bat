@echo off
rem The session watch. Starts before the Tehran open and runs until the
rem market stops answering, capturing the order book and whole-market
rem حقیقی/حقوقی while they exist.
rem
rem WHY THIS IS A SCHEDULED TASK AND NOT A HABIT
rem Intraday state cannot be backfilled. A session nobody watched is an
rem order book that no longer exists anywhere -- depth.py has said so in its
rem own docstring since August, and between 2026-08-22 and 2026-10-15 it was
rem run exactly twice, because installing the job was left to someone
rem remembering.
rem
rem The monitor decides for itself whether a session is running, by counting
rem instruments that report turnover. It does NOT read a clock or a holiday
rem calendar: this exchange published a complete frozen market every day for
rem the eleven weeks it was shut (MARKET_REGIME_AUDIT.md), so a calendar is
rem exactly the thing that cannot be trusted here. Starting early and letting
rem it exit on three consecutive closed readings costs three HTTP calls on a
rem holiday.
rem
rem 08:40 Tehran is before the 09:00 open. The machine runs on +03:30 with no
rem DST, so local time is used directly.
rem
rem Scheduled Sat-Wed 08:40 by: schtasks /TN SIRAT-session
rem Battery settings matter and do not travel with this file -- see
rem task_evening.bat and the 0x800710E0 note there.
cd /d C:\SIRAT-AI
set PY=C:\Users\gholizadeh-t\AppData\Local\Programs\Python\Python314\python.exe
echo ===== %date% %time% ===== >> logs\task_session.log
"%PY%" monitor.py >> logs\task_session.log 2>&1
