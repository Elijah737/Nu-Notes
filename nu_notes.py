#!/usr/bin/env python3
"""
nu_notes — hierarchical notebook/note TUI
Storage: ~/nu_notes/  (folders = notebooks, .txt files = notes)

Navigation
  Up / Down      move selection in list
  Right          enter notebook  OR  open note into editor
  Left           go up one level (back to parent notebook)
  Tab            cycle list → editor → actions
  Ctrl+Q         save & quit
  Ctrl+S         save current note

Actions bar (Tab to reach, Left/Right to move, Enter or letter shortcut)
  [N] New Note   [B] New Notebook   [D] Delete   [C] Copy   [Q] Exit
"""

import curses
import os
import shutil
import textwrap

ROOT = os.path.expanduser("~/nu_notes")

PANE_LIST    = 0
_PHOSPHOR_ATTR = curses.A_BOLD   # overridden in main() after color init
PANE_EDITOR  = 1
PANE_ACTIONS = 2


# ── Filesystem helpers ────────────────────────────────────────────────────────

def ensure_dir(path):
    os.makedirs(path, exist_ok=True)

def list_dir(path):
    """
    Return sorted (name, is_notebook) pairs for items in path.
    Notebooks (dirs) come first, then notes (.txt files).
    """
    try:
        entries = os.listdir(path)
    except OSError:
        return []
    notebooks = sorted(e for e in entries
                       if os.path.isdir(os.path.join(path, e))
                       and not e.startswith("."))
    notes     = sorted(e[:-4] for e in entries
                       if e.endswith(".txt")
                       and os.path.isfile(os.path.join(path, e)))
    return [(n, True) for n in notebooks] + [(n, False) for n in notes]

def note_path(directory, name):
    return os.path.join(directory, name + ".txt")

def read_note(directory, name):
    try:
        with open(note_path(directory, name), "r") as f:
            return f.read()
    except OSError:
        return ""

def write_note(directory, name, content):
    with open(note_path(directory, name), "w") as f:
        f.write(content)

def safe_name(s):
    return s.replace("/", "_").replace("\\", "_").strip()


# ── Word-wrap helpers ─────────────────────────────────────────────────────────

def wrap_lines(logical_lines, width):
    visual_rows, row_map = [], []
    for li, line in enumerate(logical_lines):
        if not line:
            visual_rows.append("")
            row_map.append((li, 0))
        else:
            segs = textwrap.wrap(line, width,
                                 drop_whitespace=False,
                                 break_long_words=True,
                                 break_on_hyphens=False) or [""]
            col = 0
            for seg in segs:
                visual_rows.append(seg)
                row_map.append((li, col))
                col += len(seg)
    return visual_rows, row_map

def logical_to_visual(lrow, lcol, row_map):
    best = 0
    for vr, (li, cs) in enumerate(row_map):
        if li == lrow and cs <= lcol:
            best = vr
    li, cs = row_map[best]
    return best, lcol - cs


# ── UI helpers ────────────────────────────────────────────────────────────────

def draw_border(win, title="", active=False):
    # active pane gets phosphor green bold border; inactive is dim
    # We receive the phosphor color pair index via a module-level sentinel;
    # actual styling is applied by the caller passing active=True/False.
    # Here we just store the flag and let the color be applied after init.
    h, w = win.getmaxyx()
    if active:
        attr = _PHOSPHOR_ATTR
    else:
        attr = curses.A_DIM
    win.attron(attr)
    try:
        win.border()
    except curses.error:
        pass
    if title:
        try:
            win.addstr(0, 2, f" {title} "[:w - 4], attr)
        except curses.error:
            pass
    win.attroff(attr)

def prompt_input(stdscr, prompt, max_len=60):
    sh, sw = stdscr.getmaxyx()
    bw = min(max(len(prompt) + 6, 34), sw - 4)
    bh = 5
    win = curses.newwin(bh, bw, (sh - bh) // 2, (sw - bw) // 2)
    win.keypad(True)
    curses.curs_set(1)
    text = ""
    while True:
        win.erase()
        win.border()
        try:
            win.addstr(1, 2, prompt[:bw - 4])
            win.addstr(2, 2, "─" * (bw - 4))
            disp = text[-(bw - 6):]
            win.addstr(3, 2, disp)
            win.move(3, 2 + len(disp))
        except curses.error:
            pass
        win.refresh()
        ch = win.getch()
        if ch in (curses.KEY_ENTER, 10, 13):
            curses.curs_set(0)
            return text.strip() or None
        elif ch == 27:
            curses.curs_set(0)
            return None
        elif ch in (curses.KEY_BACKSPACE, 127, 8):
            text = text[:-1]
        elif 32 <= ch <= 126 and len(text) < max_len:
            text += chr(ch)

def show_message(stdscr, msg, color_pair=3):
    sh, sw = stdscr.getmaxyx()
    bw = min(len(msg) + 6, sw - 4)
    win = curses.newwin(3, bw, (sh - 3) // 2, (sw - bw) // 2)
    win.border()
    try:
        win.addstr(1, 3, msg[:bw - 6], curses.color_pair(color_pair))
    except curses.error:
        pass
    win.refresh()
    win.getch()


# ── Main ──────────────────────────────────────────────────────────────────────

def main(stdscr):
    ensure_dir(ROOT)
    curses.raw()
    curses.noecho()
    stdscr.keypad(True)
    curses.curs_set(0)
    curses.start_color()
    curses.use_default_colors()

    # Phosphor green: redefine a color slot to #33FF33 if the terminal allows it,
    # otherwise fall back to the nearest built-in green.
    if curses.can_change_color() and curses.COLORS >= 16:
        curses.init_color(10, 200, 1000, 200)   # 0-1000 scale ≈ #33FF33
        PHOSPHOR = 10
    else:
        PHOSPHOR = curses.COLOR_GREEN

    curses.init_pair(1, PHOSPHOR,           -1)               # phosphor on black
    curses.init_pair(2, curses.COLOR_BLACK,  PHOSPHOR)        # selected highlight
    curses.init_pair(3, curses.COLOR_RED,    -1)              # error
    curses.init_pair(4, PHOSPHOR,            -1)              # notebook label
    curses.init_pair(5, curses.COLOR_BLACK,  PHOSPHOR)        # active action

    PHO = curses.color_pair(1) | curses.A_BOLD                # bright phosphor attr
    global _PHOSPHOR_ATTR
    _PHOSPHOR_ATTR = PHO

    # ── Navigation state ──────────────────────────────────────────────────
    # cwd_stack: list of directories from ROOT down to current level
    # Each entry is (directory_path, selected_index_when_we_left)
    # so we can restore position when pressing Left.
    cwd_stack   = []          # [(parent_dir, sel_idx_in_parent), ...]
    current_dir = ROOT
    items       = list_dir(current_dir)   # [(name, is_notebook), ...]
    sel         = 0           # selected index in current level

    active_pane     = PANE_LIST
    selected_action = 0

    # ── Editor state ──────────────────────────────────────────────────────
    open_note_name = None     # name of the currently open note (no .txt)
    open_note_dir  = None     # directory that note lives in
    editor_lines   = [""]
    editor_row     = 0
    editor_col     = 0
    editor_vscroll = 0

    ACTIONS     = ["[N] New Note", "[B] New Notebook", "[D] Delete", "[C] Copy", "[Q] Exit"]
    ACTION_KEYS = ['n', 'b', 'd', 'c', 'q']

    # ── Helpers ───────────────────────────────────────────────────────────

    def refresh_items():
        nonlocal items, sel
        items = list_dir(current_dir)
        sel   = max(0, min(sel, len(items) - 1))

    def open_note(directory, name):
        nonlocal open_note_name, open_note_dir
        nonlocal editor_lines, editor_row, editor_col, editor_vscroll
        # autosave previous note
        autosave()
        open_note_name = name
        open_note_dir  = directory
        content = read_note(directory, name)
        editor_lines = content.split("\n") if content else [""]
        editor_lines = editor_lines or [""]
        editor_row   = len(editor_lines) - 1
        editor_col   = len(editor_lines[editor_row])
        editor_vscroll = 0

    def autosave():
        if open_note_name and open_note_dir:
            write_note(open_note_dir, open_note_name, "\n".join(editor_lines))

    # ── Main loop ─────────────────────────────────────────────────────────

    while True:
        sh, sw = stdscr.getmaxyx()
        if sh < 10 or sw < 40:
            stdscr.erase()
            try:
                stdscr.addstr(0, 0, "Terminal too small — please resize.")
            except curses.error:
                pass
            stdscr.refresh()
            stdscr.getch()
            continue

        STATUS_H  = 2
        LIST_W    = max(22, sw // 4)
        EDITOR_W  = sw - LIST_W
        CONTENT_H = sh - STATUS_H

        edit_inner_h = CONTENT_H - 2
        edit_inner_w = EDITOR_W  - 3

        # Build wrapped view
        visual_rows, row_map = wrap_lines(editor_lines, edit_inner_w)
        total_vrows = len(visual_rows)
        vis_cursor_row, vis_cursor_x = logical_to_visual(
            editor_row, editor_col, row_map)

        if vis_cursor_row < editor_vscroll:
            editor_vscroll = vis_cursor_row
        elif vis_cursor_row >= editor_vscroll + edit_inner_h:
            editor_vscroll = vis_cursor_row - edit_inner_h + 1

        stdscr.erase()
        stdscr.noutrefresh()

        # ── Left pane: hierarchy list ─────────────────────────────────────
        list_win = curses.newwin(CONTENT_H, LIST_W, 0, 0)
        list_win.keypad(True)

        # Build title showing breadcrumb depth
        rel = os.path.relpath(current_dir, ROOT)
        crumb = "Notes" if rel == "." else "Notes / " + rel.replace(os.sep, " / ")
        draw_border(list_win, crumb, active=(active_pane == PANE_LIST))

        list_inner_h = CONTENT_H - 2
        # scroll list so selected item is visible
        list_scroll = max(0, sel - list_inner_h + 1) if sel >= list_inner_h else 0

        for i in range(list_inner_h):
            idx = i + list_scroll
            if idx >= len(items):
                break
            name, is_nb = items[idx]
            # prefix: 📁 for notebook, blank for note; use ASCII fallbacks
            prefix = "  + " if is_nb else "    "
            disp   = (prefix + name)[:LIST_W - 3]
            try:
                if idx == sel:
                    list_win.addstr(
                        i + 1, 1,
                        f"{disp:<{LIST_W-3}}",
                        curses.color_pair(2) | curses.A_BOLD,
                    )
                elif is_nb:
                    list_win.addstr(i + 1, 1, disp, curses.color_pair(4) | curses.A_BOLD)
                else:
                    list_win.addstr(i + 1, 1, disp)
            except curses.error:
                pass

        if not items:
            try:
                list_win.addstr(1, 2, "(empty)")
            except curses.error:
                pass

        # Show a small "^" up indicator if there's a parent to go back to
        if cwd_stack:
            try:
                list_win.addstr(CONTENT_H - 1, 2, " <- Left to go up ", curses.A_DIM)
            except curses.error:
                pass

        list_win.noutrefresh()

        # ── Right pane: editor ────────────────────────────────────────────
        edit_win = curses.newwin(CONTENT_H, EDITOR_W, 0, LIST_W)
        edit_win.keypad(True)
        note_title = open_note_name if open_note_name else "No note open"
        if open_note_dir and open_note_dir != ROOT:
            rel_dir = os.path.relpath(open_note_dir, ROOT)
            note_title = rel_dir.replace(os.sep, " / ") + " / " + note_title
        draw_border(edit_win, note_title, active=(active_pane == PANE_EDITOR))

        for screen_row in range(edit_inner_h):
            vrow = screen_row + editor_vscroll
            if vrow < total_vrows:
                try:
                    edit_win.addstr(screen_row + 1, 2, visual_rows[vrow][:edit_inner_w])
                except curses.error:
                    pass

        edit_win.noutrefresh()

        # ── Bottom: action bar (no hint text, clean) ──────────────────────
        act_win = curses.newwin(STATUS_H, sw, CONTENT_H, 0)
        act_win.keypad(True)
        draw_border(act_win, "", active=(active_pane == PANE_ACTIONS))

        x_off = 2
        for i, action in enumerate(ACTIONS):
            attr = (curses.color_pair(5) | curses.A_BOLD
                    if active_pane == PANE_ACTIONS and i == selected_action
                    else curses.A_NORMAL)
            label = f" {action} "
            if x_off + len(label) < sw - 2:
                try:
                    act_win.addstr(0, x_off, label, attr)
                except curses.error:
                    pass
                x_off += len(label) + 1

        act_win.noutrefresh()

        # Cursor — edit_win refreshed LAST so doupdate places cursor there.
        # We also paint the character cell under the cursor with phosphor
        # highlight (black on phosphor-green) so it's visible regardless
        # of the terminal's own cursor rendering.
        if active_pane == PANE_EDITOR and open_note_name:
            curses.curs_set(2)
            scr_y = max(1, min(vis_cursor_row - editor_vscroll + 1, edit_inner_h))
            scr_x = max(2, min(vis_cursor_x + 2, EDITOR_W - 2))
            # Highlight the cell under the cursor
            vrow_idx = vis_cursor_row
            if vrow_idx < len(visual_rows):
                row_text = visual_rows[vrow_idx]
                char_at_cursor = (row_text[vis_cursor_x]
                                  if vis_cursor_x < len(row_text) else " ")
            else:
                char_at_cursor = " "
            try:
                edit_win.addstr(scr_y, scr_x, char_at_cursor,
                                curses.color_pair(2) | curses.A_BOLD)
                edit_win.move(scr_y, scr_x)
            except curses.error:
                pass
            edit_win.noutrefresh()
        else:
            curses.curs_set(0)
            edit_win.noutrefresh()

        curses.doupdate()

        # ── Input ─────────────────────────────────────────────────────────
        if active_pane == PANE_EDITOR and open_note_name:
            ch = edit_win.getch()
        elif active_pane == PANE_ACTIONS:
            ch = act_win.getch()
        else:
            ch = list_win.getch()

        # ── Global keys ───────────────────────────────────────────────────

        if ch == 17:        # Ctrl+Q
            autosave()
            break

        if ch == 19:        # Ctrl+S
            autosave()
            continue

        if ch == 9:         # Tab — cycle panes
            active_pane = (active_pane + 1) % 3
            continue

        # ── List pane ─────────────────────────────────────────────────────
        if active_pane == PANE_LIST:

            if ch == curses.KEY_UP:
                sel = max(0, sel - 1)

            elif ch == curses.KEY_DOWN:
                sel = min(len(items) - 1, sel + 1)

            elif ch == curses.KEY_RIGHT and items:
                name, is_nb = items[sel]
                if is_nb:
                    # Descend into notebook
                    cwd_stack.append((current_dir, sel))
                    current_dir = os.path.join(current_dir, name)
                    ensure_dir(current_dir)
                    items = list_dir(current_dir)
                    sel   = 0
                else:
                    # Open note into editor
                    open_note(current_dir, name)
                    active_pane = PANE_EDITOR

            elif ch == curses.KEY_LEFT:
                if cwd_stack:
                    current_dir, sel = cwd_stack.pop()
                    items = list_dir(current_dir)

        # ── Editor pane ───────────────────────────────────────────────────
        elif active_pane == PANE_EDITOR and open_note_name:
            cur_line = editor_lines[editor_row]

            if ch == curses.KEY_UP:
                if vis_cursor_row > 0:
                    pv = vis_cursor_row - 1
                    li, cs = row_map[pv]
                    editor_row = li
                    editor_col = min(cs + vis_cursor_x,
                                     cs + len(visual_rows[pv]),
                                     len(editor_lines[li]))

            elif ch == curses.KEY_DOWN:
                if vis_cursor_row < total_vrows - 1:
                    nv = vis_cursor_row + 1
                    li, cs = row_map[nv]
                    editor_row = li
                    editor_col = min(cs + vis_cursor_x,
                                     cs + len(visual_rows[nv]),
                                     len(editor_lines[li]))

            elif ch == curses.KEY_LEFT:
                if editor_col > 0:
                    editor_col -= 1
                elif editor_row > 0:
                    editor_row -= 1
                    editor_col = len(editor_lines[editor_row])

            elif ch == curses.KEY_RIGHT:
                if editor_col < len(cur_line):
                    editor_col += 1
                elif editor_row < len(editor_lines) - 1:
                    editor_row += 1
                    editor_col = 0

            elif ch == curses.KEY_HOME:
                li, cs = row_map[vis_cursor_row]
                editor_col = cs

            elif ch == curses.KEY_END:
                li, cs = row_map[vis_cursor_row]
                editor_col = cs + len(visual_rows[vis_cursor_row])

            elif ch in (curses.KEY_BACKSPACE, 127, 8):
                if editor_col > 0:
                    editor_lines[editor_row] = cur_line[:editor_col-1] + cur_line[editor_col:]
                    editor_col -= 1
                elif editor_row > 0:
                    prev = editor_lines[editor_row - 1]
                    editor_col = len(prev)
                    editor_lines[editor_row - 1] = prev + cur_line
                    editor_lines.pop(editor_row)
                    editor_row -= 1
                autosave()

            elif ch == curses.KEY_DC:
                if editor_col < len(cur_line):
                    editor_lines[editor_row] = cur_line[:editor_col] + cur_line[editor_col+1:]
                elif editor_row < len(editor_lines) - 1:
                    editor_lines[editor_row] = cur_line + editor_lines[editor_row + 1]
                    editor_lines.pop(editor_row + 1)
                autosave()

            elif ch in (curses.KEY_ENTER, 10, 13):
                tail = cur_line[editor_col:]
                editor_lines[editor_row] = cur_line[:editor_col]
                editor_row += 1
                editor_lines.insert(editor_row, tail)
                editor_col = 0
                autosave()

            elif 32 <= ch <= 126:
                editor_lines[editor_row] = cur_line[:editor_col] + chr(ch) + cur_line[editor_col:]
                editor_col += 1
                autosave()

        # ── Actions pane ──────────────────────────────────────────────────
        elif active_pane == PANE_ACTIONS:

            if ch == curses.KEY_LEFT:
                selected_action = (selected_action - 1) % len(ACTIONS)
            elif ch == curses.KEY_RIGHT:
                selected_action = (selected_action + 1) % len(ACTIONS)
            elif ch in (curses.KEY_ENTER, 10, 13) or (
                    32 <= ch <= 126 and chr(ch).lower() in ACTION_KEYS):

                idx = (ACTION_KEYS.index(chr(ch).lower())
                       if 32 <= ch <= 126 and chr(ch).lower() in ACTION_KEYS
                       else selected_action)

                if idx == 0:    # New Note
                    name = prompt_input(stdscr, "New note name:")
                    if name:
                        s = safe_name(name)
                        if os.path.exists(note_path(current_dir, s)):
                            show_message(stdscr, f"'{s}' already exists!")
                        else:
                            write_note(current_dir, s, "")
                            refresh_items()
                            # select the new note and open it
                            for i, (n, nb) in enumerate(items):
                                if n == s and not nb:
                                    sel = i
                                    break
                            open_note(current_dir, s)
                            active_pane = PANE_EDITOR

                elif idx == 1:  # New Notebook
                    name = prompt_input(stdscr, "New notebook name:")
                    if name:
                        s = safe_name(name)
                        nb_path = os.path.join(current_dir, s)
                        if os.path.exists(nb_path):
                            show_message(stdscr, f"'{s}' already exists!")
                        else:
                            ensure_dir(nb_path)
                            refresh_items()
                            for i, (n, nb) in enumerate(items):
                                if n == s and nb:
                                    sel = i
                                    break

                elif idx == 2:  # Delete
                    if items:
                        name, is_nb = items[sel]
                        kind = "notebook" if is_nb else "note"
                        confirm = prompt_input(
                            stdscr, f"Delete {kind} '{name}'? Type YES:")
                        if confirm and confirm.upper() == "YES":
                            target = (os.path.join(current_dir, name)
                                      if is_nb
                                      else note_path(current_dir, name))
                            if is_nb:
                                shutil.rmtree(target, ignore_errors=True)
                            else:
                                os.remove(target)
                            # if we deleted the open note, clear editor
                            if (not is_nb and open_note_name == name
                                    and open_note_dir == current_dir):
                                open_note_name = None
                                open_note_dir  = None
                                editor_lines   = [""]
                                editor_row     = 0
                                editor_col     = 0
                            refresh_items()

                elif idx == 3:  # Copy
                    if items:
                        name, is_nb = items[sel]
                        new_name = prompt_input(stdscr, f"Copy '{name}' to:")
                        if new_name:
                            s = safe_name(new_name)
                            if is_nb:
                                dst = os.path.join(current_dir, s)
                                if os.path.exists(dst):
                                    show_message(stdscr, f"'{s}' already exists!")
                                else:
                                    shutil.copytree(
                                        os.path.join(current_dir, name), dst)
                                    refresh_items()
                            else:
                                if os.path.exists(note_path(current_dir, s)):
                                    show_message(stdscr, f"'{s}' already exists!")
                                else:
                                    shutil.copy2(note_path(current_dir, name),
                                                 note_path(current_dir, s))
                                    refresh_items()

                elif idx == 4:  # Exit
                    autosave()
                    break


def run():
    ensure_dir(ROOT)
    try:
        curses.wrapper(main)
    except KeyboardInterrupt:
        pass
    print("Thanks for using nu_notes!  Notes saved in ~/nu_notes/")


if __name__ == "__main__":
    run()
