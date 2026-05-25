# telegram-terminal

A lightweight Telegram-based remote shell for Linux. It provides a persistent `bash` session through Telegram, with live command output, interactive terminal controls, file transfer, and a simple line-based text editor.

## Features

- Persistent shell session using `pexpect`
- Run Linux commands directly from Telegram
- Live output updates in the same Telegram message
- Large command outputs are sent automatically as `.txt` files
- Interactive controls such as Ctrl+C, Ctrl+D, Ctrl+Z, Enter, Tab, arrows, Esc, Backspace and Delete
- Upload files from Telegram to the server
- Download files from the server to Telegram
- Built-in Telegram-friendly text editor
- Editor undo, find and replace commands
- Shell status and restart commands
- Command history with rerun support
- Optional output logging to `logs/`
- Terminal-style screenshots from command output
- Run commands directly as terminal screenshots
- Screenshot command runner with optional buffer control
- Screenshot themes, titles, wide mode and file saving
- Session output buffer clearing without interrupting active commands
- Output rendering state reset between commands
- Session output buffer status

## Commands

All commands start with `$`. Built-in bot commands use namespaced prefixes like `tt`, `buf`, `cmd`, `out` and `shot`, so normal shell commands such as `$tail file.txt`, `$history`, `$nano`, `$log`, `$get` and `$ss` can still run in the terminal.

### Run Shell Commands

- `$pwd`
- `$ls -la`
- `$cd /tmp`
- `$python3 script.py`

### Terminal Controls

- `$ctrlc`
- `$ctrl c`
- `$ctrl-c`
- `$ctrl+c`
- `$ctrld`
- `$ctrl d`
- `$ctrlz`
- `$enter`
- `$tab`
- `$up`
- `$down`
- `$left`
- `$right`
- `$key esc`
- `$key backspace`
- `$key delete`
- `$key home`
- `$key end`
- `$key pgup`
- `$key pgdn`

### Send Input

- `$ttinput your text here`
- `$ttpaste raw text without pressing enter`

### File Download

Send a file from the server to Telegram:

- `$ttget /path/to/file.txt`

### File Upload

Send a Telegram document with this caption:

- `$ttput /path/to/save/file.txt`

### Text Editor

Open a file:

- `$ttedit open file.txt`

Editor commands:

- `$ttedit show`
- `$ttedit set 3 new content for line 3`
- `$ttedit insert 3 inserted before line 3`
- `$ttedit append new line at the end`
- `$ttedit delete 5`
- `$ttedit delete 5-10`
- `$ttedit undo`
- `$ttedit find token`
- `$ttedit replace old new`
- `$ttedit replace-all old new`
- `$ttedit save`
- `$ttedit cancel`

### Session Output

These commands use the accumulated output buffer from the current bot session. `$buf clear` clears only the session buffer and does not interrupt a running command:

- `$buf tail`
- `$buf tail 200`
- `$buf tail full`
- `$shot`
- `$shot 80`
- `$shot wide`
- `$shot wide 80`
- `$shot clear`
- `$shot file output.png`
- `$shot title my-server`
- `$shot theme green`
- `$shot theme white`
- `$shot theme amber`
- `$shot run neofetch`
- `$shot run clear neofetch`
- `$shot run --no-session neofetch`
- `$shot run file neofetch.png neofetch`
- `$shot run ls -la`
- `$buf send`
- `$buf send output.txt`
- `$buf clear`
- `$buf status`

Large command outputs are still sent automatically as `.txt` files when a command finishes.

### Utility Commands

- `$tt help`
- `$tt status`
- `$tt restart`
- `$tt version`
- `$tt ping`
- `$cmd history`
- `$cmd history 50`
- `$cmd last`
- `$cmd rerun 3`
- `$out log on`
- `$out log off`
- `$out log status`

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
- `$pwd`
- `$shot run neofetch`

## License

MIT
