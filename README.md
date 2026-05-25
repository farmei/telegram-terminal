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
- Shell status and restart commands
- Command history with rerun support
- Optional output logging to `logs/`
- Terminal-style screenshots from command output

## Commands

All commands start with `$`.

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

- `$input your text here`
- `$paste raw text without pressing enter`

### File Download

Send a file from the server to Telegram:

- `$get /path/to/file.txt`

### File Upload

Send a Telegram document with this caption:

- `$put /path/to/save/file.txt`

### Text Editor

Open a file:

- `$edit file.txt`
- `$nano file.txt`

Editor commands:

- `$e show`
- `$e set 3 new content for line 3`
- `$e insert 3 inserted before line 3`
- `$e append new line at the end`
- `$e delete 5`
- `$e delete 5-10`
- `$e save`
- `$e cancel`

### Session Output

These commands use the accumulated output buffer from the current bot session:

- `$tail`
- `$tail 200`
- `$tail full`
- `$ss`
- `$ss 80`
- `$sendout`
- `$sendout output.txt`

Large command outputs are still sent automatically as `.txt` files when a command finishes.

### Utility Commands

- `$help`
- `$status`
- `$history`
- `$history 50`
- `$last`
- `$rerun 3`
- `$log on`
- `$log off`
- `$log status`
- `$restart-shell`

## Installation

- `python3 -m venv remoteenv`
- `source remoteenv/bin/activate`
- `pip install telethon pexpect`

## Configuration

Set your Telegram API credentials in `telegram-terminal.py`:

- `api_id = 123456`
- `api_hash = "your_api_hash"`

Get Telegram API credentials from `https://my.telegram.org/apps`.

Steps:

- Open `https://my.telegram.org/apps`
- Log in with your Telegram phone number
- Create an application
- Copy the `api_id` and `api_hash`
- Put them in `telegram-terminal.py`

## Run

- `source remoteenv/bin/activate`
- `python3 telegram-terminal.py`

On the first run, Telegram will ask for login confirmation and create a local session file.

## License

MIT
