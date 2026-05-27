import asyncio
import os
import socket
import time
import re
import shlex
import struct
import tempfile
import zlib
from datetime import datetime
from pathlib import Path

import pexpect
import pyte
from PIL import Image, ImageDraw, ImageFont

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError

api_id = int(os.environ.get("TG_API_ID", "123456"))
api_hash = os.environ.get("TG_API_HASH", "your_api_hash")

client = TelegramClient(
    "telegram_shell",
    api_id,
    api_hash
)

VERSION = "1.3.0"
BASE_DIR = Path(__file__).resolve().parent
EDIT_INTERVAL = 3
MAX_MESSAGE_OUTPUT = 3900
MAX_BUFFER_SIZE = 200000
TERM_COLUMNS = 160
TERM_LINES = 44
TERM_SCROLLBACK = 400
SHOT_RENDER_ROWS = 76
SHOT_LIVE_SECONDS = 5
SHOT_LIVE_MAX_SECONDS = 10
SHOT_LIVE_INTERVAL = 0.2
SHOT_LIVE_MAX_BYTES = 8 * 1024 * 1024

DONE_MARKER = "__TCM_DONE_982741__"
MARKER_HOLD_SIZE = len(DONE_MARKER) - 1


def spawn_shell():
    child = pexpect.spawn(
        "bash",
        ["--noprofile", "--norc", "--noediting"],
        encoding="utf-8",
        echo=False,
        dimensions=(TERM_LINES, TERM_COLUMNS),
        env={
            **os.environ,
            "TERM": "xterm-256color",
            "TERM_PROGRAM": "telegram-terminal",
            "TERM_PROGRAM_VERSION": VERSION,
            "COLORTERM": "truecolor",
            "PS1": "",
            "PS2": "",
            "PROMPT_COMMAND": "",
        }
    )
    child.delaybeforesend = 0
    return child


shell = spawn_shell()
terminal_screen = pyte.HistoryScreen(TERM_COLUMNS, TERM_LINES, history=TERM_SCROLLBACK)
terminal_stream = pyte.Stream(terminal_screen)

current_msg = None
current_event = None

output_buffer = ""
command_output_buffer = ""
command_file_output_buffer = ""
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
current_shot_wide = False
current_shot_command = None
current_command_started_at = None
current_command_last_activity = None
pending_shell_data = ""
shot_theme = "black"
shot_title = "telegram-terminal"
shell_cwd = Path.cwd()
terminal_waiting_prompt = False
terminal_external_prompt = False
started_at = time.time()
truetype_available = True
truetype_warning_shown = False

SHELL_WATCHDOG_IDLE_TIMEOUT = 1800
SHELL_WATCHDOG_POLL_INTERVAL = 10

SHOT_THEMES = {
    "black": {
        "bg": (0, 0, 0),
        "bar": (24, 30, 39),
        "line": (42, 52, 65),
        "title": (226, 232, 240),
        "text": (235, 235, 235),
        "cursor": (235, 235, 235),
        "cursor_text": (0, 0, 0),
    },
    "green": {
        "bg": (0, 0, 0),
        "bar": (0, 0, 0),
        "line": (28, 48, 34),
        "title": (226, 232, 240),
        "text": (220, 255, 226),
        "cursor": (220, 255, 226),
        "cursor_text": (0, 0, 0),
    },
    "white": {
        "bg": (0, 0, 0),
        "bar": (0, 0, 0),
        "line": (42, 42, 42),
        "title": (238, 242, 247),
        "text": (235, 239, 245),
        "cursor": (235, 239, 245),
        "cursor_text": (0, 0, 0),
    },
    "amber": {
        "bg": (0, 0, 0),
        "bar": (0, 0, 0),
        "line": (82, 58, 22),
        "title": (255, 236, 179),
        "text": (255, 213, 128),
        "cursor": (255, 213, 128),
        "cursor_text": (0, 0, 0),
    },
}

ansi_escape = re.compile(
    r'\x1B(?:\][^\x07]*(?:\x07|\x1B\\)|\[[0-?]*[ -/]*[@-~]|[@-Z\\-_])'
)


def clean_output(text):
    return ansi_escape.sub('', text)


def render_terminal_text(text):
    lines = [[]]
    col = 0

    for char in text:
        if char == "\r":
            lines[-1] = []
            col = 0
        elif char == "\n":
            lines.append([])
            col = 0
        elif char == "\b":
            col = max(0, col - 1)
        elif char == "\t":
            spaces = 8 - (col % 8)

            for _ in range(spaces):
                if col == len(lines[-1]):
                    lines[-1].append(" ")
                else:
                    lines[-1][col] = " "

                col += 1
        elif char >= " ":
            line = lines[-1]

            if col > len(line):
                line.extend(" " for _ in range(col - len(line)))

            if col == len(line):
                line.append(char)
            else:
                line[col] = char

            col += 1

    return "\n".join("".join(line).rstrip() for line in lines).rstrip()


def command_message_preview():
    return command_output_buffer[-MAX_MESSAGE_OUTPUT:] if command_output_buffer else ""

def tg_code(text):
    safe = str(text).replace("```", "`\u200b``")
    return f"```{safe}```"


def build_help():
    return """telegram-terminal help

Shell
  $<command>                 run command in persistent bash
  $ttinput <text>            send one input line
  $ttpaste <text>            paste raw text without Enter

Terminal Keys
  $ctrlc / $ctrl c           send Ctrl+C
  $ctrlb / $ctrl b           send Ctrl+B, useful for tmux prefix
  $ctrla / $ctrl a           send Ctrl+A
  $ctrld                     send Ctrl+D
  $ctrlz                     send Ctrl+Z
  $enter                     send Enter
  $tab                       send Tab
  $up / $down / $left / $right
  $key esc|backspace|delete|home|end|pgup|pgdn|space
  $key f1..f12               send function keys

Screenshots
  $shot                      screenshot current terminal screen
  $shot 80                   screenshot last 80 text-buffer lines
  $shot wide                 wider terminal screenshot
  $shot clear                screenshot, then clear screen/buffer
  $shot live N               readable animated screenshot, 1-10 seconds
  $shot live wide N          wider animated screenshot, 1-10 seconds
  $shot run <cmd>            run command and send screenshot
  $shot run wide <cmd>       run command with wider screenshot
  $shot run clear <cmd>      run, screenshot, then clear
  $shot run --no-session <cmd>
  $shot theme [black|green|white|amber]
  $shot title <text>
  $tt size [COLSxROWS]       show or resize the pty/screenshot terminal

Buffers
  $buf tail [lines|full]     show session output buffer
  $buf send [file.txt]       send session buffer as .txt
  $buf save <file.txt>       save session buffer on server
  $tt save-session [file]    save session buffer on server
  $buf clear                 clear session buffer and shot screen
  $buf status                show buffer status

Files
  $ttget <file>              send file from server
  $ttput <path>              upload attached document to path

Editor
  $ttedit open <file>        open file
  $ttedit show               show editor buffer
  $ttedit set N <text>       replace line N
  $ttedit insert N <text>    insert before line N
  $ttedit append <text>      append line
  $ttedit delete N[-M]       delete line/range
  $ttedit undo               undo last edit
  $ttedit find <text>        find text
  $ttedit replace old new    replace first match
  $ttedit replace-all old new
  $ttedit save               save file
  $ttedit cancel             close editor

History / Logs
  $cmd history [N]           show command history
  $cmd last                  show last command
  $cmd rerun N               rerun command by history number
  $out log on|off|status     save command outputs to logs/

Bot
  $tt status                 shell/editor status
  $tt restart                restart persistent bash
  $tt reset                  clear bot runtime state
  $tt version                show version
  $tt ping                   check latency
  $tt uptime                 show bot and system uptime
  $tt uptime bot             show bot uptime
  $tt uptime system          show system uptime
  $tt about                  show summary"""


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


def reset_runtime_state():
    global current_msg
    global current_event
    global command_output_buffer
    global command_file_output_buffer
    global output_revision
    global current_log_path
    global current_output_mode
    global current_output_no_session
    global current_shot_clear_after
    global current_shot_save_path
    global current_shot_wide
    global current_shot_command
    global current_command_started_at
    global current_command_last_activity
    global pending_shell_data
    global terminal_waiting_prompt
    global terminal_external_prompt

    current_msg = None
    current_event = None
    command_output_buffer = ""
    command_file_output_buffer = ""
    current_log_path = None
    current_output_mode = "chat"
    current_output_no_session = False
    current_shot_clear_after = False
    current_shot_save_path = None
    current_shot_wide = False
    current_shot_command = None
    current_command_started_at = None
    current_command_last_activity = None
    pending_shell_data = ""
    terminal_waiting_prompt = False
    terminal_external_prompt = False
    output_revision += 1


def reply_target_id(event):
    message = getattr(event, "message", event)
    return getattr(message, "id", None)


async def reply_file(event, file_path, message=None, force_document=False):
    chat_id = getattr(event, "chat_id", None)

    if chat_id is None and hasattr(event, "get_chat"):
        chat = await event.get_chat()
        chat_id = getattr(chat, "id", chat)

    await event.client.send_file(
        chat_id,
        str(file_path),
        caption=message,
        reply_to=reply_target_id(event),
        force_document=force_document,
    )


async def send_text_file(event, content, filename="telegram-terminal-output.txt", message="Output attached as text file."):
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)

    final_path = tmp_path.with_name(filename)
    tmp_path.replace(final_path)

    try:
        await reply_file(event, final_path, message)
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

TERMINAL_PALETTE = {
    "black": (0, 0, 0),
    "red": (205, 49, 49),
    "green": (13, 188, 121),
    "brown": (229, 229, 16),
    "yellow": (229, 229, 16),
    "blue": (36, 114, 200),
    "magenta": (188, 63, 188),
    "cyan": (17, 168, 205),
    "white": (229, 229, 229),
    "brightblack": (102, 102, 102),
    "brightred": (241, 76, 76),
    "brightgreen": (35, 209, 139),
    "brightyellow": (245, 245, 67),
    "brightblue": (59, 142, 234),
    "brightmagenta": (214, 112, 214),
    "brightcyan": (41, 184, 219),
    "brightwhite": (255, 255, 255),
}

FONT_PATHS = [
    BASE_DIR / "assets/fonts/DejaVuSansMono.ttf",
    "assets/fonts/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansMono-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationMono-Regular.ttf",
]

TERMUX_PILLOW_FREETYPE_HELP = (
    "TrueType fonts unavailable; screenshots will use Pillow's default font.\n"
    "If you are running this in Termux, install FreeType support and reinstall Pillow:\n"
    "  pkg install freetype libjpeg-turbo zlib python\n"
    "  pip uninstall -y pillow\n"
    "  pip install --no-cache-dir --force-reinstall pillow\n"
    "If pip still builds Pillow without _imagingft, use Termux's package instead:\n"
    "  pip uninstall -y pillow\n"
    "  pkg install python-pillow"
)


def xterm_color(index):
    index = max(0, min(255, int(index)))

    if index < 16:
        palette = [
            (0, 0, 0), (205, 49, 49), (13, 188, 121), (229, 229, 16),
            (36, 114, 200), (188, 63, 188), (17, 168, 205), (229, 229, 229),
            (102, 102, 102), (241, 76, 76), (35, 209, 139), (245, 245, 67),
            (59, 142, 234), (214, 112, 214), (41, 184, 219), (255, 255, 255),
        ]
        return palette[index]

    if 16 <= index <= 231:
        index -= 16
        r = index // 36
        g = (index % 36) // 6
        b = index % 6
        steps = [0, 95, 135, 175, 215, 255]
        return (steps[r], steps[g], steps[b])

    shade = 8 + (index - 232) * 10
    return (shade, shade, shade)


def resolve_terminal_color(value, default_color):
    if value is None:
        return default_color

    if isinstance(value, int):
        return xterm_color(value)

    name = str(value).lower().strip().replace("-", "")

    if name in {"default", ""}:
        return default_color

    if name.startswith("#"):
        name = name[1:]

    if len(name) == 6 and all(char in "0123456789abcdef" for char in name):
        return tuple(int(name[i:i + 2], 16) for i in (0, 2, 4))

    if name.startswith("ansi"):
        name = name[4:]

    return TERMINAL_PALETTE.get(name) or default_color


def brighten(color):
    return tuple(min(255, int(channel * 1.25) + 18) for channel in color)


def load_terminal_font(size):
    global truetype_available
    global truetype_warning_shown

    if truetype_available:
        for font_path in FONT_PATHS:
            if not Path(font_path).is_file():
                continue

            try:
                return ImageFont.truetype(str(font_path), size=size)
            except ImportError as e:
                truetype_available = False

                if not truetype_warning_shown:
                    print(f"{TERMUX_PILLOW_FREETYPE_HELP}\nOriginal error: {e}")
                    truetype_warning_shown = True

                break
            except Exception as e:
                if not truetype_warning_shown:
                    print(f"Font load failed ({font_path}); using Pillow default font: {e}")
                    truetype_warning_shown = True

    return ImageFont.load_default()


def font_bbox(font, text):
    if hasattr(font, "getbbox"):
        return font.getbbox(text)

    if hasattr(font, "getsize"):
        width, height = font.getsize(text)
        return (0, 0, width, height)

    return (0, 0, 10, 18)


def terminal_cell(screen, row, col):
    line = screen.buffer.get(row, {})
    return line.get(col)


def screen_rows(screen):
    rows = []
    history = getattr(screen, "history", None)

    if history is not None:
        rows.extend(list(history.top))

    rows.extend(screen.buffer.get(row, {}) for row in range(screen.lines))
    return rows


def row_has_text(row):
    return any(getattr(cell, "data", " ") != " " for cell in row.values())


def terminal_has_text(screen=None):
    screen = screen or terminal_screen
    return any(row_has_text(row) for row in screen_rows(screen))


def terminal_snapshot_lines():
    lines = []

    for row in screen_rows(terminal_screen):
        chars = []
        for col in range(terminal_screen.columns):
            cell = row.get(col)
            chars.append(getattr(cell, "data", " ") if cell else " ")
        lines.append("".join(chars).rstrip())

    return lines


def feed_terminal_screen(data):
    if not data:
        return

    try:
        terminal_stream.feed(data)
    except Exception as e:
        print(f"Terminal emulator feed error: {e}")


def reset_terminal_screen():
    global terminal_screen
    global terminal_stream
    global terminal_waiting_prompt
    global terminal_external_prompt

    terminal_screen = pyte.HistoryScreen(TERM_COLUMNS, TERM_LINES, history=TERM_SCROLLBACK)
    terminal_stream = pyte.Stream(terminal_screen)
    terminal_waiting_prompt = False
    terminal_external_prompt = False


def resize_terminal(cols, lines):
    global TERM_COLUMNS
    global TERM_LINES
    global terminal_screen
    global terminal_stream

    cols = max(40, min(240, int(cols)))
    lines = max(12, min(80, int(lines)))
    TERM_COLUMNS = cols
    TERM_LINES = lines

    try:
        shell.setwinsize(lines, cols)
    except Exception:
        pass

    try:
        terminal_screen.resize(lines, cols)
    except Exception:
        terminal_screen = pyte.HistoryScreen(cols, lines, history=TERM_SCROLLBACK)
        terminal_stream = pyte.Stream(terminal_screen)

    return cols, lines


def parse_terminal_size(value):
    match = re.fullmatch(r"\s*(\d{2,3})\s*[x, ]\s*(\d{2,3})\s*", value)

    if not match:
        raise ValueError("usage: $tt size COLSxROWS, example: $tt size 120x36")

    return int(match.group(1)), int(match.group(2))


def short_cwd(path):
    home = Path.home()

    if path == home:
        return "~"

    try:
        return "~/" + str(path.relative_to(home))
    except ValueError:
        return str(path)


def update_shell_cwd(command):
    global shell_cwd

    try:
        parts = shlex.split(command)
    except ValueError:
        return

    if not parts or parts[0] != "cd":
        return

    target = Path.home() if len(parts) == 1 else Path(parts[1]).expanduser()

    if not target.is_absolute():
        target = shell_cwd / target

    shell_cwd = target.resolve(strict=False)


def feed_terminal_prompt(command="", newline=True, replace_current=False):
    user = os.environ.get("USER") or "user"
    host = socket.gethostname().split(".")[0]
    cwd = short_cwd(shell_cwd)

    if replace_current and terminal_has_text():
        prefix = "\r\x1b[2K"
    else:
        prefix = "\r\n" if terminal_has_text() and getattr(terminal_screen.cursor, "x", 0) else ""

    suffix = "\r\n" if newline else ""
    command_text = f" {command}" if command else ""
    prompt = f"{prefix}\x1b[1;32m{user}@{host}\x1b[0m:\x1b[1;34m{cwd}\x1b[0m${command_text}{suffix}"
    feed_terminal_screen(prompt)


def fallback_text_to_screen(content):
    screen = pyte.HistoryScreen(TERM_COLUMNS, TERM_LINES, history=TERM_SCROLLBACK)
    stream = pyte.Stream(screen)
    stream.feed(clean_output(content).replace("\r", ""))
    return screen



def render_terminal_image(content, wide=False, use_terminal=True, command_line=None):
    theme = SHOT_THEMES.get(shot_theme, SHOT_THEMES["black"])
    screen = terminal_screen

    if not use_terminal or not terminal_has_text(screen):
        screen = fallback_text_to_screen(content or "Output buffer is empty.")

    cols = screen.columns
    all_rows = screen_rows(screen)
    max_rows = SHOT_RENDER_ROWS if wide else min(SHOT_RENDER_ROWS, 64)
    start_row = max(0, len(all_rows) - max_rows)
    rendered_rows = all_rows[start_row:]
    command_line = (command_line or "").strip()
    rendered_lines = [
        "".join(getattr(row.get(col), "data", " ") if row.get(col) else " " for col in range(cols)).rstrip()
        for row in rendered_rows
    ]
    command_is_visible = any(line.endswith(f"$ {command_line}") for line in rendered_lines)
    show_command_header = bool(command_line and not command_is_visible)
    rows = (len(rendered_rows) or screen.lines) + (1 if show_command_header else 0)
    font_size = 16 if wide else 17
    font = load_terminal_font(font_size)
    title_font = load_terminal_font(16)
    bbox = font_bbox(font, "M")
    cell_width = max(9, bbox[2] - bbox[0] + 1)
    cell_height = max(18, bbox[3] - bbox[1] + 7)
    pad_x = 22 if wide else 26
    pad_top = 64
    pad_bottom = 22
    title_height = 46
    width = pad_x * 2 + cols * cell_width
    height = pad_top + rows * cell_height + pad_bottom

    image = Image.new("RGB", (width, height), theme["bg"])
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width, title_height), fill=theme["bar"])
    draw.rectangle((0, title_height, width, title_height + 1), fill=theme["line"])
    draw.ellipse((18, 15, 31, 28), fill=(255, 95, 87))
    draw.ellipse((42, 15, 55, 28), fill=(255, 189, 46))
    draw.ellipse((66, 15, 79, 28), fill=(40, 200, 64))
    draw.text((100, 13), shot_title[:100], fill=theme["title"], font=title_font)

    history_len = len(list(getattr(getattr(screen, "history", None), "top", [])))
    cursor_row = history_len + getattr(screen.cursor, "y", -1)
    cursor_col = getattr(screen.cursor, "x", -1)
    visible_cursor_row = cursor_row - start_row + (1 if show_command_header else 0)
    cursor_visible = 0 <= visible_cursor_row < rows and 0 <= cursor_col < cols

    row_offset = 0

    if show_command_header:
        user = os.environ.get("USER") or "user"
        host = socket.gethostname().split(".")[0]
        prompt_text = f"{user}@{host}:{short_cwd(shell_cwd)}$ "
        draw.text((pad_x, pad_top), prompt_text[:cols], fill=resolve_terminal_color("brightgreen", theme["text"]), font=font)
        prompt_width = min(len(prompt_text), cols) * cell_width
        draw.text((pad_x + prompt_width, pad_top), command_line[:max(0, cols - len(prompt_text))], fill=theme["text"], font=font)
        row_offset = 1

    for row_index, row_data in enumerate(rendered_rows):
        y = pad_top + (row_index + row_offset) * cell_height
        for col in range(cols):
            cell = row_data.get(col)
            char = getattr(cell, "data", " ") if cell else " "

            if not char or char == "\x00":
                char = " "

            fg = resolve_terminal_color(getattr(cell, "fg", None), theme["text"])
            bg = resolve_terminal_color(getattr(cell, "bg", None), theme["bg"])
            is_cursor = cursor_visible and (row_index + row_offset) == visible_cursor_row and col == cursor_col

            if cell and getattr(cell, "reverse", False):
                fg, bg = bg, fg

            if cell and getattr(cell, "bold", False):
                fg = brighten(fg)

            if cell and getattr(cell, "dim", False):
                fg = tuple(max(0, int(channel * 0.55)) for channel in fg)

            if is_cursor:
                bg = theme.get("cursor", theme["text"])
                fg = theme.get("cursor_text", theme["bg"])

            x = pad_x + col * cell_width

            if bg != theme["bg"] or is_cursor:
                draw.rectangle((x, y, x + cell_width, y + cell_height), fill=bg)

            if char != " ":
                draw.text((x, y), char, fill=fg, font=font)

            if cell and getattr(cell, "underscore", False):
                draw.line((x, y + cell_height - 3, x + cell_width, y + cell_height - 3), fill=fg)

            if cell and getattr(cell, "strikethrough", False):
                draw.line((x, y + cell_height // 2, x + cell_width, y + cell_height // 2), fill=fg)

    return image


async def send_terminal_screenshot(event, content, wide=False, save_path=None, use_terminal=True, command_line=None):
    try:
        image = render_terminal_image(content, wide=wide, use_terminal=use_terminal, command_line=command_line)

        if save_path:
            image_path = Path(save_path).expanduser()
            image_path.parent.mkdir(parents=True, exist_ok=True)
            image.save(image_path, "PNG")
            await reply_file(event, image_path, f"Terminal screenshot saved: {image_path}")
            return

        with tempfile.TemporaryDirectory() as tmp_dir:
            image_path = Path(tmp_dir) / "telegram-terminal.png"
            image.save(image_path, "PNG")
            await reply_file(event, image_path, "Terminal screenshot:")

    except Exception as e:
        await event.reply(tg_code(f"Screenshot Error:\n{type(e).__name__}: {e}"))


def gif_terminal_frame(image):
    palette_container = getattr(Image, "Palette", None)
    palette_mode = getattr(palette_container, "ADAPTIVE", None) if palette_container else None
    dither_container = getattr(Image, "Dither", None)
    dither_mode = getattr(dither_container, "NONE", None) if dither_container else None

    if palette_mode is None:
        palette_mode = Image.ADAPTIVE

    if dither_mode is None:
        dither_mode = Image.NONE

    frame = image.convert("P", palette=palette_mode, colors=256, dither=dither_mode)
    frame.info.pop("transparency", None)
    return frame


def save_terminal_live_gif(path, frames, seconds):
    step = 1

    while True:
        selected = frames[::step]

        if selected[-1] is not frames[-1]:
            selected.append(frames[-1])

        frame_duration = max(20, int(seconds * 1000 / len(selected)))
        gif_frames = [frame if frame.mode == "P" else gif_terminal_frame(frame) for frame in selected]
        gif_frames[0].save(
            path,
            "GIF",
            save_all=True,
            append_images=gif_frames[1:],
            duration=frame_duration,
            loop=0,
            optimize=False,
            disposal=1,
            transparency=None,
        )

        if path.stat().st_size <= SHOT_LIVE_MAX_BYTES or len(selected) <= 2:
            return len(selected), frame_duration

        step *= 2


async def send_terminal_live_shot(event, content, seconds=SHOT_LIVE_SECONDS, wide=False, use_terminal=True, command_line=None):
    try:
        seconds = max(1, min(SHOT_LIVE_MAX_SECONDS, int(seconds)))
        frame_count = max(2, int(seconds / SHOT_LIVE_INTERVAL) + 1)
        frames = []

        for frame_index in range(frame_count):
            frame = render_terminal_image(content, wide=wide, use_terminal=use_terminal, command_line=command_line)
            frames.append(gif_terminal_frame(frame))

            if frame_index < frame_count - 1:
                await asyncio.sleep(SHOT_LIVE_INTERVAL)

        with tempfile.TemporaryDirectory() as tmp_dir:
            image_path = Path(tmp_dir) / "telegram-terminal-live.gif"
            frame_count, frame_duration = save_terminal_live_gif(image_path, frames, seconds)
            caption = f"Terminal live shot ({seconds}s, {frame_count} frames):"
            await reply_file(event, image_path, caption, force_document=True)

    except Exception as e:
        await event.reply(tg_code(f"Live Shot Error:\n{type(e).__name__}: {e}"))


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

    await reply_file(event, path, f"File: {path}")
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
    global terminal_screen
    global terminal_stream
    global terminal_waiting_prompt
    global terminal_external_prompt
    global pending_shell_data

    try:
        if shell.isalive():
            shell.terminate(force=True)
    except Exception:
        pass

    shell = spawn_shell()
    terminal_screen = pyte.HistoryScreen(TERM_COLUMNS, TERM_LINES, history=TERM_SCROLLBACK)
    terminal_stream = pyte.Stream(terminal_screen)
    terminal_waiting_prompt = False
    terminal_external_prompt = False
    pending_shell_data = ""


async def shell_watchdog():
    global current_command_started_at
    global current_command_last_activity

    while True:
        await asyncio.sleep(SHELL_WATCHDOG_POLL_INTERVAL)

        try:
            if not shell.isalive():
                print("Watchdog: shell dead; restarting")
                restart_shell()
                reset_runtime_state()
                continue

            if not current_command_started_at or not current_command_last_activity:
                continue

            if not command_output_buffer:
                continue

            idle_for = time.time() - current_command_last_activity

            if idle_for < SHELL_WATCHDOG_IDLE_TIMEOUT:
                continue

            print(f"Watchdog: shell idle for {int(idle_for)}s; restarting")

            if current_event:
                try:
                    await current_event.reply(
                        tg_code(
                            "Shell watchdog restarted the session after inactivity."
                        )
                    )
                except Exception:
                    pass

            restart_shell()
            reset_runtime_state()

        except Exception as e:
            print(f"Watchdog Error: {e}")


def command_program_names(command):
    names = []

    for segment in command.split("|"):
        try:
            parts = shlex.split(segment)
        except ValueError:
            parts = segment.split()

        if parts:
            names.append(Path(parts[0]).name)

    return names


def is_interactive_shell_command(command):
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()

    if not parts:
        return False

    name = Path(parts[0]).name
    full_screen_commands = {
        "tmux", "screen", "vim", "vi", "nvim", "nano", "micro", "emacs",
        "less", "more", "man", "top", "htop", "btop", "watch", "ssh",
        "su", "login", "ftp", "sftp", "mysql", "psql", "sqlite3", "python",
        "python3", "node", "irb", "php", "lua", "radian", "R", "cmatrix",
        "asciiquarium", "cava", "hollywood",
    }
    non_interactive_flags = {"-c", "--command", "--version", "-V", "--help", "-h"}

    if "|" in command and any(program in full_screen_commands for program in command_program_names(command)):
        return True

    if name == "sudo":
        if any(arg in {"-i", "-s", "su"} for arg in parts[1:]):
            return True

        idx = 1
        options_with_values = {"-u", "--user", "-g", "--group", "-p", "--prompt", "-C", "--close-from", "-h", "--host"}

        while idx < len(parts):
            arg = parts[idx]

            if arg == "--":
                idx += 1
                break

            if arg in options_with_values:
                idx += 2
                continue

            if arg.startswith("-"):
                idx += 1
                continue

            break

        return idx < len(parts) and is_interactive_shell_command(" ".join(shlex.quote(arg) for arg in parts[idx:]))

    if name == "tmux":
        detached = {"-d", "detach", "detach-client", "ls", "list-sessions", "kill-session", "kill-server"}
        return not any(arg in detached for arg in parts[1:])

    if name in {"python", "python3", "node", "php", "lua", "R"}:
        return not any(arg in non_interactive_flags for arg in parts[1:])

    return name in full_screen_commands


def is_shell_exit_command(command):
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()

    return bool(parts) and parts[0] in {"exit", "logout"}


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



def format_duration(seconds):
    seconds = int(seconds)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    parts = []

    if days:
        parts.append(f"{days}d")

    if hours or parts:
        parts.append(f"{hours}h")

    if minutes or parts:
        parts.append(f"{minutes}m")

    parts.append(f"{seconds}s")
    return " ".join(parts)


def system_uptime():
    try:
        uptime_seconds = float(Path("/proc/uptime").read_text().split()[0])
    except Exception:
        return "unavailable"

    return format_duration(uptime_seconds)


def uptime_text(mode=""):
    bot = format_duration(time.time() - started_at)

    if mode == "bot":
        return f"Bot uptime: {bot}"

    if mode in {"system", "sys", "vps"}:
        return f"System uptime: {system_uptime()}"

    return f"Bot uptime: {bot}\nSystem uptime: {system_uptime()}"


def about_text():
    status = "alive" if shell.isalive() else "dead"
    logging = "on" if log_enabled else "off"
    editor = "none"

    if editor_state:
        dirty = "modified" if editor_state["dirty"] else "saved"
        editor = f"{editor_state['path']} ({dirty})"

    return (
        f"telegram-terminal {VERSION}\n"
        f"Uptime: {format_duration(time.time() - started_at)}\n"
        f"Shell: {status}\n"
        f"Session buffer: {len(output_buffer)} chars\n"
        f"Last command: {last_command or 'none'}\n"
        f"Logging: {logging}\n"
        f"Editor: {editor}"
    )


async def stream_shell_output():

    global current_msg
    global current_event
    global output_buffer
    global command_output_buffer
    global command_file_output_buffer
    global output_revision
    global current_output_mode
    global current_output_no_session
    global current_shot_clear_after
    global current_shot_save_path
    global current_shot_wide
    global current_shot_command
    global current_command_started_at
    global current_command_last_activity
    global pending_shell_data
    global terminal_waiting_prompt
    global terminal_external_prompt

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

                    command_finished = False

                    if current_command_started_at:
                        pending_shell_data += data

                        if DONE_MARKER in pending_shell_data:
                            raw_data = pending_shell_data.replace(DONE_MARKER, "", 1)
                            pending_shell_data = ""
                            command_finished = True
                        elif len(pending_shell_data) > MARKER_HOLD_SIZE:
                            raw_data = pending_shell_data[:-MARKER_HOLD_SIZE]
                            pending_shell_data = pending_shell_data[-MARKER_HOLD_SIZE:]
                        else:
                            continue
                    else:
                        raw_data = data
                        pending_shell_data = ""

                    feed_terminal_screen(raw_data)
                    cleaned = clean_output(raw_data)

                    if not current_output_no_session:
                        output_buffer += cleaned

                    command_output_buffer += cleaned
                    command_file_output_buffer += cleaned

                    output_buffer = output_buffer[-MAX_BUFFER_SIZE:]
                    command_output_buffer = command_output_buffer[-MAX_BUFFER_SIZE:]

                    now = time.time()
                    current_command_last_activity = now

                    trimmed = command_message_preview()

                    if command_finished:
                        if not terminal_external_prompt:
                            feed_terminal_prompt(newline=False)
                            terminal_waiting_prompt = True
                        else:
                            terminal_waiting_prompt = False

                        if current_msg:

                            try:

                                if current_log_path:
                                    write_command_log(last_command or "", command_file_output_buffer, current_log_path)

                                if current_output_mode == "ss":
                                    target_event = current_event or current_msg
                                    await send_terminal_screenshot(
                                        target_event,
                                        command_output_buffer,
                                        wide=current_shot_wide,
                                        save_path=current_shot_save_path,
                                        command_line=current_shot_command,
                                    )

                                    try:
                                        await current_msg.delete()
                                    except Exception:
                                        pass

                                    if current_shot_clear_after:
                                        output_buffer = ""
                                        reset_terminal_screen()
                                        output_revision += 1

                                    current_output_mode = "chat"
                                    current_output_no_session = False
                                    current_shot_clear_after = False
                                    current_shot_save_path = None
                                    current_shot_wide = False
                                    current_shot_command = None
                                    current_msg = None
                                    current_event = None
                                    command_output_buffer = ""
                                    command_file_output_buffer = ""
                                    current_command_started_at = None
                                    current_command_last_activity = None
                                    last_text = trimmed
                                    last_edit = now
                                    continue

                                if len(command_file_output_buffer) > MAX_MESSAGE_OUTPUT:
                                    suffix = "\n\nOutput is large. Sending full output as .txt..."

                                    if current_log_path:
                                        suffix += f"\nLog saved: {current_log_path}"

                                    try:
                                        await current_msg.edit(
                                            tg_code(trimmed + suffix)
                                        )
                                    except Exception as e:
                                        print(f"Final large-output edit error: {e}")

                                    target_event = current_event or current_msg
                                    await send_text_file(
                                        target_event,
                                        command_file_output_buffer,
                                        "telegram-terminal-output.txt",
                                        "Full output:"
                                    )
                                else:
                                    suffix = f"\n\nLog saved: {current_log_path}" if current_log_path else ""
                                    await current_msg.edit(
                                        tg_code(trimmed + suffix)
                                    )

                                current_msg = None
                                current_event = None
                                command_output_buffer = ""
                                command_file_output_buffer = ""
                                current_output_mode = "chat"
                                current_output_no_session = False
                                current_shot_clear_after = False
                                current_shot_save_path = None
                                current_shot_command = None
                                current_command_started_at = None
                                current_command_last_activity = None
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
            reset_runtime_state()
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
    global command_file_output_buffer
    global output_revision
    global last_command
    global log_enabled
    global current_log_path
    global current_output_mode
    global current_output_no_session
    global current_shot_clear_after
    global current_shot_save_path
    global current_shot_wide
    global current_shot_command
    global current_command_started_at
    global current_command_last_activity
    global pending_shell_data
    global terminal_waiting_prompt
    global terminal_external_prompt
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
        "ctrl b": "ctrlb",
        "control b": "ctrlb",
        "ctrl a": "ctrla",
        "control a": "ctrla",
        "ctrl l": "ctrll",
        "control l": "ctrll",
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

    if command_key == "tt uptime" or command_key.startswith("tt uptime "):
        args = command.split(maxsplit=2)
        mode = args[2].lower() if len(args) > 2 else ""

        if mode and mode not in {"bot", "system", "sys", "vps"}:
            await event.reply(tg_code("Usage: $tt uptime [bot|system]"))
            return

        await event.reply(tg_code(uptime_text(mode)))
        return

    if command_key == "tt about":
        await event.reply(tg_code(about_text()))
        return

    if command_key == "tt size" or command_key.startswith("tt size "):
        if command_key == "tt size":
            await event.reply(tg_code(f"Terminal size: {TERM_COLUMNS}x{TERM_LINES}"))
            return

        try:
            cols, lines = parse_terminal_size(command.split(maxsplit=2)[2])
            cols, lines = resize_terminal(cols, lines)
            reset_terminal_screen()
            output_revision += 1
            await event.reply(tg_code(f"Terminal resized: {cols}x{lines}"))
        except Exception as e:
            await event.reply(tg_code(str(e)))

        return

    if command_key in {"tt theme", "shot theme"} or command_key.startswith("tt theme ") or command_key.startswith("shot theme "):
        args = command.split(maxsplit=2)

        if len(args) < 3:
            names = ", ".join(sorted(SHOT_THEMES))
            await event.reply(tg_code(f"Current theme: {shot_theme}\nAvailable: {names}"))
            return

        selected = args[2].strip().lower()

        if selected not in SHOT_THEMES:
            names = ", ".join(sorted(SHOT_THEMES))
            await event.reply(tg_code(f"Unknown theme: {selected}\nAvailable: {names}"))
            return

        shot_theme = selected
        await event.reply(tg_code(f"Screenshot theme: {shot_theme}"))
        return

    if command_key in {"tt title", "shot title"} or command_key.startswith("tt title ") or command_key.startswith("shot title "):
        args = command.split(maxsplit=2)

        if len(args) < 3 or not args[2].strip():
            await event.reply(tg_code(f"Screenshot title: {shot_title}"))
            return

        shot_title = args[2].strip()[:100]
        await event.reply(tg_code(f"Screenshot title: {shot_title}"))
        return

    if command_key == "tt reset" or command_key == "tt cleanup":
        reset_runtime_state()

        if not shell.isalive():
            restart_shell()

        await event.reply(tg_code("Runtime state reset."))
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

    if command.startswith("shot run "):
        run_text = command[9:].strip()
        current_shot_clear_after = False
        current_output_no_session = False
        current_shot_save_path = None
        current_shot_wide = False
        current_shot_command = None

        while True:
            if run_text.startswith("clear "):
                current_shot_clear_after = True
                run_text = run_text[6:].strip()
                continue

            if run_text.startswith("wide "):
                current_shot_wide = True
                run_text = run_text[5:].strip()
                continue

            if run_text.startswith("--no-session "):
                current_output_no_session = True
                run_text = run_text[13:].strip()
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
        current_shot_command = command

    elif command_key == "shot run":
        await event.reply(tg_code("Usage: $shot run <command>"))
        return

    if command_key == "shot" or command_key.startswith("shot "):
        shot_args = command.split()
        wide = False
        clear_after = False
        live = False
        live_seconds = SHOT_LIVE_SECONDS
        tail_arg = ""
        idx = 1

        while idx < len(shot_args):
            arg = shot_args[idx]

            if arg == "wide":
                wide = True
            elif arg == "clear":
                clear_after = True
            elif arg == "live":
                live = True
            elif live and arg.isdigit():
                live_seconds = arg
            else:
                tail_arg = arg

            idx += 1

        use_terminal = not tail_arg

        if live:
            await send_terminal_live_shot(
                event,
                tail_output(tail_arg),
                seconds=live_seconds,
                wide=wide,
                use_terminal=use_terminal,
            )
        else:
            await send_terminal_screenshot(
                event,
                tail_output(tail_arg),
                wide=wide,
                save_path=None,
                use_terminal=use_terminal,
            )

        if clear_after:
            output_buffer = ""
            reset_terminal_screen()
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

    if command_key == "buf save" or command_key.startswith("buf save "):
        args = command.split(maxsplit=2)

        if len(args) < 3:
            await event.reply(tg_code("Usage: $buf save <file.txt>"))
            return

        if not output_buffer:
            await event.reply(tg_code("Output buffer is empty."))
            return

        save_path = Path(args[2]).expanduser()
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_text(output_buffer, encoding="utf-8", errors="replace")
        await event.reply(tg_code(f"Output buffer saved: {save_path}"))
        return

    if command_key == "tt save session" or command_key.startswith("tt save session "):
        args = command.split(maxsplit=2)
        filename = args[2].strip() if len(args) > 2 else "telegram-terminal-session.txt"

        if not filename.endswith(".txt"):
            filename += ".txt"

        if not output_buffer:
            await event.reply(tg_code("Output buffer is empty."))
            return

        save_path = Path(filename).expanduser()
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_text(output_buffer, encoding="utf-8", errors="replace")
        await event.reply(tg_code(f"Session saved: {save_path}"))
        return

    if command_key == "buf status":
        await event.reply(tg_code(buffer_status()))
        return

    if command_key == "buf clear":
        output_buffer = ""
        command_output_buffer = ""
        command_file_output_buffer = ""
        pending_shell_data = ""
        current_msg = None
        current_event = None
        current_output_mode = "chat"
        current_output_no_session = False
        current_shot_clear_after = False
        current_shot_save_path = None
        current_shot_wide = False
        current_shot_command = None
        current_command_started_at = None
        current_command_last_activity = None
        reset_terminal_screen()
        output_revision += 1
        await event.reply(tg_code("Session output buffer and current command state cleared."))
        return

    if command_key.startswith("buf "):
        await event.reply(tg_code("Usage: $buf status | $buf clear | $buf tail | $buf send | $buf save"))
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
        reset_runtime_state()
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
        "ctrlb": "\x02",
        "ctrla": "\x01",
        "ctrll": "\x0c",
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
        "space": " ",
        "f1": "\x1bOP",
        "f2": "\x1bOQ",
        "f3": "\x1bOR",
        "f4": "\x1bOS",
        "f5": "\x1b[15~",
        "f6": "\x1b[17~",
        "f7": "\x1b[18~",
        "f8": "\x1b[19~",
        "f9": "\x1b[20~",
        "f10": "\x1b[21~",
        "f11": "\x1b[23~",
        "f12": "\x1b[24~",
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
            interrupted_started_at = current_command_started_at
            shell.send(control_sequences[command_key])

            if command_key == "ctrlc" and interrupted_started_at:
                await asyncio.sleep(0.2)

                if shell.isalive():
                    shell.sendline(f"printf '\n{DONE_MARKER}\n'")

                await asyncio.sleep(1.0)

                if current_command_started_at == interrupted_started_at:
                    preview = command_message_preview()
                    suffix = "\n\nInterrupted."

                    if current_msg:
                        try:
                            await current_msg.edit(tg_code((preview + suffix)[-MAX_MESSAGE_OUTPUT:]))
                        except Exception as e:
                            print(f"Ctrl+C final edit error: {e}")

                    command_output_buffer = ""
                    command_file_output_buffer = ""
                    pending_shell_data = ""
                    current_msg = None
                    current_event = None
                    current_output_mode = "chat"
                    current_output_no_session = False
                    current_shot_clear_after = False
                    current_shot_save_path = None
                    current_shot_wide = False
                    current_shot_command = None
                    current_command_started_at = None
                    current_command_last_activity = None
                    terminal_waiting_prompt = False
                    feed_terminal_prompt(newline=False)
                    output_revision += 1

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

            if terminal_external_prompt:
                await event.reply(tg_code("Input sent"))
            else:
                await event.reply(
                    tg_code(f"Input Sent:\n{user_input}")
                )

        except Exception as e:

            await event.reply(
                tg_code(f"Input Error:\n{e}")
            )

        return

    if is_interactive_shell_command(command) or (terminal_external_prompt and is_shell_exit_command(command)):
        print(
            f"[{current_time}] "
            f"Interactive: {command}"
        )

        last_command = command
        command_history.append(command)
        command_history[:] = command_history[-200:]
        command_output_buffer = ""
        command_file_output_buffer = ""
        output_revision += 1
        current_event = event

        if not terminal_external_prompt:
            # Interactive commands such as su/ssh own the prompt after this point.
            # Start their virtual screen clean so old local prompts do not mix with
            # the real remote/user prompt.
            reset_terminal_screen()
        terminal_waiting_prompt = False

        current_command_started_at = None
        current_command_last_activity = None
        current_msg = None

        try:
            shell.sendline(command)
            terminal_external_prompt = not is_shell_exit_command(command)

            if current_output_mode == "ss":
                await asyncio.sleep(1)
                await send_terminal_screenshot(event, command_output_buffer, wide=current_shot_wide, save_path=current_shot_save_path, command_line=current_shot_command)

                if current_shot_clear_after:
                    output_buffer = ""
                    reset_terminal_screen()
                    output_revision += 1

                current_output_mode = "chat"
                current_output_no_session = False
                current_shot_clear_after = False
                current_shot_save_path = None
                current_shot_wide = False
                current_shot_command = None
            else:
                await event.reply(tg_code("Interactive command sent"))
        except Exception as e:
            await event.reply(tg_code(f"Interactive Error:\n{e}"))

        return

    print(
        f"[{current_time}] "
        f"You Executed: {command}"
    )

    last_command = command
    command_history.append(command)
    command_history[:] = command_history[-200:]

    command_output_buffer = ""
    command_file_output_buffer = ""
    output_revision += 1
    current_event = event

    if not terminal_external_prompt:
        feed_terminal_prompt(command, replace_current=terminal_waiting_prompt)
    terminal_waiting_prompt = False
    update_shell_cwd(command)
    current_command_started_at = time.time()
    current_command_last_activity = current_command_started_at
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

    print("telegram-terminal is running.")

    asyncio.create_task(
        stream_shell_output()
    )
    asyncio.create_task(
        shell_watchdog()
    )

    await client.start()

    await client.run_until_disconnected()

asyncio.run(main())
