import asyncio
import time
import re
import shlex
import struct
import tempfile
import zlib
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

VERSION = "1.0.0"
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
current_event = None

output_buffer = ""
command_output_buffer = ""
output_revision = 0

editor_state = None

command_history = []
last_command = None
log_enabled = False
current_log_path = None
current_output_mode = "chat"
current_output_no_session = False
current_shot_clear_after = False
current_shot_save_path = None
shot_theme = "green"
shot_title = "telegram-terminal"

SHOT_THEMES = {
    "green": {
        "bg": (8, 11, 16),
        "bar": (24, 30, 39),
        "line": (42, 52, 65),
        "title": (226, 232, 240),
        "text": (220, 255, 226),
    },
    "white": {
        "bg": (10, 14, 20),
        "bar": (28, 34, 44),
        "line": (58, 67, 82),
        "title": (238, 242, 247),
        "text": (235, 239, 245),
    },
    "amber": {
        "bg": (16, 12, 5),
        "bar": (42, 31, 15),
        "line": (82, 58, 22),
        "title": (255, 236, 179),
        "text": (255, 213, 128),
    },
}

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
$tt help               Show telegram-terminal help
$tt status             Show shell/editor status
$tt restart            Restart the persistent bash session
$tt version            Show telegram-terminal version
$tt ping               Check bot latency
$ctrlc / $ctrl c       Send Ctrl+C
$ctrld                  Send Ctrl+D
$ctrlz                  Send Ctrl+Z
$enter                  Send Enter
$tab                    Send Tab
$up/$down/$left/$right  Send arrow keys
$key <name>             Send a key: esc, backspace, delete, home, end, pgup, pgdn
$ttpaste <text>         Paste raw text into the shell
$ttinput <text>         Send one input line
$buf tail [lines|full] Show recent output buffer
$buf send [file.txt]   Send output buffer as .txt
$buf clear             Clear session output buffer
$buf status            Show output buffer status
$shot [lines]          Send output as terminal image
$shot wide [lines]     Send wider output image
$shot clear [lines]    Send image and clear buffer
$shot file <path.png>  Save and send output image
$shot title <text>     Set screenshot title
$shot theme <name>     Set theme: green, white, amber
$shot run <command>    Run command and send output image
$shot run clear <cmd>  Run, send image, clear buffer
$shot run --no-session <cmd> Run without adding output to session buffer
$cmd history           Show command history
$cmd last              Show last shell command
$cmd rerun N           Run command from history
$out log on/off/status Save command outputs to logs/
$ttget <file>          Send a file from the server
$ttput <path>          Upload document to server
$ttedit open <file>    Open Telegram text editor
$ttedit show           Show editor buffer
$ttedit set N <text>   Replace line N
$ttedit insert N text  Insert before line N
$ttedit append <text>  Append a line
$ttedit delete N[-M]   Delete line or range
$ttedit undo           Undo editor change
$ttedit find <text>    Find text in open file
$ttedit replace old new Replace first match
$ttedit replace-all old new Replace all matches
$ttedit save           Save file
$ttedit cancel         Close editor without saving

Send a document with caption "$ttput <path>" to upload it."""


def editor_preview(max_chars=3300):
    if not editor_state:
        return "No file is open. Use $ttedit open <file> first."

    lines = editor_state["lines"]
    path = editor_state["path"]
    dirty = "modified" if editor_state["dirty"] else "saved"
    header = f"Editing: {path} ({len(lines)} lines, {dirty})\n"
    header += "Commands: $ttedit show | $ttedit set N text | $ttedit insert N text | $ttedit delete N[-M] | $ttedit save | $ttedit cancel\n\n"
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



def history_preview(limit=30):
    if not command_history:
        return "History is empty."

    items = command_history[-limit:]
    offset = len(command_history) - len(items)
    return "\n".join(f"{offset + idx + 1}: {cmd}" for idx, cmd in enumerate(items))


def create_log_path(command):
    logs_dir = Path("logs")
    logs_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^a-zA-Z0-9_.-]+", "_", command.strip())[:60].strip("_")

    if not safe_name:
        safe_name = "command"

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return logs_dir / f"{stamp}-{safe_name}.txt"


def write_command_log(command, content, path):
    if not path:
        return

    header = f"Command: {command}\nTime: {datetime.now().isoformat(timespec='seconds')}\n\n"
    path.write_text(header + content, encoding="utf-8", errors="replace")


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



FONT_5X7 = {
    " ": [0, 0, 0, 0, 0, 0, 0],
    "!": [4, 4, 4, 4, 4, 0, 4],
    "\"": [10, 10, 10, 0, 0, 0, 0],
    "#": [10, 10, 31, 10, 31, 10, 10],
    "$": [4, 15, 20, 14, 5, 30, 4],
    "%": [24, 25, 2, 4, 8, 19, 3],
    "&": [12, 18, 20, 8, 21, 18, 13],
    "'": [4, 4, 8, 0, 0, 0, 0],
    "(": [2, 4, 8, 8, 8, 4, 2],
    ")": [8, 4, 2, 2, 2, 4, 8],
    "*": [0, 4, 21, 14, 21, 4, 0],
    "+": [0, 4, 4, 31, 4, 4, 0],
    ",": [0, 0, 0, 0, 4, 4, 8],
    "-": [0, 0, 0, 31, 0, 0, 0],
    ".": [0, 0, 0, 0, 0, 12, 12],
    "/": [1, 2, 4, 8, 16, 0, 0],
    "0": [14, 17, 19, 21, 25, 17, 14],
    "1": [4, 12, 4, 4, 4, 4, 14],
    "2": [14, 17, 1, 2, 4, 8, 31],
    "3": [30, 1, 1, 14, 1, 1, 30],
    "4": [2, 6, 10, 18, 31, 2, 2],
    "5": [31, 16, 16, 30, 1, 1, 30],
    "6": [14, 16, 16, 30, 17, 17, 14],
    "7": [31, 1, 2, 4, 8, 8, 8],
    "8": [14, 17, 17, 14, 17, 17, 14],
    "9": [14, 17, 17, 15, 1, 1, 14],
    ":": [0, 12, 12, 0, 12, 12, 0],
    ";": [0, 12, 12, 0, 4, 4, 8],
    "<": [2, 4, 8, 16, 8, 4, 2],
    "=": [0, 0, 31, 0, 31, 0, 0],
    ">": [8, 4, 2, 1, 2, 4, 8],
    "?": [14, 17, 1, 2, 4, 0, 4],
    "@": [14, 17, 1, 13, 21, 21, 14],
    "A": [14, 17, 17, 31, 17, 17, 17],
    "B": [30, 17, 17, 30, 17, 17, 30],
    "C": [14, 17, 16, 16, 16, 17, 14],
    "D": [30, 17, 17, 17, 17, 17, 30],
    "E": [31, 16, 16, 30, 16, 16, 31],
    "F": [31, 16, 16, 30, 16, 16, 16],
    "G": [14, 17, 16, 23, 17, 17, 14],
    "H": [17, 17, 17, 31, 17, 17, 17],
    "I": [14, 4, 4, 4, 4, 4, 14],
    "J": [7, 2, 2, 2, 18, 18, 12],
    "K": [17, 18, 20, 24, 20, 18, 17],
    "L": [16, 16, 16, 16, 16, 16, 31],
    "M": [17, 27, 21, 21, 17, 17, 17],
    "N": [17, 25, 21, 19, 17, 17, 17],
    "O": [14, 17, 17, 17, 17, 17, 14],
    "P": [30, 17, 17, 30, 16, 16, 16],
    "Q": [14, 17, 17, 17, 21, 18, 13],
    "R": [30, 17, 17, 30, 20, 18, 17],
    "S": [15, 16, 16, 14, 1, 1, 30],
    "T": [31, 4, 4, 4, 4, 4, 4],
    "U": [17, 17, 17, 17, 17, 17, 14],
    "V": [17, 17, 17, 17, 17, 10, 4],
    "W": [17, 17, 17, 21, 21, 21, 10],
    "X": [17, 17, 10, 4, 10, 17, 17],
    "Y": [17, 17, 10, 4, 4, 4, 4],
    "Z": [31, 1, 2, 4, 8, 16, 31],
    "[": [14, 8, 8, 8, 8, 8, 14],
    "\\": [16, 8, 4, 2, 1, 0, 0],
    "]": [14, 2, 2, 2, 2, 2, 14],
    "^": [4, 10, 17, 0, 0, 0, 0],
    "_": [0, 0, 0, 0, 0, 0, 31],
    "`": [8, 4, 2, 0, 0, 0, 0],
    "{": [2, 4, 4, 8, 4, 4, 2],
    "|": [4, 4, 4, 0, 4, 4, 4],
    "}": [8, 4, 4, 2, 4, 4, 8],
    "~": [0, 0, 8, 21, 2, 0, 0],
}

for char in "abcdefghijklmnopqrstuvwxyz":
    FONT_5X7[char] = FONT_5X7[char.upper()]


def png_chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)


def write_png(path, width, height, pixels):
    raw = bytearray()

    for y in range(height):
        raw.append(0)
        start = y * width * 3
        raw.extend(pixels[start:start + width * 3])

    data = b"\x89PNG\r\n\x1a\n"
    data += png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    data += png_chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    data += png_chunk(b"IEND", b"")
    path.write_bytes(data)


def draw_rect(pixels, width, height, x1, y1, x2, y2, color):
    x1 = max(0, min(width, x1))
    x2 = max(0, min(width, x2))
    y1 = max(0, min(height, y1))
    y2 = max(0, min(height, y2))

    for y in range(y1, y2):
        row = y * width * 3
        for x in range(x1, x2):
            idx = row + x * 3
            pixels[idx:idx + 3] = bytes(color)


def draw_circle(pixels, width, height, cx, cy, radius, color):
    rr = radius * radius

    for y in range(cy - radius, cy + radius + 1):
        if y < 0 or y >= height:
            continue

        for x in range(cx - radius, cx + radius + 1):
            if x < 0 or x >= width:
                continue

            if (x - cx) ** 2 + (y - cy) ** 2 <= rr:
                idx = (y * width + x) * 3
                pixels[idx:idx + 3] = bytes(color)


def draw_text(pixels, width, height, x, y, text, color, scale=2, line_gap=2):
    cursor_x = x
    cursor_y = y
    char_width = 6 * scale
    line_height = 7 * scale + line_gap

    for char in text:
        if char == "\n":
            cursor_x = x
            cursor_y += line_height
            continue

        if char == "\t":
            cursor_x += char_width * 4
            continue

        glyph = FONT_5X7.get(char, FONT_5X7.get("?"))

        for gy, row in enumerate(glyph):
            for gx in range(5):
                if row & (1 << (4 - gx)):
                    draw_rect(
                        pixels,
                        width,
                        height,
                        cursor_x + gx * scale,
                        cursor_y + gy * scale,
                        cursor_x + (gx + 1) * scale,
                        cursor_y + (gy + 1) * scale,
                        color,
                    )

        cursor_x += char_width

        if cursor_x > width - char_width:
            cursor_x = x
            cursor_y += line_height

        if cursor_y > height - line_height:
            break


async def send_terminal_screenshot(event, content, wide=False, save_path=None):
    if not content.strip():
        content = "Output buffer is empty."

    theme = SHOT_THEMES.get(shot_theme, SHOT_THEMES["green"])
    content = clean_output(content).replace("\r", "")

    if wide:
        width = 1800
        height = 1000
        max_lines = 50
        max_cols = 210
    else:
        width = 1440
        height = 900
        max_lines = 44
        max_cols = 155

    lines = content.splitlines()[-max_lines:]
    cropped = [line[:max_cols] for line in lines]
    content = "\n".join(cropped)

    pixels = bytearray(bytes(theme["bg"]) * width * height)

    draw_rect(pixels, width, height, 0, 0, width, 54, theme["bar"])
    draw_rect(pixels, width, height, 0, 54, width, 56, theme["line"])
    draw_circle(pixels, width, height, 30, 27, 8, (255, 95, 87))
    draw_circle(pixels, width, height, 58, 27, 8, (255, 189, 46))
    draw_circle(pixels, width, height, 86, 27, 8, (40, 200, 64))
    draw_text(pixels, width, height, 120, 19, shot_title[:80], theme["title"], scale=2, line_gap=2)
    draw_text(pixels, width, height, 28, 78, content, theme["text"], scale=2, line_gap=4)

    if save_path:
        image_path = Path(save_path).expanduser()
        image_path.parent.mkdir(parents=True, exist_ok=True)
        write_png(image_path, width, height, pixels)
        await event.reply(f"Terminal screenshot saved: {image_path}", file=str(image_path))
        return

    with tempfile.TemporaryDirectory() as tmp_dir:
        image_path = Path(tmp_dir) / "telegram-terminal.png"
        write_png(image_path, width, height, pixels)
        await event.reply("Terminal screenshot:", file=str(image_path))


async def handle_editor_command(event, command):
    global editor_state

    if not command.startswith("ttedit"):
        return False

    rest = command[6:].strip()
    editor_actions = {
        "show", "ls", "view", "set", "replace", "insert", "ins", "append", "add",
        "delete", "del", "rm", "undo", "find", "replace-all", "replaceall", "save",
        "cancel", "close", "quit",
    }

    action_name = rest.split(maxsplit=1)[0].lower() if rest else ""

    if rest and (action_name not in editor_actions or action_name == "open"):
        if action_name == "open":
            _, _, path_text = rest.partition(" ")
        else:
            path_text = rest

        if not path_text:
            await event.reply(tg_code("Usage: $ttedit open <file>"))
            return True

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
                "undo": [],
            }

            await event.reply(tg_code(editor_preview()))

        except Exception as e:
            await event.reply(tg_code(f"Editor open error:\n{e}"))

        return True

    if not editor_state:
        await event.reply(tg_code("No file is open. Use $ttedit open <file> first."))
        return True

    action_text = rest

    if not action_text:
        await event.reply(tg_code(editor_preview()))
        return True

    action, _, rest = action_text.partition(" ")
    action = action.lower()

    try:
        lines = editor_state["lines"]

        def snapshot():
            editor_state["undo"].append(lines.copy())
            editor_state["undo"] = editor_state["undo"][-20:]

        if action in ("show", "ls", "view"):
            await event.reply(tg_code(editor_preview()))

        elif action == "set":
            line_text, _, new_text = rest.partition(" ")
            line_no = int(line_text)

            if line_no < 1 or line_no > len(lines):
                raise ValueError(f"line must be between 1 and {len(lines)}")

            snapshot()
            lines[line_no - 1] = new_text
            editor_state["dirty"] = True
            await event.reply(tg_code(f"Line {line_no} updated.\n\n{editor_preview()}"))

        elif action in ("insert", "ins"):
            line_text, _, new_text = rest.partition(" ")
            line_no = int(line_text)

            if line_no < 1 or line_no > len(lines) + 1:
                raise ValueError(f"line must be between 1 and {len(lines) + 1}")

            snapshot()
            lines.insert(line_no - 1, new_text)
            editor_state["dirty"] = True
            await event.reply(tg_code(f"Inserted at line {line_no}.\n\n{editor_preview()}"))

        elif action in ("append", "add"):
            snapshot()
            lines.append(rest)
            editor_state["dirty"] = True
            await event.reply(tg_code(f"Appended at line {len(lines)}.\n\n{editor_preview()}"))

        elif action in ("delete", "del", "rm"):
            if not lines:
                raise ValueError("file is empty")

            start, end = parse_line_range(rest, len(lines))
            snapshot()
            del lines[start - 1:end]
            editor_state["dirty"] = True
            await event.reply(tg_code(f"Deleted line(s) {start}-{end}.\n\n{editor_preview()}"))

        elif action == "undo":
            if not editor_state["undo"]:
                raise ValueError("nothing to undo")

            editor_state["lines"] = editor_state["undo"].pop()
            editor_state["dirty"] = True
            await event.reply(tg_code(f"Undo applied.\n\n{editor_preview()}"))

        elif action == "find":
            needle = rest

            if not needle:
                raise ValueError("usage: $ttedit find <text>")

            matches = [f"{idx}: {line}" for idx, line in enumerate(lines, start=1) if needle in line]
            await event.reply(tg_code("\n".join(matches[:80]) if matches else f"No matches: {needle}"))

        elif action in ("replace", "replace-all", "replaceall"):
            old_text, _, new_text = rest.partition(" ")

            if not old_text:
                raise ValueError("usage: $ttedit replace <old> <new>")

            count = 0
            snapshot()

            for idx, line in enumerate(lines):
                if old_text in line:
                    count += line.count(old_text)
                    lines[idx] = line.replace(old_text, new_text)

                    if action == "replace":
                        break

            if count == 0:
                editor_state["undo"].pop()
                await event.reply(tg_code(f"No matches: {old_text}"))
            else:
                editor_state["dirty"] = True
                await event.reply(tg_code(f"Replaced {count} occurrence(s).\n\n{editor_preview()}"))

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
        await event.reply(tg_code("Usage: $ttget <file>"))
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
        await event.reply(tg_code("Usage: send a document with caption '$ttput <path>'"))
        return True

    if not event.message.file:
        await event.reply(tg_code("Attach a document and use caption: $ttput <path>"))
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



def buffer_status():
    session_lines = len(output_buffer.splitlines()) if output_buffer else 0
    command_lines = len(command_output_buffer.splitlines()) if command_output_buffer else 0
    logging = "on" if log_enabled else "off"
    editor = "none"

    if editor_state:
        dirty = "modified" if editor_state["dirty"] else "saved"
        editor = f"{editor_state['path']} ({dirty})"

    log_path = str(current_log_path) if current_log_path else "none"
    last = last_command or "none"

    return (
        f"Session buffer: {len(output_buffer)} chars, {session_lines} lines\n"
        f"Current command buffer: {len(command_output_buffer)} chars, {command_lines} lines\n"
        f"Last command: {last}\n"
        f"Logging: {logging}\n"
        f"Current log: {log_path}\n"
        f"Editor: {editor}"
    )


async def stream_shell_output():

    global current_msg
    global current_event
    global output_buffer
    global command_output_buffer
    global output_revision
    global current_output_mode
    global current_output_no_session
    global current_shot_clear_after
    global current_shot_save_path

    last_edit = 0
    last_text = ""
    seen_revision = output_revision

    while True:

        await asyncio.sleep(0.03)

        if seen_revision != output_revision:
            seen_revision = output_revision
            last_edit = 0
            last_text = ""

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

                    if not current_output_no_session:
                        output_buffer += cleaned

                    command_output_buffer += cleaned

                    output_buffer = output_buffer[-MAX_BUFFER_SIZE:]
                    command_output_buffer = command_output_buffer[-MAX_BUFFER_SIZE:]

                    now = time.time()

                    trimmed = command_output_buffer[-MAX_MESSAGE_OUTPUT:]

                    if command_finished:

                        if current_msg:

                            try:

                                if current_log_path:
                                    write_command_log(last_command or "", command_output_buffer, current_log_path)

                                if current_output_mode == "ss":
                                    target_event = current_event or current_msg
                                    await send_terminal_screenshot(
                                        target_event,
                                        command_output_buffer,
                                        save_path=current_shot_save_path,
                                    )

                                    try:
                                        await current_msg.delete()
                                    except Exception:
                                        pass

                                    if current_shot_clear_after:
                                        output_buffer = ""
                                        output_revision += 1

                                    current_output_mode = "chat"
                                    current_output_no_session = False
                                    current_shot_clear_after = False
                                    current_shot_save_path = None
                                    last_text = trimmed
                                    last_edit = now
                                    continue

                                if trimmed == last_text:
                                    continue

                                if len(command_output_buffer) > MAX_MESSAGE_OUTPUT:
                                    suffix = "\n\nOutput is large. Sending full output as .txt..."

                                    if current_log_path:
                                        suffix += f"\nLog saved: {current_log_path}"

                                    await current_msg.edit(
                                        tg_code(trimmed + suffix)
                                    )
                                    await send_text_file(
                                        current_msg,
                                        command_output_buffer,
                                        "telegram-terminal-output.txt",
                                        "Full output:"
                                    )
                                else:
                                    suffix = f"\n\nLog saved: {current_log_path}" if current_log_path else ""
                                    await current_msg.edit(
                                        tg_code(trimmed + suffix)
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
    global current_event
    global output_buffer
    global command_output_buffer
    global output_revision
    global last_command
    global log_enabled
    global current_log_path
    global current_output_mode
    global current_output_no_session
    global current_shot_clear_after
    global current_shot_save_path
    global shot_theme
    global shot_title

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

    if command_key == "tt help":
        await event.reply(tg_code(build_help()))
        return

    if command_key == "tt status":
        await event.reply(tg_code(shell_status()))
        return

    if command_key == "tt version":
        await event.reply(tg_code(f"telegram-terminal {VERSION}"))
        return

    if command_key == "tt ping":
        started = time.time()
        msg = await event.reply(tg_code("pong"))
        latency = int((time.time() - started) * 1000)
        await msg.edit(tg_code(f"pong {latency}ms"))
        return

    if command_key == "buf tail" or command_key.startswith("buf tail "):
        tail_arg = command[8:].strip()
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

    if command_key == "shot theme" or command_key.startswith("shot theme "):
        theme = command[10:].strip().lower()

        if not theme:
            await event.reply(tg_code(f"Current theme: {shot_theme}\nAvailable: {', '.join(SHOT_THEMES)}"))
            return

        if theme not in SHOT_THEMES:
            await event.reply(tg_code(f"Unknown theme: {theme}\nAvailable: {', '.join(SHOT_THEMES)}"))
            return

        shot_theme = theme
        await event.reply(tg_code(f"Screenshot theme set to: {shot_theme}"))
        return

    if command_key == "shot title" or command_key.startswith("shot title "):
        title = command[10:].strip()

        if not title:
            shot_title = "telegram-terminal"
            await event.reply(tg_code("Screenshot title reset."))
        else:
            shot_title = title[:80]
            await event.reply(tg_code(f"Screenshot title set to: {shot_title}"))

        return

    if command.startswith("shot run "):
        run_text = command[9:].strip()
        current_shot_clear_after = False
        current_output_no_session = False
        current_shot_save_path = None

        while True:
            if run_text.startswith("clear "):
                current_shot_clear_after = True
                run_text = run_text[6:].strip()
                continue

            if run_text.startswith("--no-session "):
                current_output_no_session = True
                run_text = run_text[13:].strip()
                continue

            if run_text.startswith("file "):
                parts = run_text.split(maxsplit=2)

                if len(parts) < 3:
                    await event.reply(tg_code("Usage: $shot run file <path.png> <command>"))
                    return

                current_shot_save_path = parts[1]
                run_text = parts[2].strip()
                continue

            break

        command = run_text

        if not command:
            await event.reply(tg_code("Usage: $shot run <command>"))
            return

        command_key = command.lower().replace("+", " ").replace("-", " ")
        command_key = " ".join(command_key.split())
        command_key = aliases.get(command_key, command_key)
        current_output_mode = "ss"

    elif command_key == "shot run":
        await event.reply(tg_code("Usage: $shot run <command>"))
        return

    if command_key == "shot" or command_key.startswith("shot "):
        shot_args = command.split()
        wide = False
        clear_after = False
        save_path = None
        tail_arg = ""
        idx = 1

        while idx < len(shot_args):
            arg = shot_args[idx]

            if arg == "wide":
                wide = True
            elif arg == "clear":
                clear_after = True
            elif arg == "file":
                if idx + 1 >= len(shot_args):
                    await event.reply(tg_code("Usage: $shot file <path.png> [lines]"))
                    return

                save_path = shot_args[idx + 1]
                idx += 1
            elif arg not in ("run", "theme", "title"):
                tail_arg = arg

            idx += 1

        await send_terminal_screenshot(event, tail_output(tail_arg), wide=wide, save_path=save_path)

        if clear_after:
            output_buffer = ""
            output_revision += 1

        return

    if command_key == "buf send" or command_key.startswith("buf send "):
        args = command.split(maxsplit=2)
        filename = args[2].strip() if len(args) > 2 else "telegram-terminal-buffer.txt"

        if not filename.endswith(".txt"):
            filename += ".txt"

        if not output_buffer:
            await event.reply(tg_code("Output buffer is empty."))
            return

        await send_text_file(
            event,
            output_buffer,
            Path(filename).name,
            "Output buffer:"
        )
        return

    if command_key == "buf status":
        await event.reply(tg_code(buffer_status()))
        return

    if command_key == "buf clear":
        output_buffer = ""
        output_revision += 1
        await event.reply(tg_code("Session output buffer cleared."))
        return

    if command_key.startswith("buf "):
        await event.reply(tg_code("Usage: $buf status | $buf clear | $buf tail | $buf send"))
        return

    if command_key == "cmd history" or command_key.startswith("cmd history "):
        args = command.split(maxsplit=2)
        limit = 30

        if len(args) > 2:
            try:
                limit = max(1, int(args[2]))
            except ValueError:
                limit = 30

        await event.reply(tg_code(history_preview(limit)))
        return

    if command_key == "cmd last":
        await event.reply(tg_code(last_command or "No command has been executed yet."))
        return

    if command_key.startswith("cmd rerun "):
        try:
            index = int(command.split(maxsplit=2)[2])

            if index < 1 or index > len(command_history):
                raise ValueError

            command = command_history[index - 1]
            command_key = command.lower().replace("+", " ").replace("-", " ")
            command_key = " ".join(command_key.split())
            command_key = aliases.get(command_key, command_key)

            await event.reply(tg_code(f"Rerun #{index}:\n{command}"))
        except Exception:
            await event.reply(tg_code(f"Usage: $cmd rerun N\n\n{history_preview()}"))
            return

    if command_key == "out log" or command_key.startswith("out log "):
        arg = command[7:].strip().lower()

        if arg == "on":
            log_enabled = True
            await event.reply(tg_code("Logging enabled. Outputs will be saved in logs/."))
        elif arg == "off":
            log_enabled = False
            await event.reply(tg_code("Logging disabled."))
        elif arg == "status" or not arg:
            status = "on" if log_enabled else "off"
            await event.reply(tg_code(f"Logging: {status}"))
        else:
            await event.reply(tg_code("Usage: $out log on | $out log off | $out log status"))

        return

    if command_key in ("tt restart", "tt restart shell"):
        restart_shell()
        output_buffer = ""
        command_output_buffer = ""
        current_msg = None
        current_event = None
        await event.reply(tg_code("Shell restarted."))
        return

    if await handle_editor_command(event, command):
        return

    if command.startswith("ttget "):
        await send_file(event, command)
        return

    if command.startswith("ttput "):
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

    if command.startswith("ttpaste "):
        try:
            pasted = command[8:]
            shell.send(pasted)
            await event.reply(tg_code(f"Pasted {len(pasted)} chars"))
        except Exception as e:
            await event.reply(tg_code(f"Paste Error:\n{e}"))
        return

    if command.startswith("ttinput "):

        user_input = command[8:]

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

    last_command = command
    command_history.append(command)
    command_history[:] = command_history[-200:]

    command_output_buffer = ""
    output_revision += 1
    current_event = event
    current_log_path = create_log_path(command) if log_enabled else None

    if current_output_mode == "ss":
        current_msg = await event.reply(tg_code(f"Capturing:\n{command}"))
    else:
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
