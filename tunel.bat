@echo off
chcp 65001 >nul
title Tunel de Cloudflare - Inventario
rem Abre un tunel temporal de Cloudflare hacia el programa de inventario (puerto 5000).
rem El programa (python run.py) debe estar corriendo en otra ventana.

where cloudflared >nul 2>nul
if errorlevel 1 (
  echo No se encontro cloudflared. Instalalo con este comando y vuelve a abrir este archivo:
  echo.
  echo     winget install --id Cloudflare.cloudflared
  echo.
  pause
  exit /b 1
)

echo Abriendo tunel... Busca abajo una direccion como https://algo.trycloudflare.com
echo Copiala en Configuracion ^> Direccion publica para que el codigo QR la use.
echo Deja esta ventana abierta mientras quieras que funcione desde internet.
echo.
cloudflared tunnel --url http://localhost:5000
pause
