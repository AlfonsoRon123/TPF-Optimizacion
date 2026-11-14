@echo off
REM Doble clic para abrir el optimizador de flota en el navegador.
cd /d "%~dp0"
echo Instalando/verificando librerias (solo tarda la primera vez)...
python -m pip install -q -r requirements.txt
if errorlevel 1 (
    echo.
    echo No se pudieron instalar las librerias. Revisa que Python este instalado
    echo y marcado "Add Python to PATH" durante la instalacion.
    pause
    exit /b 1
)
echo Abriendo la app... (para cerrarla, cerra esta ventana)
python -m streamlit run app.py
pause
