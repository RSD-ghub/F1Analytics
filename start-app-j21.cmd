@echo off
cd /d C:\Deepu\Workspaces\prompts
"C:\Program Files\Java\jdk-21\bin\java.exe" "-Dfastf1.storage.read-order=file,mongo" -jar target\prompts.jar 1>>logs\app.out.log 2>>logs\app.err.log
