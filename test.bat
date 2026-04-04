@echo off
"C:\Program Files\QGIS 3.40.15\apps\Python312\python.exe" -m pytest "%~dp0tests" -m "not slow" -v %*
