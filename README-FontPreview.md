# FontPreview

A fast Windows font previewer for designers. Type your text once and see it in every font installed on your PC, big and uniform, without the tiny, jumpy previews of Photoshop's font menu.

![FontPreview in grid view](screenshot.png)

## Made with Claude

This tool was written entirely by **Claude**, Anthropic's AI assistant, through a back-and-forth conversation about what a good font previewer for client work should do.

Because of that, **no credit or attribution is needed**. Use it, modify it, share it, or build it into your own projects however you like.

## Features

- **Every installed font**, including fonts you bought and installed yourself
- **Uniform previews.** "Fit to card" makes every font fill its card the same way, whatever its natural size. Also: cap height, x-height and true size modes.
- **List or grid view**, with adjustable rows per screen (list) or columns (grid)
- **Weights grouped per family**, with a style picker on each card
- **Normalized line spacing**, so fonts with huge built-in line gaps look normal
- **Missing-character warning** for French accents (é à ç œ « ») and anything in your sample text
- **My fonts** filter that hides the fonts that came with Windows
- **Favorites** and a compare view, **hide** fonts you never use
- **Custom text and background colors**, with recent color combos
- **Export your shortlist as a PNG** (stack or grid, optional watermark) ready to send to a client
- **Copy any preview as an image**
- Dark and light themes, keyboard shortcuts

## Requirements

**To use the .exe:** Windows 10 or 11 (64-bit). Nothing else to install.

**To run or build from source:**

| Requirement | Version | What it's for |
|---|---|---|
| [Python](https://www.python.org/downloads/) | 3.10 or newer | Runs the app. During installation, check **"Add python.exe to PATH"**. |
| [PySide6](https://pypi.org/project/PySide6/) | 6.5 or newer | The interface (Qt) and font rendering |
| [fontTools](https://pypi.org/project/fonttools/) | 4.40 or newer | Reads font files for the "My fonts" filter and family grouping. Optional: without it the app still runs, but "My fonts" is disabled. |
| [PyInstaller](https://pypi.org/project/pyinstaller/) | any recent version | Only needed to build the .exe |

Install the modules in one command:

```
pip install -r requirements.txt
```

## Download

Grab `FontPreview.exe` from the [Releases](../../releases) page. It's a single file, no installation needed.

Windows may show "Windows protected your PC" the first time, because the app isn't signed. Click **More info → Run anyway**.

## Run from source

```
pip install -r requirements.txt
python font_preview.py
```

### Build the .exe yourself

```
pip install pyinstaller
pyinstaller --onefile --windowed --name FontPreview font_preview.py
```

The executable lands in `dist\FontPreview.exe`.

## Keyboard shortcuts

| Key | Action |
|---|---|
| Arrows | Move between fonts |
| Space | Add / remove favorite |
| `[` `]` | Previous / next style |
| Enter | Open style menu |
| H | Hide / unhide font |
| Ctrl+C | Copy font name |
| Ctrl+Shift+C | Copy preview as image |
| Ctrl+F | Search |
| Ctrl+L | Edit sample text |
| Ctrl+1 / 2 / 3 | List / Grid / Favorites |
| Ctrl+E | Export shortlist |
| Ctrl+\ | Show / hide sidebar |

## Notes

- Settings are saved in `.fontpreview_settings.json` in your user folder. The font index cache is `.fontpreview_cache.json`, and it's safe to delete (it rebuilds on the next launch).
- "My fonts" shows everything installed after Windows itself, so fonts added by other software (like Microsoft Office) may appear there. Hide them once with **H** and they stay hidden.
