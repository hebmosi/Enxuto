# Enxuto

Programa simples para **cortar e comprimir vídeos** no Windows, com interface inspirada no Avidemux.

## O que ele faz

- **Corte com marcadores A e B**: prévia do vídeo com som, linha do tempo, navegação quadro a quadro e por keyframe.
- **Remover ou acelerar trechos do meio**: marque o trecho com A e B e use ✂ Remover ou ⏩ Acelerar (1,5× a 16×, com o áudio acelerado sem distorcer a voz). As edições aparecem na linha do tempo e podem ser desfeitas.
- **Juntar vídeos** (Arquivo → Juntar vídeos…): emenda vários vídeos em sequência e abre o resultado no editor. Vídeos do mesmo formato são juntados na hora, sem perda.
- **Compressão por tamanho máximo em MB** (padrão), por % do tamanho original, por qualidade constante (CRF) ou por bitrate máximo, em H.264 ou H.265.
- **Faixas de áudio**: escolher quais faixas entram (ex.: jogo e microfone gravados separados no OBS), ajustar o volume de cada uma e juntar todas numa faixa só.
- **Resolução e proporção**: reduzir a resolução e manter o formato original, esticar para 16:9 (tela esticada) ou preencher 16:9 cortando as bordas.
- **Modo "Copiar"**: corte instantâneo e sem perda, sem recodificar.
- **Tema claro e escuro**, que pode seguir o tema do Windows.
- **Atalhos de teclado configuráveis** (Editar → Atalhos de teclado…).

Todo o processamento é feito pelo [FFmpeg](https://ffmpeg.org/), que já vai embutido no instalador.

## Atalhos padrão

Todos podem ser trocados em **Editar → Atalhos de teclado…**.

| Tecla | Ação |
|---|---|
| Espaço | Tocar / pausar |
| M | Ligar / desligar o som da prévia |
| ← / → | Quadro anterior / próximo |
| Shift + ← / → | Voltar / avançar 1 s |
| Ctrl + ← / → | Voltar / avançar 10 s |
| ↓ / ↑ | Keyframe anterior / próximo |
| Home / End | Início / fim do vídeo |
| [ / ] | Marcar A (início) / B (fim) |
| Delete | Remover o trecho A–B |
| R | Acelerar o trecho A–B |
| Ctrl + Z | Desfazer |
| Ctrl + O / Ctrl + S | Abrir / salvar vídeo |

## Estrutura

```
enxuto.pyw            o programa inteiro (Python + Tkinter)
build/
  ambiente.py         baixa Python, Tk e FFmpeg para Windows (conda-forge)
  montar.py           monta a pasta do programa e confere se não falta nenhuma DLL
  instalador.nsi      script do instalador (NSIS)
  icone.py, icone.ico ícone do programa
```

## Rodar direto pelo código (sem instalar)

Com o Python 3.10+ e o FFmpeg instalados no Windows:

```
winget install Python.Python.3.12
winget install Gyan.FFmpeg
```

Depois é só dar dois cliques em `enxuto.pyw`.

## Gerar o instalador

O instalador é montado em Linux (ou no WSL, dentro do Windows), porque usa o `makensis` e baixa os componentes do Windows pelo conda-forge:

```
sudo apt install nsis
pip install py-rattler pefile
cd build
python3 ambiente.py      # baixa Python + Tk + FFmpeg para Windows em build/winenv
python3 montar.py        # monta build/dist/Enxuto e confere as DLLs
makensis instalador.nsi  # gera build/dist/Enxuto-Instalador-<versão>.exe
```

Para lançar uma nova versão, altere `VERSION` em `enxuto.pyw` e em `build/instalador.nsi`.

## Licenças

O código do Enxuto é distribuído sob a licença MIT (veja `LICENSE`).

O instalador inclui programas de terceiros, que mantêm as próprias licenças:
- **FFmpeg 8.1.2** (build "gpl" do conda-forge, com x264 e x265), licenciado sob a GPL.
  Os textos da licença acompanham a instalação em `ffmpeg/licencas/`.
  Código-fonte: https://ffmpeg.org/releases/ e receita do build em
  https://github.com/conda-forge/ffmpeg-feedstock
- **Python 3.12** (licença PSF), em `runtime/LICENSE_PYTHON.txt`.
