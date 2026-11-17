# Lembretes

App de lembretes discretos para Windows, minimalista, com um toque de "dynamic island".

## O que faz

- **Pop-ups sutis** acima do relógio, com lembretes recorrentes (a cada X minutos ou em horários fixos), dias da semana, meta diária e contador de "Feito".
- **Lembrete rápido** em qualquer lugar com `Ctrl + Alt + L` (`17:30 buscar a Sarah`, `25 min alongar`, `amanhã 9h reunião`).
- **Ilha no topo da tela**: leve o mouse ao topo, no centro, e uma gota escura desce com o que vem a seguir e as tarefas do dia.
- **Olhinhos curiosos**: de tempos em tempos descem do topo, piscam e espiam o mouse ou o texto que você digita.
- **Notion**: lê a rotina ou as tarefas de hoje de um banco, marca como feito no Notion e cria tarefas com `Ctrl + Alt + N` (`orçamento amanhã !alta #setor`).
- **Foco automático**: segura os lembretes durante chamadas, tela cheia e pomodoro e mostra um resumo depois.
- **Pomodoro** com `Ctrl + Alt + P`, sons suaves, temas claro, escuro e automático.

## Rodar

Requer Python 3.11+ no Windows.

```bash
pip install pystray pillow
pyw lembretes.pyw
```

Ou dê dois cliques em `Abrir Lembretes.bat`.

## Gerar o .exe

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name Lembretes --hidden-import pystray._win32 lembretes.pyw
```

O executável sai em `dist/Lembretes.exe` (veja `dist/LEIA-ME.txt` para quem for usar).

## Onde ficam os dados

Tudo fica em `%APPDATA%\Lembretes`: `config.json` com lembretes e configurações, `notion_cache.json` e os sons gerados. O token do Notion é criptografado com a conta do Windows (DPAPI) e nunca fica na pasta do projeto.
