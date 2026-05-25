import asyncio
import time
import re
import shlex
import tempfile
from datetime import datetime
from pathlib import Path

import pexpect
import pyte
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
command_output_buffer = ""
raw_output_buffer = ""
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
current_live_delay = None
current_live_wide = False
shot_theme = "green"
shot_title = "telegram-terminal"
started_at = time.time()

SHOT_THEMES = {
    "green": {
        "bg": (8, 11, 16),
        "bar": (24, 30, 39),
        "line": (42, 52, 65),
        "title": (226, 232, 240),
        "text": (220, 255, 226),
        "default_fg": "#dcffe2",
    },
    "white": {
        "bg": (10, 14, 20),
        "bar": (28, 34, 44),
        "line": (58, 67, 82),
        "title": (238, 242, 247),
        "text": (235, 239, 245),
        "default_fg": "#ebeff5",
    },
    "amber": {
        "bg": (16, 12, 5),
        "bar": (42, 31, 15),
        "line": (82, 58, 22),
        "title": (255, 236, 179),
        "text": (255, 213, 128),
        "default_fg": "#ffd580",
    },
}

ANSI_PALETTE = {
    "black": "#1f2933",
    "red": "#ff5f57",
    "green": "#7ee787",
    "yellow": "#f2cc60",
    "blue": "#79c0ff",
    "magenta": "#d2a8ff",
    "cyan": "#76e3ea",
    "white": "#d8dee9",
    "brightblack": "#6b7280",
    "brightred": "#ff7b72",
    "brightgreen": "#aff5b4",
    "brightyellow": "#f7dc6f",
    "brightblue": "#a5d6ff",
    "brightmagenta": "#d8b4fe",
    "brightcyan": "#9af5ff",
    "brightwhite": "#ffffff",
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
$shot live <seconds> <cmd> Capture live fullscreen command
$shot live wide <seconds> <cmd> Capture wide live fullscreen command
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



def load_terminal_font(size):
    for font_path in FONT_PATHS:
        if Path(font_path).exists():
            return ImageFont.truetype(font_path, size=size)

    return ImageFont.load_default()


def color_to_rgb(value, fallback):
    if not value or value == "default":
        value = fallback

    if isinstance(value, str):
        value = ANSI_PALETTE.get(value, value)

        if value.startswith("#") and len(value) == 7:
            return tuple(int(value[idx:idx + 2], 16) for idx in (1, 3, 5))

    if isinstance(value, tuple):
        return value

    return color_to_rgb(fallback, "#dcffe2")


def terminal_cells(content, cols, rows):
    screen = pyte.Screen(cols, rows)
    stream = pyte.ByteStream(screen)
    stream.feed(content.encode("utf-8", errors="replace"))
    return screen


def useful_text_count(text):
    return sum(1 for char in text if char.isalnum() or char in "/._-:@~#$")


def screen_text(screen, rows):
    rendered = []

    for y, line in enumerate(screen.buffer.values()):
        if y >= rows:
            break

        rendered.append("".join(char.data for char in line.values()).rstrip())

    return "\n".join(rendered).strip()


def draw_plain_terminal(draw, content, font, theme, pad_x, pad_top, cell_height, max_cols, max_lines):
    clean = clean_output(content).replace("\r", "")
    lines = clean.splitlines()[-max_lines:]
    y = pad_top

    for line in lines:
        draw.text((pad_x, y), line[:max_cols], fill=theme["text"], font=font)
        y += cell_height


async def send_terminal_screenshot(event, content, wide=False, save_path=None):
    if not content.strip():
        content = "Output buffer is empty."

    theme = SHOT_THEMES.get(shot_theme, SHOT_THEMES["green"])

    if wide:
        cols = 170
        rows = 48
        font_size = 16
    else:
        cols = 120
        rows = 38
        font_size = 18

    font = load_terminal_font(font_size)
    bbox = font.getbbox("M")
    cell_width = max(9, bbox[2] - bbox[0] + 1)
    cell_height = max(18, bbox[3] - bbox[1] + 5)
    pad_x = 28
    pad_top = 78
    pad_bottom = 28
    title_height = 56
    width = pad_x * 2 + cols * cell_width
    height = pad_top + rows * cell_height + pad_bottom

    normalized_content = content.replace("\r\n", "\n")
    screen = terminal_cells(normalized_content, cols, rows)
    image = Image.new("RGB", (width, height), theme["bg"])
    draw = ImageDraw.Draw(image)

    draw.rectangle((0, 0, width, title_height), fill=theme["bar"])
    draw.rectangle((0, title_height, width, title_height + 2), fill=theme["line"])
    draw.ellipse((22, 20, 38, 36), fill=(255, 95, 87))
    draw.ellipse((50, 20, 66, 36), fill=(255, 189, 46))
    draw.ellipse((78, 20, 94, 36), fill=(40, 200, 64))
    draw.text((120, 18), shot_title[:80], fill=theme["title"], font=font)

    default_fg = theme["default_fg"]
    default_bg = theme["bg"]

    rendered_text = screen_text(screen, rows)
    clean_text = clean_output(normalized_content)

    if useful_text_count(rendered_text) < max(8, useful_text_count(clean_text) // 12):
        draw_plain_terminal(draw, normalized_content, font, theme, pad_x, pad_top, cell_height, cols, rows)
    else:
        for y, line in enumerate(screen.buffer.values()):
            if y >= rows:
                break

            for x, char in enumerate(line.values()):
                if x >= cols:
                    break

                data = char.data

                if not data:
                    continue

                px = pad_x + x * cell_width
                py = pad_top + y * cell_height
                fg = color_to_rgb(char.fg, default_fg)
                bg = color_to_rgb(char.bg, default_bg)

                if bg != default_bg:
                    draw.rectangle((px, py, px + cell_width, py + cell_height), fill=bg)

                draw.text((px, py), data, fill=fg, font=font)

    if save_path:
        image_path = Path(save_path).expanduser()
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(image_path, "PNG")
        await event.reply(f"Terminal screenshot saved: {image_path}", file=str(image_path))
        return

    with tempfile.TemporaryDirectory() as tmp_dir:
        image_path = Path(tmp_dir) / "telegram-terminal.png"
        image.save(image_path, "PNG")
        await event.reply("Terminal screenshot:", file=str(image_path))


async def finish_live_screenshot(delay):
    global current_msg
    global current_output_mode
    global current_output_no_session
    global current_shot_clear_after
    global current_shot_save_path
    global current_live_delay
    global current_live_wide
    global current_live_delay
    global current_live_wide
    global output_buffer
    global raw_output_buffer
    global output_revision

    await asyncio.sleep(delay)

    if current_output_mode != "live":
        return

    target_event = current_event or current_msg

    if target_event:
        await send_terminal_screenshot(
            target_event,
            raw_command_output_buffer or command_output_buffer,
            wide=current_live_wide,
            save_path=current_shot_save_path,
        )

    try:
        shell.sendintr()
    except Exception:
        pass

    if current_msg:
        try:
            await current_msg.delete()
        except Exception:
            pass

    if current_shot_clear_after:
        output_buffer = ""
        raw_output_buffer = ""
        output_revision += 1

    current_output_mode = "chat"
    current_output_no_session = False
    current_shot_clear_after = False
    current_shot_save_path = None
    current_live_delay = None
    current_live_wide = False


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
    global command_output_buffer
    global raw_output_buffer
    global raw_command_output_buffer
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

                    raw_data = data
                    cleaned = clean_output(data)

                    command_finished = False

                    if DONE_MARKER in cleaned:

                        cleaned = cleaned.replace(
                            DONE_MARKER,
                            ""
                        )
                        raw_data = raw_data.replace(
                            DONE_MARKER,
                            ""
                        )

                        command_finished = True

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

                    trimmed = command_output_buffer[-MAX_MESSAGE_OUTPUT:]

                    if command_finished:

                        if current_msg:

                            try:

                                if current_log_path:
                                    write_command_log(last_command or "", command_output_buffer, current_log_path)

                                if current_output_mode == "live":
                                    current_output_mode = "chat"
                                    current_output_no_session = False
                                    current_shot_clear_after = False
                                    current_shot_save_path = None
                                    current_live_delay = None
                                    current_live_wide = False
                                    last_text = trimmed
                                    last_edit = now
                                    continue

                                if current_output_mode == "ss":
                                    target_event = current_event or current_msg
                                    await send_terminal_screenshot(
                                        target_event,
                                        raw_command_output_buffer or command_output_buffer,
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
    global raw_output_buffer
    global raw_command_output_buffer
    global output_revision
    global last_command
    global log_enabled
    global current_log_path
    global current_output_mode
    global current_output_no_session
    global current_shot_clear_after
    global current_shot_save_path
    global current_live_delay
    global current_live_wide
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

    if command.startswith("shot live "):
        live_text = command[10:].strip()
        live_parts = live_text.split(maxsplit=2)
        current_live_wide = False
        current_shot_clear_after = False
        current_output_no_session = False
        current_shot_save_path = None

        if live_parts and live_parts[0] == "wide":
            current_live_wide = True
            live_parts = live_text.split(maxsplit=3)[1:]

        if len(live_parts) < 2:
            await event.reply(tg_code("Usage: $shot live [wide] <seconds> <command>"))
            return

        try:
            current_live_delay = max(1.0, min(30.0, float(live_parts[0])))
        except ValueError:
            await event.reply(tg_code("Usage: $shot live [wide] <seconds> <command>"))
            return

        command = live_parts[1] if len(live_parts) == 2 else live_parts[1] + " " + live_parts[2]

        if not command:
            await event.reply(tg_code("Usage: $shot live [wide] <seconds> <command>"))
            return

        command_key = command.lower().replace("+", " ").replace("-", " ")
        command_key = " ".join(command_key.split())
        command_key = aliases.get(command_key, command_key)
        current_output_mode = "live"

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

        if raw_output_buffer:
            screenshot_content = raw_output_buffer if not tail_arg else select_output_lines(raw_output_buffer, tail_arg)
        elif output_buffer:
            screenshot_content = output_buffer if not tail_arg else select_output_lines(output_buffer, tail_arg)
        else:
            screenshot_content = tail_output(tail_arg)
        await send_terminal_screenshot(event, screenshot_content, wide=wide, save_path=save_path)

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
        output_buffer = ""
        raw_output_buffer = ""
        command_output_buffer = ""
        raw_command_output_buffer = ""
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
    raw_command_output_buffer = ""
    output_revision += 1
    current_event = event
    current_log_path = create_log_path(command) if log_enabled else None

    if current_output_mode == "ss":
        current_msg = await event.reply(tg_code(f"Capturing:\n{command}"))
    elif current_output_mode == "live":
        current_msg = await event.reply(tg_code(f"Live capture ({current_live_delay}s):\n{command}"))
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

        if current_output_mode == "live":
            asyncio.create_task(finish_live_screenshot(current_live_delay or 3.0))

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
