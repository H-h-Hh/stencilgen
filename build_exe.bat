@echo off
chcp 65001 >nul
rem 重新打包免安装版（在 源码\ 里运行，产物写到 dist\StencilGen-免安装版）
cd /d "%~dp0"

set "OUT=%~dp0dist\StencilGen-免安装版"

if not exist "_build\vendor\PyInstaller" (
    echo [1/3] 下载 PyInstaller 到 _build ...
    py -3 -m pip download --no-cache-dir -d "_build\dl" pyinstaller || goto :err
    py -3 -m pip install --no-index --find-links "_build\dl" --target "_build\vendor" pyinstaller || goto :err
)

echo [2/3] 打包中 ...
set PYTHONPATH=%CD%\_build\vendor
py -3 -m PyInstaller --noconfirm --onedir --console --name StencilGen --paths . ^
    --distpath "%CD%\_build\out" --workpath "%CD%\_build\work" ^
    --collect-all OCP ^
    --exclude-module vtkmodules --exclude-module cadquery --exclude-module scipy ^
    --exclude-module numba --exclude-module llvmlite --exclude-module casadi ^
    --exclude-module matplotlib --exclude-module IPython --exclude-module pytest ^
    --exclude-module trame app.py || goto :err

echo [3/3] 安装到 ..\StencilGen-免安装版 ...
if exist "%OUT%" rmdir /s /q "%OUT%"
move "_build\out\StencilGen" "%OUT%" >nul || goto :err
rmdir /s /q "_build\out" 2>nul

rem 把启动器和使用说明放进去（这两样不在 PyInstaller 产物里，必须每次补）
copy /y "launcher.txt" "%OUT%\启动.bat" >nul || goto :err
copy /y "使用说明.txt" "%OUT%\使用说明.txt" >nul || goto :err

rem 新产物会继承本目录的完整性标签；若本目录是低完整性（各种沙箱），
rem 生成的 exe 会被降权运行，写文件时报「拒绝访问」。统一改回 Medium。
icacls "%OUT%" /setintegritylevel "(OI)(CI)Medium" /T >nul 2>&1

echo.
echo 完成：dist\StencilGen-免安装版\StencilGen.exe   (约 335 MB，目标机器不需要 Python)
echo 这个文件夹可以直接打包发给别人。
pause
exit /b 0

:err
echo.
echo 打包失败
pause
exit /b 1
