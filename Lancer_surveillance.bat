@echo off
chcp 65001 >nul
title CROUS Watcher - surveillance logement Nancy
cd /d "%~dp0"
echo ============================================
echo   CROUS WATCHER - surveillance en cours
echo   (laisse cette fenetre OUVERTE)
echo   Ferme-la pour arreter la surveillance.
echo ============================================
echo.
:loop
py crous_watcher.py
echo.
echo [!] Le script s'est arrete. Redemarrage dans 15 secondes...
echo     (Ctrl+C puis fermer la fenetre pour arreter completement)
timeout /t 15 >nul
goto loop
