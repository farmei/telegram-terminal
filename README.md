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
- `$tt version`, `$tt ping`, `$tt uptime`, `$tt about`

## Installation

Clone the repository:

- `git clone https://github.com/farmei/telegram-terminal.git`
- `cd telegram-terminal`

Create and activate a virtual environment:

- `python3 -m venv remoteenv`
- `source remoteenv/bin/activate`

Install dependencies:

- `pip install -r requirements.txt`

## Telegram API Setup

Get your Telegram API credentials from `https://my.telegram.org/apps`.

Steps:

- Open `https://my.telegram.org/apps`
- Log in with your Telegram phone number
- Create an application
- Copy the `api_id` and `api_hash`

Set the credentials in `telegram-terminal.py`:

- `api_id = 123456`
- `api_hash = "your_api_hash"`

## Run

Start the bot:

- `source remoteenv/bin/activate`
- `python3 telegram-terminal.py`

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
