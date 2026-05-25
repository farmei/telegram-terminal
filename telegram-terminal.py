import asyncio
import time
import re
import shlex
import tempfile
from datetime import datetime
from pathlib import Path

import pexpect

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError

api_id = 123456
api_hash = "12344567abcdefghijklmnop"

client = TelegramClient(
    "telegram_shell",
    api_id,
    api_hash
)

EDIT_INTERVAL = 3
MAX_MESSAGE_OUTPUT = 3500
MAX_BUFFER_SIZE = 200000

DONE_MARKER = "__TCM_DONE_982741__"

shell = pexpect.spawn(
    "bash",
    encoding="utf-8",
    echo=False
)

shell.delaybeforesend = 0

current_msg = None

output_buffer = ""

editor_state = None

ansi_escape = re.compile(
    r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])'
)


def clean_output(text):
    return ansi_escape.sub('', text)



def tg_code(text):
    safe = str(text).replace("```", "`\u200b``")
    return f"```{safe}```"


def build_help():
    return """Telegram shell commands:
$<command>              Run a shell command
$ctrlc / $ctrl c       Send Ctrl+C
$ctrld                  Send Ctrl+D
$ctrlz                  Send Ctrl+Z
$enter                  Send Enter
$tab                    Send Tab
$up/$down/$left/$right  Send arrow keys
$input <text>           Send one input line
$key <name>             Send a key: esc, backspace, delete, home, end, pgup, pgdn
$paste <text>           Paste raw text into the shell
$restart-shell          Restart the persistent bash session
$status                 Show shell/editor status
$tail [lines|full]      Show recent output buffer
$get <file>             Send a file from the server
$edit <file>            Open Telegram text editor
$nano <file>            Alias for $edit
$e show                 Show editor buffer
$e set N <text>         Replace line N
$e insert N <text>      Insert before line N
$e append <text>        Append a line
$e delete N[-M]         Delete line or range
$e save                 Save file
$e cancel               Close editor without saving

Send a document with caption "$put <path>" to upload it."""


def editor_preview(max_chars=3300):
    if not editor_state:
        return "No file is open. Use $edit <file> first."

    lines = editor_state["lines"]
    path = editor_state["path"]
    dirty = "modified" if editor_state["dirty"] else "saved"
    header = f"Editing: {path} ({len(lines)} lines, {dirty})\n"
    header += "Commands: $e show | $e set N text | $e insert N text | $e delete N[-M] | $e save | $e cancel\n\n"
    body = "\n".join(f"{idx:4}: {line}" for idx, line in enumerate(lines, start=1))
    preview = header + body

    if len(preview) > max_chars:
        preview = preview[:max_chars] + "\n... (preview truncated; file is still loaded)"

    return preview


def parse_line_range(value, total_lines):
    value = value.strip()

    if not value:
        raise ValueError("missing line number")

    if "-" in value:
        start_text, end_text = value.split("-", 1)
        start = int(start_text)
        end = int(end_text)
    else:
        start = end = int(value)

    if start < 1 or end < start or end > total_lines:
        raise ValueError(f"line range must be between 1 and {total_lines}")

    return start, end


def split_command_args(command):
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()



def tail_output(arg=""):
    if not output_buffer:
        return "Output buffer is empty."

    arg = arg.strip().lower()

    if arg == "full":
        return output_buffer

    if arg:
        try:
            line_count = max(1, int(arg))
        except ValueError:
            line_count = 80
    else:
        line_count = 80

    return "\n".join(output_buffer.splitlines()[-line_count:])


async def send_text_file(event, content, filename="telegram-terminal-output.txt", message="Output attached as text file."):
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)

    final_path = tmp_path.with_name(filename)
    tmp_path.replace(final_path)

    try:
        await event.reply(message, file=str(final_path))
    finally:
        try:
            final_path.unlink()
        except OSError:
            pass


async def handle_editor_command(event, command):
    global editor_state

    if command.startswith("edit ") or command.startswith("nano "):
        _, path_text = command.split(maxsplit=1)
        path = Path(path_text).expanduser()

        try:
            if path.exists():
                content = path.read_text(encoding="utf-8", errors="replace")
                lines = content.splitlines()
            else:
                lines = []

            editor_state = {
                "path": path,
                "lines": lines,
                "dirty": False,
            }

            await event.reply(tg_code(editor_preview()))

        except Exception as e:
            await event.reply(tg_code(f"Editor open error:\n{e}"))

        return True

    if not command.startswith("e "):
        return False

    if not editor_state:
        await event.reply(tg_code("No file is open. Use $edit <file> first."))
        return True

    action_text = command[2:].strip()

    if not action_text:
        await event.reply(tg_code(editor_preview()))
        return True

    action, _, rest = action_text.partition(" ")
    action = action.lower()

    try:
        lines = editor_state["lines"]

        if action in ("show", "ls", "view"):
            await event.reply(tg_code(editor_preview()))

        elif action in ("set", "replace"):
            line_text, _, new_text = rest.partition(" ")
            line_no = int(line_text)

            if line_no < 1 or line_no > len(lines):
                raise ValueError(f"line must be between 1 and {len(lines)}")

            lines[line_no - 1] = new_text
            editor_state["dirty"] = True
            await event.reply(tg_code(f"Line {line_no} updated.\n\n{editor_preview()}"))

        elif action in ("insert", "ins"):
            line_text, _, new_text = rest.partition(" ")
            line_no = int(line_text)

            if line_no < 1 or line_no > len(lines) + 1:
                raise ValueError(f"line must be between 1 and {len(lines) + 1}")

            lines.insert(line_no - 1, new_text)
            editor_state["dirty"] = True
            await event.reply(tg_code(f"Inserted at line {line_no}.\n\n{editor_preview()}"))

        elif action in ("append", "add"):
            lines.append(rest)
            editor_state["dirty"] = True
            await event.reply(tg_code(f"Appended at line {len(lines)}.\n\n{editor_preview()}"))

        elif action in ("delete", "del", "rm"):
            if not lines:
                raise ValueError("file is empty")

            start, end = parse_line_range(rest, len(lines))
            del lines[start - 1:end]
            editor_state["dirty"] = True
            await event.reply(tg_code(f"Deleted line(s) {start}-{end}.\n\n{editor_preview()}"))

        elif action == "save":
            path = editor_state["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            content = "\n".join(lines) + ("\n" if lines else "")
            path.write_text(content, encoding="utf-8")
            editor_state["dirty"] = False
            await event.reply(tg_code(f"Saved: {path}"))

        elif action in ("cancel", "close", "quit"):
            path = editor_state["path"]
            dirty = editor_state["dirty"]
            editor_state = None
            suffix = "Unsaved changes discarded." if dirty else "Editor closed."
            await event.reply(tg_code(f"{suffix}\n{path}"))

        else:
            await event.reply(tg_code(editor_preview()))

    except Exception as e:
        await event.reply(tg_code(f"Editor error:\n{e}"))

    return True


async def send_file(event, command):
    args = split_command_args(command)

    if len(args) < 2:
        await event.reply(tg_code("Usage: $get <file>"))
        return True

    path = Path(args[1]).expanduser()

    if not path.is_file():
        await event.reply(tg_code(f"File not found: {path}"))
        return True

    await event.reply(file=str(path), message=f"File: {path}")
    return True


async def receive_file(event, command):
    args = split_command_args(command)

    if len(args) < 2:
        await event.reply(tg_code("Usage: send a document with caption '$put <path>'"))
        return True

    if not event.message.file:
        await event.reply(tg_code("Attach a document and use caption: $put <path>"))
        return True

    path = Path(args[1]).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    await event.message.download_media(file=str(path))
    await event.reply(tg_code(f"Uploaded: {path}"))
    return True


def restart_shell():
    global shell

    try:
        if shell.isalive():
            shell.terminate(force=True)
    except Exception:
        pass

    shell = pexpect.spawn("bash", encoding="utf-8", echo=False)
    shell.delaybeforesend = 0


def shell_status():
    status = "alive" if shell.isalive() else "dead"
    editor = "none"

    if editor_state:
        dirty = "modified" if editor_state["dirty"] else "saved"
        editor = f"{editor_state['path']} ({dirty})"

    return f"Shell: {status}\nEditor: {editor}\nBuffer: {len(output_buffer)} chars"


async def stream_shell_output():

    global current_msg
    global output_buffer

    last_edit = 0
    last_text = ""

    while True:

        await asyncio.sleep(0.03)

        try:

            if shell.isalive():

                data = shell.read_nonblocking(
                    size=4096,
                    timeout=0.01
                )

                if data:

                    cleaned = clean_output(data)

                    command_finished = False

                    if DONE_MARKER in cleaned:

                        cleaned = cleaned.replace(
                            DONE_MARKER,
                            ""
                        )

                        command_finished = True

                    output_buffer += cleaned

                    output_buffer = output_buffer[-MAX_BUFFER_SIZE:]

                    now = time.time()

                    trimmed = output_buffer[-MAX_MESSAGE_OUTPUT:]

                    if command_finished:

                        if (
                            current_msg and
                            trimmed != last_text
                        ):

                            try:

                                if len(output_buffer) > MAX_MESSAGE_OUTPUT:
                                    await current_msg.edit(
                                        tg_code(trimmed + "\n\nOutput is large. Sending full output as .txt...")
                                    )
                                    await send_text_file(
                                        current_msg,
                                        output_buffer,
                                        "telegram-terminal-output.txt",
                                        "Full output:"
                                    )
                                else:
                                    await current_msg.edit(
                                        tg_code(trimmed)
                                    )

                                last_text = trimmed
                                last_edit = now

                            except Exception as e:

                                print(
                                    f"Final Flush Error: {e}"
                                )

                    elif (
                        current_msg and
                        now - last_edit >= EDIT_INTERVAL
                    ):

                        if trimmed != last_text:

                            try:

                                await current_msg.edit(
                                    tg_code(trimmed)
                                )

                                last_text = trimmed
                                last_edit = now

                            except FloodWaitError as e:

                                print(
                                    f"FloodWait: "
                                    f"{e.seconds}s"
                                )

                                await asyncio.sleep(
                                    e.seconds
                                )

                            except Exception as e:

                                print(
                                    f"Edit Error: {e}"
                                )

        except pexpect.exceptions.TIMEOUT:
            pass

        except pexpect.exceptions.EOF:
            print("Shell EOF; restarting shell")
            restart_shell()
            last_text = ""
            last_edit = 0

        except Exception as e:
            print(f"Stream Error: {e}")


@client.on(events.NewMessage)
async def shell_handler(event):

    global current_msg
    global output_buffer

    if not event.out:
        return

    text = event.raw_text.strip()

    if not text.startswith("$"):
        return

    command = text[1:].strip()

    if not command:
        return

    command_key = command.lower().replace("+", " ").replace("-", " ")
    command_key = " ".join(command_key.split())

    aliases = {
        "ctrl c": "ctrlc",
        "control c": "ctrlc",
        "ctrl d": "ctrld",
        "control d": "ctrld",
        "ctrl z": "ctrlz",
        "control z": "ctrlz",
        "seta cima": "up",
        "seta baixo": "down",
        "seta esquerda": "left",
        "seta direita": "right",
    }

    command_key = aliases.get(command_key, command_key)

    current_time = datetime.now().strftime("%H:%M:%S")

    if command_key in ("help", "h", "?"):
        await event.reply(tg_code(build_help()))
        return

    if command_key == "status":
        await event.reply(tg_code(shell_status()))
        return

    if command_key == "tail" or command_key.startswith("tail "):
        tail_arg = command[4:].strip()
        content = tail_output(tail_arg)

        if len(content) > MAX_MESSAGE_OUTPUT:
            await send_text_file(
                event,
                content,
                "telegram-terminal-tail.txt",
                "Tail output:"
            )
        else:
            await event.reply(tg_code(content))

        return

    if command_key in ("restart shell", "restart-shell"):
        restart_shell()
        output_buffer = ""
        current_msg = None
        await event.reply(tg_code("Shell restarted."))
        return

    if await handle_editor_command(event, command):
        return

    if command.startswith("get "):
        await send_file(event, command)
        return

    if command.startswith("put "):
        await receive_file(event, command)
        return

    control_sequences = {
        "ctrlc": "\x03",
        "ctrld": "\x04",
        "ctrlz": "\x1a",
        "tab": "\t",
        "up": "\x1b[A",
        "down": "\x1b[B",
        "right": "\x1b[C",
        "left": "\x1b[D",
    }

    named_keys = {
        "esc": "\x1b",
        "backspace": "\x7f",
        "delete": "\x1b[3~",
        "home": "\x1b[H",
        "end": "\x1b[F",
        "pgup": "\x1b[5~",
        "pgdn": "\x1b[6~",
    }

    if command_key == "enter":
        try:
            shell.sendline("")
            print(f"[{current_time}] You Sent ENTER")
            await event.reply(tg_code("ENTER sent"))
        except Exception as e:
            await event.reply(tg_code(f"ENTER Error:\n{e}"))
        return

    if command_key in control_sequences:
        try:
            shell.send(control_sequences[command_key])
            print(f"[{current_time}] You Sent {command_key.upper()}")
            await event.reply(tg_code(f"{command_key.upper()} sent"))
        except Exception as e:
            await event.reply(tg_code(f"Control Error:\n{e}"))
        return

    if command.startswith("key "):
        key_name = command[4:].strip().lower()

        if key_name not in named_keys:
            await event.reply(tg_code(f"Unknown key: {key_name}"))
            return

        try:
            shell.send(named_keys[key_name])
            await event.reply(tg_code(f"{key_name.upper()} sent"))
        except Exception as e:
            await event.reply(tg_code(f"Key Error:\n{e}"))
        return

    if command.startswith("paste "):
        try:
            pasted = command[6:]
            shell.send(pasted)
            await event.reply(tg_code(f"Pasted {len(pasted)} chars"))
        except Exception as e:
            await event.reply(tg_code(f"Paste Error:\n{e}"))
        return

    if command.startswith("input "):

        user_input = command[6:]

        try:

            shell.sendline(user_input)

            print(
                f"[{current_time}] "
                f"Input: {user_input}"
            )

            await event.reply(
                tg_code(f"Input Sent:\n{user_input}")
            )

        except Exception as e:

            await event.reply(
                tg_code(f"Input Error:\n{e}")
            )

        return

    print(
        f"[{current_time}] "
        f"You Executed: {command}"
    )

    output_buffer = ""

    current_msg = await event.reply(
        tg_code(f"Running:\n{command}")
    )

    try:

        builtin_commands = [
            "cd",
            "export",
            "alias",
            "source",
            "set",
            "unset",
            "history",
            "exit"
        ]

        first_word = command.strip().split()[0]

        if first_word in builtin_commands:

            shell.sendline(
                f"{command}; echo {DONE_MARKER}"
            )

        else:

            shell.sendline(
                f"stdbuf -oL -eL {command}; "
                f"echo {DONE_MARKER}"
            )

    except Exception as e:

        await current_msg.edit(
            tg_code(f"Execution Error:\n{e}")
        )


async def main():

    print("Bhai shell is running...")

    asyncio.create_task(
        stream_shell_output()
    )

    await client.start()

    await client.run_until_disconnected()

asyncio.run(main())
