import asyncio
import time
import re
import shlex
import tempfile
from datetime import datetime
from pathlib import Path

import pexpect
from PIL import Image, ImageDraw, ImageFont

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError

api_id = 123456
api_hash = "12344567abcdefghijklmnop"

client = TelegramClient(
    "telegram_shell",
    api_id,
    api_hash
)

VERSION = "1.1.0"
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
raw_output_buffer = ""
command_output_buffer = ""
raw_command_output_buffer = ""
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
current_command_started_at = None
current_command_last_activity = None
shot_theme = "green"
shot_title = "telegram-terminal"
started_at = time.time()

SHELL_WATCHDOG_IDLE_TIMEOUT = 1800
SHELL_WATCHDOG_POLL_INTERVAL = 10

SHOT_THEMES = {
    "green": {
        "bg": (0, 0, 0),
        "bar": (18, 18, 18),
        "line": (46, 46, 46),
        "title": (238, 238, 238),
        "text": (220, 255, 226),
    },
    "white": {
        "bg": (0, 0, 0),
        "bar": (18, 18, 18),
        "line": (46, 46, 46),
        "title": (238, 242, 247),
        "text": (235, 239, 245),
    },
    "amber": {
        "bg": (0, 0, 0),
        "bar": (24, 18, 8),
        "line": (82, 58, 22),
        "title": (255, 236, 179),
        "text": (255, 213, 128),
    },
}

FONT_PATHS = [
    "/usr/share/fonts/truetype/noto/NotoSansMono-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
]

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
$tt uptime             Show bot uptime
$tt about              Show bot summary
$tt reset              Clear bot runtime state
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
$buf save <file.txt>   Save output buffer on server
$buf clear             Clear session output buffer
$buf status            Show output buffer status
$shot [lines]          Send output as terminal image
$shot wide [lines]     Send wider output image
$shot clear [lines]    Send image and clear buffer
$shot file <path.png>  Save and send output image
$shot title <text>     Set screenshot title
$shot theme <name>     Set theme: green, white, amber
$shot reset            Reset screenshot settings
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


def reset_runtime_state():
    global current_msg
    global current_event
    global raw_command_output_buffer
    global command_output_buffer
    global output_revision
    global current_log_path
    global current_output_mode
    global current_output_no_session
    global current_shot_clear_after
    global current_shot_save_path
    global current_command_started_at
    global current_command_last_activity

    current_msg = None
    current_event = None
    raw_command_output_buffer = ""
    command_output_buffer = ""
    current_log_path = None
    current_output_mode = "chat"
    current_output_no_session = False
    current_shot_clear_after = False
    current_shot_save_path = None
    current_command_started_at = None
    current_command_last_activity = None
    output_revision += 1


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



def load_terminal_font(size):
    for font_path in FONT_PATHS:
        if Path(font_path).exists():
            return ImageFont.truetype(font_path, size=size)

    return ImageFont.load_default()


def text_metrics(font):
    bbox = font.getbbox("M")
    width = max(8, bbox[2] - bbox[0] + 1)
    height = max(16, bbox[3] - bbox[1] + 6)
    return width, height


ANSI_SGR_RE = re.compile(r'\x1b\[([0-9;]*)m')

ANSI_BASE_COLORS = {
    30: (0, 0, 0),
    31: (205, 49, 49),
    32: (13, 188, 121),
    33: (229, 229, 16),
    34: (36, 114, 200),
    35: (188, 63, 188),
    36: (17, 168, 205),
    37: (229, 229, 229),
}

ANSI_BRIGHT_COLORS = {
    90: (102, 102, 102),
    91: (241, 76, 76),
    92: (35, 209, 139),
    93: (245, 245, 67),
    94: (59, 142, 234),
    95: (214, 112, 214),
    96: (41, 184, 219),
    97: (255, 255, 255),
}


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


def parse_ansi_color_segments(text, default_color):
    text = text.replace("\r", "")
    segments = []
    current_color = default_color
    idx = 0

    def append_chunk(chunk):
        if chunk:
            segments.append((chunk, current_color))

    for match in ansi_escape.finditer(text):
        chunk = text[idx:match.start()]
        append_chunk(chunk)

        sequence = match.group(0)
        if sequence.endswith("m") and sequence.startswith("\x1b["):
            codes_text = sequence[2:-1]
            codes = [int(code) for code in codes_text.split(";") if code] if codes_text else [0]
            i = 0

            while i < len(codes):
                code = codes[i]

                if code == 0 or code == 39:
                    current_color = default_color
                elif 30 <= code <= 37:
                    current_color = ANSI_BASE_COLORS.get(code, default_color)
                elif 90 <= code <= 97:
                    current_color = ANSI_BRIGHT_COLORS.get(code, default_color)
                elif code in (38, 48) and i + 1 < len(codes):
                    mode = codes[i + 1]
                    if mode == 5 and i + 2 < len(codes):
                        value = codes[i + 2]
                        if code == 38:
                            current_color = xterm_color(value)
                        i += 2
                    elif mode == 2 and i + 4 < len(codes):
                        r, g, b = codes[i + 2:i + 5]
                        if code == 38:
                            current_color = (r, g, b)
                        i += 4
                i += 1

        idx = match.end()

    append_chunk(text[idx:])

    if not segments:
        return [(text, default_color)]

    return segments


def clip_segments(segments, max_cols):
    clipped = []
    visible = 0

    for piece, color in segments:
        if visible >= max_cols:
            break

        remaining = max_cols - visible
        text = piece[:remaining]
        if text:
            clipped.append((text, color))
            visible += len(text)

    return clipped


async def send_terminal_screenshot(event, content, wide=False, save_path=None):
    if not content.strip():
        content = "Output buffer is empty."

    theme = SHOT_THEMES.get(shot_theme, SHOT_THEMES["green"])
    content = content.replace("\r", "")

    if wide:
        max_lines = 54
        max_cols = 180
        font_size = 16
    else:
        max_lines = 44
        max_cols = 145
        font_size = 17

    font = load_terminal_font(font_size)
    title_font = load_terminal_font(16)
    cell_width, cell_height = text_metrics(font)
    pad_x = 28
    pad_top = 78
    pad_bottom = 28
    title_height = 56
    width = pad_x * 2 + max_cols * cell_width
    height = pad_top + max_lines * cell_height + pad_bottom

    lines = content.splitlines()[-max_lines:]
    rendered_lines = []
    has_color = False

    for line in lines:
        segments = clip_segments(parse_ansi_color_segments(line, theme["text"]), max_cols)
        if any(color != theme["text"] for _, color in segments):
            has_color = True
        rendered_lines.append(segments)

    image = Image.new("RGB", (width, height), theme["bg"])
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width, title_height), fill=theme["bar"])
    draw.rectangle((0, title_height, width, title_height + 2), fill=theme["line"])
    draw.ellipse((22, 20, 38, 36), fill=(255, 95, 87))
    draw.ellipse((50, 20, 66, 36), fill=(255, 189, 46))
    draw.ellipse((78, 20, 94, 36), fill=(40, 200, 64))
    draw.text((120, 18), shot_title[:80], fill=theme["title"], font=title_font)

    y = pad_top

    for segments in rendered_lines:
        x = pad_x
        for piece, color in segments:
            draw.text((x, y), piece, fill=color, font=font)
            x += cell_width * len(piece)
        y += cell_height

    if save_path:
        image_path = Path(save_path).expanduser()
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(image_path, "PNG")
        await event.reply(f"Terminal screenshot saved: {image_path}", file=str(image_path))
        return

    caption = "Terminal screenshot with color:" if has_color else "Terminal screenshot:"

    with tempfile.TemporaryDirectory() as tmp_dir:
        image_path = Path(tmp_dir) / "telegram-terminal.png"
        image.save(image_path, "PNG")
        await event.reply(caption, file=str(image_path))


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
        f"Shot theme: {shot_theme}\n"
        f"Shot title: {shot_title}\n"
        f"Editor: {editor}"
    )


async def stream_shell_output():

    global current_msg
    global current_event
    global output_buffer
    global raw_output_buffer
    global command_output_buffer
    global raw_command_output_buffer
    global output_revision
    global current_output_mode
    global current_output_no_session
    global current_shot_clear_after
    global current_shot_save_path
    global current_command_started_at
    global current_command_last_activity

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

                    raw_data = data
                    command_finished = False

                    if DONE_MARKER in raw_data:
                        raw_data = raw_data.replace(DONE_MARKER, "")
                        command_finished = True

                    cleaned = clean_output(raw_data)

                    if not current_output_no_session:
                        output_buffer += cleaned
                        raw_output_buffer += raw_data

                    command_output_buffer += cleaned
                    raw_command_output_buffer += raw_data

                    output_buffer = output_buffer[-MAX_BUFFER_SIZE:]
                    raw_output_buffer = raw_output_buffer[-MAX_BUFFER_SIZE:]
                    command_output_buffer = command_output_buffer[-MAX_BUFFER_SIZE:]
                    raw_command_output_buffer = raw_command_output_buffer[-MAX_BUFFER_SIZE:]

                    now = time.time()
                    current_command_last_activity = now

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
                                        raw_command_output_buffer,
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
                                    current_msg = None
                                    current_event = None
                                    command_output_buffer = ""
                                    raw_command_output_buffer = ""
                                    current_command_started_at = None
                                    current_command_last_activity = None
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

                                    target_event = current_event or current_msg
                                    await send_text_file(
                                        target_event,
                                        command_output_buffer,
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
                                raw_command_output_buffer = ""
                                current_output_mode = "chat"
                                current_output_no_session = False
                                current_shot_clear_after = False
                                current_shot_save_path = None
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
    global output_revision
    global last_command
    global log_enabled
    global current_log_path
    global current_output_mode
    global current_output_no_session
    global current_shot_clear_after
    global current_shot_save_path
    global current_command_started_at
    global current_command_last_activity
    global raw_output_buffer
    global raw_command_output_buffer
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

    if command_key == "tt uptime":
        await event.reply(tg_code(format_duration(time.time() - started_at)))
        return

    if command_key == "tt about":
        await event.reply(tg_code(about_text()))
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

    if command_key == "shot reset":
        shot_theme = "green"
        shot_title = "telegram-terminal"
        await event.reply(tg_code("Screenshot settings reset."))
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

        shot_source = raw_output_buffer or output_buffer

        if tail_arg:
            try:
                line_count = max(1, int(tail_arg))
            except ValueError:
                line_count = 80
            shot_source = "\n".join(shot_source.splitlines()[-line_count:])

        await send_terminal_screenshot(event, shot_source, wide=wide, save_path=save_path)

        if clear_after:
            output_buffer = ""
            raw_output_buffer = ""
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

    if command_key == "buf status":
        await event.reply(tg_code(buffer_status()))
        return

    if command_key == "buf clear":
        output_buffer = ""
        raw_output_buffer = ""
        output_revision += 1
        await event.reply(tg_code("Session output buffer cleared."))
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
    raw_command_output_buffer = ""
    output_revision += 1
    current_event = event
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

    print("Bhai shell is running...")

    asyncio.create_task(
        stream_shell_output()
    )
    asyncio.create_task(
        shell_watchdog()
    )

    await client.start()

    await client.run_until_disconnected()

asyncio.run(main())
