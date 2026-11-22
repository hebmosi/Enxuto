Unicode true
SetCompressor /SOLID lzma
SetCompressorDictSize 64

!define APPNAME "Enxuto"
!define APPKEY "Enxuto"
!define VERSION "1.2.0"
!define SRC "dist\Enxuto"

Name "${APPNAME}"
OutFile "dist\Enxuto-Instalador-${VERSION}.exe"
InstallDir "$LOCALAPPDATA\Programs\${APPKEY}"
InstallDirRegKey HKCU "Software\${APPKEY}" "InstallDir"
RequestExecutionLevel user
BrandingText "${APPNAME} ${VERSION}"

VIProductVersion "${VERSION}.0"
VIAddVersionKey /LANG=1046 "ProductName" "${APPNAME}"
VIAddVersionKey /LANG=1046 "FileDescription" "Instalador do ${APPNAME}"
VIAddVersionKey /LANG=1046 "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=1046 "ProductVersion" "${VERSION}"
VIAddVersionKey /LANG=1046 "LegalCopyright" "Uso pessoal. Inclui Python e FFmpeg (licenças nos arquivos)."

!include "MUI2.nsh"
!define MUI_ICON "icone.ico"
!define MUI_UNICON "icone.ico"
!define MUI_ABORTWARNING

!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_FUNCTION AbrirPrograma
!define MUI_FINISHPAGE_RUN_TEXT "Abrir o ${APPNAME} agora"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "PortugueseBR"

Function AbrirPrograma
  Exec '"$INSTDIR\runtime\pythonw.exe" "$INSTDIR\enxuto.pyw"'
FunctionEnd

!macro Atalho path
  CreateShortCut "${path}" "$INSTDIR\runtime\pythonw.exe" '"$INSTDIR\enxuto.pyw"' "$INSTDIR\icone.ico" 0 SW_SHOWNORMAL "" "Corta e comprime vídeos"
!macroend

Section "${APPNAME} (obrigatório)" SecMain
  SectionIn RO
  ; remove a versão antiga, que se chamava "Corta & Comprime"
  ReadRegStr $0 HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\CortaComprime" "InstallLocation"
  StrCmp $0 "" +3
    ExecWait '"$0\Desinstalar.exe" /S _?=$0'
    RMDir /r "$0"
  ; limpa uma versão anterior antes de copiar
  RMDir /r "$INSTDIR\runtime"
  RMDir /r "$INSTDIR\ffmpeg"
  SetOutPath "$INSTDIR"
  File /r "${SRC}\*.*"
  WriteUninstaller "$INSTDIR\Desinstalar.exe"

  CreateDirectory "$SMPROGRAMS\${APPNAME}"
  !insertmacro Atalho "$SMPROGRAMS\${APPNAME}\${APPNAME}.lnk"
  CreateShortCut "$SMPROGRAMS\${APPNAME}\Desinstalar.lnk" "$INSTDIR\Desinstalar.exe"

  WriteRegStr HKCU "Software\${APPKEY}" "InstallDir" "$INSTDIR"
  !define UNKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPKEY}"
  WriteRegStr HKCU "${UNKEY}" "DisplayName" "${APPNAME}"
  WriteRegStr HKCU "${UNKEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNKEY}" "Publisher" "Heb"
  WriteRegStr HKCU "${UNKEY}" "DisplayIcon" "$INSTDIR\icone.ico"
  WriteRegStr HKCU "${UNKEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNKEY}" "UninstallString" '"$INSTDIR\Desinstalar.exe"'
  WriteRegDWORD HKCU "${UNKEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNKEY}" "NoRepair" 1
  WriteRegDWORD HKCU "${UNKEY}" "EstimatedSize" 195000
SectionEnd

Section "Atalho na área de trabalho" SecDesktop
  !insertmacro Atalho "$DESKTOP\${APPNAME}.lnk"
SectionEnd

!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SecMain} "O programa, com o Python e o FFmpeg embutidos."
  !insertmacro MUI_DESCRIPTION_TEXT ${SecDesktop} "Cria um atalho na área de trabalho. Dá para arrastar um vídeo sobre ele para abrir."
!insertmacro MUI_FUNCTION_DESCRIPTION_END

Section "Uninstall"
  Delete "$DESKTOP\${APPNAME}.lnk"
  RMDir /r "$SMPROGRAMS\${APPNAME}"
  RMDir /r "$INSTDIR\runtime"
  RMDir /r "$INSTDIR\ffmpeg"
  RMDir /r "$INSTDIR\__pycache__"
  Delete "$INSTDIR\enxuto.pyw"
  Delete "$INSTDIR\icone.ico"
  Delete "$INSTDIR\Desinstalar.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPKEY}"
  DeleteRegKey HKCU "Software\${APPKEY}"
SectionEnd
