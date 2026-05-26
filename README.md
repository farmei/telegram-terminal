# telegram-terminal

A lightweight Telegram-based remote shell for Linux. It provides a persistent `bash` session through Telegram, with live command output, interactive terminal controls, file transfer, and a simple line-based text editor.

![telegram-terminal preview](assets/preview.png)

## Features

- Persistent `bash` session over Telegram
- Live command output with automatic full `.txt` export for large outputs
- Topic-safe file and screenshot replies in forum groups
- Interactive terminal controls such as Ctrl+C, Ctrl+D, Enter, Tab and arrow keys
- Upload/download files between Telegram and the server
- Built-in line editor with undo, find and replace
- Command history, rerun and optional output logging
- Xterm-style screenshots with VT100/ANSI emulation, scrollback and bundled monospace font
- Shell watchdog, uptime, status, restart and runtime reset commands

## Commands

All commands start with `$`. Built-in commands use namespaced prefixes like `tt`, `buf`, `cmd`, `out` and `shot`, so normal shell commands such as `$tail file.txt`, `$history`, `$nano`, `$log`, `$get` and `$ss` can still run in the terminal.

### Shell

- `$<command>`
- `$ttinput your text here`
- `$ttpaste raw text without pressing enter`

### Terminal Keys

- `$ctrlc`, `$ctrl c`, `$ctrld`, `$ctrlz`
- `$enter`, `$tab`
- `$up`, `$down`, `$left`, `$right`
- `$key esc`, `$key backspace`, `$key delete`, `$key home`, `$key end`, `$key pgup`, `$key pgdn`

### Screenshots

- `$shot`, `$shot 80`
- `$shot wide`, `$shot wide 80`
- `$shot clear`
- `$shot run neofetch`
- `$shot run wide btop`
- `$shot run clear neofetch`
- `$shot run --no-session neofetch`

`$shot` renders the current xterm-compatible virtual screen with scrollback. `$shot run` appends to the existing virtual screen like a normal terminal; use `$buf clear` or `$shot clear` when you want a clean screen.

### Buffers

- `$buf tail`, `$buf tail 200`, `$buf tail full`
- `$buf send`, `$buf send output.txt`
- `$buf save output.txt`
- `$tt save-session`, `$tt save-session session.txt`
- `$buf clear`, `$buf status`

Large command outputs are sent automatically as full `.txt` files when a command finishes.

### Files

- `$ttget /path/to/file.txt`
- `$ttput /path/to/save/file.txt`

Send a Telegram document with caption `$ttput /path/to/save/file.txt` to upload it.

### Editor

- `$ttedit open file.txt`, `$ttedit show`
- `$ttedit set 3 new content for line 3`
- `$ttedit insert 3 inserted before line 3`
- `$ttedit append new line at the end`
- `$ttedit delete 5`, `$ttedit delete 5-10`
- `$ttedit undo`, `$ttedit find token`
- `$ttedit replace old new`, `$ttedit replace-all old new`
- `$ttedit save`, `$ttedit cancel`

### History And Logs

- `$cmd history`, `$cmd history 50`
- `$cmd last`, `$cmd rerun 3`
- `$out log on`, `$out log off`, `$out log status`

### Bot

- `$tt help`, `$tt status`, `$tt restart`, `$tt reset`
- `$tt version`, `$tt ping`, `$tt uptime`, `$tt uptime bot`, `$tt uptime system`, `$tt about`

## Installation

`telegram-terminal` runs on Linux with Python 3 and `bash`.

Before installing the Python dependencies, make sure your system has:

- `git`
- Python 3 with `venv` and `pip`
- `bash`
- FreeType, JPEG and zlib development libraries for Pillow screenshots
- C/build tools if your distro builds Pillow from source

Clone the repository:

- `git clone https://github.com/farmei/telegram-terminal.git`
- `cd telegram-terminal`

Create and activate a virtual environment:

- `python3 -m venv remoteenv`
- `source remoteenv/bin/activate`

Install Python dependencies:

- `pip install --upgrade pip setuptools wheel`
- `pip install -r requirements.txt`

Test whether Pillow can load the bundled monospace font used by `$shot`:

```bash
python - <<'PY'
from PIL import ImageFont
font = ImageFont.truetype("assets/fonts/DejaVuSansMono.ttf", 16)
print("Pillow FreeType OK:", font)
PY
```

If the test fails with `_imagingft`, install the FreeType development package for your distro, then reinstall Pillow inside the virtual environment:

- `pip uninstall -y pillow`
- `pip install --no-cache-dir --force-reinstall pillow`

## Termux / Android Setup

Termux can run `telegram-terminal`, but screenshots need Pillow with FreeType support. If Pillow is installed without FreeType, `$shot` can fail with this error:

- `ImportError: cannot import name '_imagingft' from 'PIL'`

For a clean Termux install, install the native libraries before installing Python dependencies:

- `pkg update`
- `pkg install python git freetype libjpeg-turbo zlib clang make pkg-config`
- `git clone https://github.com/farmei/telegram-terminal.git`
- `cd telegram-terminal`
- `python -m venv remoteenv`
- `source remoteenv/bin/activate`
- `pip install --upgrade pip setuptools wheel`
- `pip install --no-cache-dir -r requirements.txt`

Test whether Pillow can load the bundled monospace font:

```bash
python - <<'PY'
from PIL import ImageFont
font = ImageFont.truetype("assets/fonts/DejaVuSansMono.ttf", 16)
print("Pillow FreeType OK:", font)
PY
```

If the test fails with `_imagingft`, reinstall Pillow after installing FreeType:

- `pkg install freetype libjpeg-turbo zlib`
- `pip uninstall -y pillow`
- `pip install --no-cache-dir --force-reinstall pillow`

If it still fails, use Termux's packaged Pillow instead of the pip wheel/build:

- `pip uninstall -y pillow`
- `deactivate`
- `pkg install python-pillow`

Then run the bot without the virtual environment, or recreate the environment with access to system site packages:

- `python -m venv --system-site-packages remoteenv`
- `source remoteenv/bin/activate`
- `pip install telethon pexpect pyte`

The bot has a fallback for broken TrueType support and will keep running with Pillow's default font, but terminal screenshots may look less aligned until `_imagingft` is available.

## Telegram API Setup

Get your Telegram API credentials from `https://my.telegram.org/apps`.

Steps:

- Open `https://my.telegram.org/apps`
- Log in with your Telegram phone number
- Create an application
- Copy the `api_id` and `api_hash`

Pass the credentials with environment variables when starting the bot:

- `TG_API_ID=123456 TG_API_HASH=your_api_hash python3 telegram-terminal.py`

You can also edit `api_id` and `api_hash` directly in `telegram-terminal.py` if you prefer.

## Run

Start the bot:

- `source remoteenv/bin/activate`
- `TG_API_ID=123456 TG_API_HASH=your_api_hash python3 telegram-terminal.py`

On the first run, Telegram will ask for login confirmation and create a local session file. After login, send commands from your own Telegram account using the `$` prefix.

Example:

- `$tt ping`
- `$tt uptime`
- `$tt about`
- `$pwd`
- `$shot run neofetch`

## Version

Current version: `1.2.0`

## License

MIT
