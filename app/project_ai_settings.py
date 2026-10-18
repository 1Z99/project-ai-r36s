"""Shared settings UI; persistence and engine changes belong to its host app."""

import time

import project_ai_r36s as app


PAGES = ("MODELS", "SYSTEM PROMPT", "GENERATION", "VOICE", "APPEARANCE", "SYSTEM INFO")
NAV_X, NAV_Y, NAV_W, NAV_STEP = 24, 120, 164, 46
DETAIL_X, DETAIL_W, ROW_Y = 208, 404, 151
PROMPT_RECT = (DETAIL_X, 149, DETAIL_W, 224)
PROMPT_APPLY = (DETAIL_X, 383, 112, 28)
PROMPT_RESET = (DETAIL_X + 124, 383, 112, 28)
EDITOR_RECT = (app.CONTENT_X, 114, app.CONTENT_W, 120)
PROMPT_ACTIONS = ["SPACE", "DEL", "ALL", "APPLY", "CANCEL"]
PROMPT_KEYS = app.KEYBOARD[:-1] + [PROMPT_ACTIONS]
OPTIONS = {
    "context": (512, 768, 1024),
    "max_tokens": (64, 96, 128, 160, 192, 256),
    "temperature": (0.0, 0.1, 0.25, 0.5, 0.7, 1.0),
    "highlight": (False, True),
    "speech_rate": (0.75, 1.0, 1.25, 1.5),
    "background": (0, 10, 20, 30, 40),
    "intro": (False, True),
}
PAGE_ROWS = {
    2: (("context", "CONTEXT WINDOW"), ("max_tokens", "REPLY LIMIT"),
        ("temperature", "TEMPERATURE")),
    3: (("highlight", "WORD HIGHLIGHT"), ("speech_rate", "READING SPEED")),
    4: (("background", "BACKGROUND OPACITY"), ("intro", "OPENING ANIMATION")),
}


def contains(rect, x, y):
    return rect[0] <= x < rect[0] + rect[2] and rect[1] <= y < rect[1] + rect[3]


class SettingsPanel:
    def __init__(self, choices, on_model=None, on_background=None, config=None,
                 on_change=None, preview=True):
        self.choices = choices
        self.on_model = on_model
        self.on_background = on_background
        self.on_change = on_change
        self.preview = preview
        self.saved_notice = "Changes stay in this preview session only." if preview else "Settings are saved on this device."
        self.values = {"model": choices[0][0] if choices else "fast", "system_prompt": app.SYSTEM_PROMPT,
                       "context": 768, "max_tokens": 160, "temperature": 0.25,
                       "highlight": True, "speech_rate": 1.0, "background": 20, "intro": True}
        if config:
            self.values.update({key: config[key] for key in self.values if key in config})
        self.page = self.row = 0
        self.detail_focus = self.editing = self.select_all = False
        self.draft = ""
        self.cursor = self.prompt_scroll = 0
        self.keyboard_row = self.keyboard_col = 0
        self.prompt_lines = []
        self.prompt_layout_key = None
        self.prompt_font = None
        self.prompt_follow_cursor = True
        self.note = self.saved_notice

    def open(self, model):
        self.values["model"] = model
        self.editing = self.select_all = False
        self.note = self.saved_notice

    def store(self, key, value):
        if self.on_change:
            self.note = "Could not apply this setting; previous value retained."
            if not self.on_change(key, value):
                return False
        self.values[key] = value
        return True

    def focus_rect(self):
        if self.editing:
            x, width = app.key_geometry(self.keyboard_row)[self.keyboard_col]
            return x, app.KEY_TOP + self.keyboard_row * app.KEY_STEP, width, app.KEY_HEIGHT
        if not self.detail_focus:
            return NAV_X, NAV_Y + self.page * NAV_STEP, NAV_W, 40
        if self.page == 1:
            return PROMPT_APPLY
        return self.row_rect(self.row)

    def render_key(self):
        return (self.page, self.row, self.detail_focus, self.editing, self.select_all,
                self.draft, self.cursor, self.prompt_scroll, self.prompt_follow_cursor,
                self.keyboard_row, self.keyboard_col, self.note, tuple(self.values.items()))

    def row_rect(self, index):
        step = 56 if self.page == 0 else 52
        return DETAIL_X, ROW_Y + index * step, DETAIL_W, step - 6

    def edit_prompt(self):
        self.draft = self.values["system_prompt"]
        self.cursor = len(self.draft)
        self.editing, self.select_all = True, False
        self.detail_focus = True
        self.keyboard_row = self.keyboard_col = self.prompt_scroll = 0
        self.prompt_follow_cursor = True
        self.note = "Use the keyboard below, or type on the PC." if self.preview else "A types; B deletes; X applies; Y selects all."

    def apply_prompt(self):
        if not self.draft.strip():
            self.note = "The system prompt cannot be empty."
            return
        if not self.store("system_prompt", self.draft.strip()):
            return
        self.editing = self.select_all = False
        self.prompt_scroll = 0
        self.note = "Prompt updated in preview only." if self.preview else "System prompt saved for the next answer."

    def cancel_prompt(self):
        self.editing = self.select_all = False
        self.prompt_scroll = 0
        self.note = "Prompt edit cancelled."

    def text_input(self, text):
        if not self.editing:
            return
        if self.select_all:
            self.draft, self.cursor, self.select_all = "", 0, False
        text = app.sanitize(text)[:max(0, 2000 - len(self.draft))]
        self.draft = self.draft[:self.cursor] + text + self.draft[self.cursor:]
        self.cursor += len(text)
        self.prompt_follow_cursor = True
        self.note = "APPLY saves this prompt. CANCEL discards the edit."

    def activate_prompt_key(self, key):
        if key == "APPLY":
            self.apply_prompt()
        elif key == "CANCEL":
            self.cancel_prompt()
        elif key == "DEL":
            self.key(8)
        elif key == "ALL":
            self.select_all = True
            self.prompt_follow_cursor = True
            self.note = "All text selected; type to replace it."
        else:
            self.text_input(" " if key == "SPACE" else key)

    def keyboard_hit(self, x, y):
        for row, keys in enumerate(PROMPT_KEYS):
            if app.KEY_TOP + row * app.KEY_STEP <= y < app.KEY_TOP + row * app.KEY_STEP + app.KEY_HEIGHT:
                for column, (left, width) in enumerate(app.key_geometry(row)):
                    if left <= x < left + width:
                        return row, column

    def hover(self, x, y):
        if self.editing:
            hit = self.keyboard_hit(x, y)
            if hit is not None:
                self.keyboard_row, self.keyboard_col = hit

    def controller_button(self, button):
        if self.editing:
            if button in (8, 9):
                self.keyboard_row = max(0, min(len(PROMPT_KEYS) - 1,
                                               self.keyboard_row + (-1 if button == 8 else 1)))
                self.keyboard_col = min(self.keyboard_col, len(PROMPT_KEYS[self.keyboard_row]) - 1)
            elif button in (10, 11):
                self.keyboard_col = max(0, min(len(PROMPT_KEYS[self.keyboard_row]) - 1,
                                               self.keyboard_col + (-1 if button == 10 else 1)))
            elif button == 1:
                self.activate_prompt_key(PROMPT_KEYS[self.keyboard_row][self.keyboard_col])
            elif button == 0:
                self.key(8)
            elif button == 2:
                self.apply_prompt()
            elif button == 3:
                self.activate_prompt_key("ALL")
            elif button == 4:
                self.text_input("\n")
            return
        key = {0: 27, 1: 13, 8: 1073741906, 9: 1073741905,
               10: 1073741904, 11: 1073741903}.get(button)
        if key is not None:
            return self.key(key)

    def adjust(self, direction=1):
        if self.page == 0:
            if not self.choices:
                self.note = "No installed models were found."
                return
            name, profile = self.choices[self.row]
            if not self.store("model", name):
                return
            if self.on_model:
                self.on_model(self.row)
            self.note = "Model changed in preview. Your chat stays open." if self.preview else "Model selected. Chat and draft are retained."
        elif self.page == 1:
            self.edit_prompt()
        elif self.page in PAGE_ROWS:
            key, _ = PAGE_ROWS[self.page][self.row]
            options = OPTIONS[key]
            current = self.values[key]
            if current in options:
                value = options[(options.index(current) + direction) % len(options)]
            elif direction > 0:
                value = next((option for option in options if option > current), options[0])
            else:
                value = next((option for option in reversed(options) if option < current), options[-1])
            if key == "background" and self.on_background and not self.on_background(value):
                self.note = "Background unavailable; previous value retained."
                return
            if not self.store(key, value):
                return
            self.note = "Updated in preview only; nothing sent to R36S." if self.preview else "Setting saved on this device."

    def key(self, key, modifiers=0):
        if self.editing:
            if key == 27:
                self.cancel_prompt()
            elif key == 13 and modifiers & 0xC0:
                self.apply_prompt()
            elif key == ord("a") and modifiers & 0xC0:
                self.activate_prompt_key("ALL")
            elif key in (8, 127):
                if self.select_all:
                    self.draft, self.cursor, self.select_all = "", 0, False
                elif key == 8 and self.cursor:
                    self.draft = self.draft[:self.cursor - 1] + self.draft[self.cursor:]
                    self.cursor -= 1
                elif key == 127:
                    self.draft = self.draft[:self.cursor] + self.draft[self.cursor + 1:]
                self.note = "APPLY saves this prompt. CANCEL discards the edit."
            elif key == 13:
                self.text_input("\n")
            elif key in (1073741904, 1073741903, 1073741898, 1073741901):
                self.select_all = False
                if key == 1073741898:
                    self.cursor = 0
                elif key == 1073741901:
                    self.cursor = len(self.draft)
                else:
                    self.cursor = max(0, min(len(self.draft), self.cursor + (1 if key == 1073741903 else -1)))
            self.prompt_follow_cursor = True
            return
        if key == 27:
            return "close"
        if key == 9:
            self.detail_focus = not self.detail_focus
        elif key in (1073741906, 1073741905):
            direction = -1 if key == 1073741906 else 1
            if self.detail_focus:
                count = len(self.choices) if self.page == 0 else len(PAGE_ROWS.get(self.page, ()))
                self.row = max(0, min(max(0, count - 1), self.row + direction))
            else:
                self.page = max(0, min(len(PAGES) - 1, self.page + direction))
                self.row = self.prompt_scroll = 0
        elif key in (1073741904, 1073741903):
            if self.detail_focus:
                if self.page in PAGE_ROWS:
                    self.adjust(-1 if key == 1073741904 else 1)
                elif key == 1073741904:
                    self.detail_focus = False
            else:
                self.detail_focus = True
        elif key == 13:
            if self.detail_focus:
                self.adjust()
            else:
                self.detail_focus = True

    def click(self, x, y):
        if self.editing:
            hit = self.keyboard_hit(x, y)
            if hit is not None:
                self.keyboard_row, self.keyboard_col = hit
                self.activate_prompt_key(PROMPT_KEYS[hit[0]][hit[1]])
            elif contains(EDITOR_RECT, x, y) and self.prompt_font is not None and self.prompt_lines:
                row = max(0, min(len(self.prompt_lines) - 1,
                                 (y - EDITOR_RECT[1] - 10) // app.LINE_H + self.prompt_scroll))
                text, start = self.prompt_lines[row]
                column = min(range(len(text) + 1),
                             key=lambda i: abs(app.text_width(self.prompt_font, text[:i]) - (x - app.MSG_LEFT)))
                self.cursor = min(len(self.draft), start + column)
                self.select_all = False
                self.prompt_follow_cursor = True
            return
        for index in range(len(PAGES)):
            if contains((NAV_X, NAV_Y + index * NAV_STEP, NAV_W, 40), x, y):
                self.page, self.row, self.prompt_scroll = index, 0, 0
                self.detail_focus = self.editing = self.select_all = False
                self.note = self.saved_notice
                return
        if self.page == 1:
            if contains(PROMPT_APPLY, x, y):
                self.edit_prompt()
            elif contains(PROMPT_RESET, x, y):
                if self.store("system_prompt", app.SYSTEM_PROMPT):
                    self.note = "Default prompt restored in preview." if self.preview else "Default system prompt restored and saved."
            return
        count = len(self.choices) if self.page == 0 else len(PAGE_ROWS.get(self.page, ()))
        for index in range(count):
            if contains(self.row_rect(index), x, y):
                self.row, self.detail_focus = index, True
                left_arrow = DETAIL_X + DETAIL_W - 48 <= x < DETAIL_X + DETAIL_W - 32
                self.adjust(-1 if self.page in PAGE_ROWS and left_arrow else 1)
                return

    def wheel(self, direction):
        if not direction:
            return
        if self.editing:
            self.prompt_scroll = max(0, self.prompt_scroll - direction)
            self.prompt_follow_cursor = False
        elif self.page == 1:
            self.prompt_scroll = max(0, self.prompt_scroll - direction)
        else:
            self.key(1073741906 if direction > 0 else 1073741905)

    def draw_prompt_editor(self, renderer, fonts, cache, background):
        title, body, small, _ = fonts
        app.fill_panel(renderer, 0, app.HEADER_H, app.W, app.H - app.HEADER_H,
                       app.PANEL, background)
        app.box(renderer, app.CONTENT_X, app.FRAME_TOP, app.CONTENT_W, 406, app.EDGE)
        cache.draw(title, "EDIT SYSTEM PROMPT", app.MSG_LEFT, 78, app.GREEN)
        count = f"{'PC PREVIEW' if self.preview else 'LOCAL EDIT'}  {len(self.draft)}/2000"
        cache.draw(small, count, app.MSG_RIGHT - app.text_width(small, count), 82, app.MUTED)
        app.line(renderer, app.MSG_LEFT, 106, app.MSG_RIGHT, 106, app.BORDER)
        app.fill_panel(renderer, *EDITOR_RECT, app.PANEL_2, background)
        app.box(renderer, *EDITOR_RECT, app.GREEN_DIM)
        self.prompt_font = body
        layout_key = (body, self.draft)
        if layout_key != self.prompt_layout_key:
            self.prompt_lines = app.wrap_pixels_indexed(body, self.draft or "", app.MSG_RIGHT - app.MSG_LEFT)
            self.prompt_layout_key = layout_key
        cursor_row = next((i for i in range(len(self.prompt_lines) - 1, -1, -1)
                           if self.prompt_lines[i][1] <= self.cursor), 0)
        if self.prompt_follow_cursor:
            if cursor_row < self.prompt_scroll:
                self.prompt_scroll = cursor_row
            elif cursor_row >= self.prompt_scroll + 6:
                self.prompt_scroll = cursor_row - 5
        self.prompt_scroll = min(self.prompt_scroll, max(0, len(self.prompt_lines) - 6))
        clip = app.Rect(app.MSG_LEFT, EDITOR_RECT[1] + 8, app.MSG_RIGHT - app.MSG_LEFT, EDITOR_RECT[3] - 10)
        app.SDL.SDL_RenderSetClipRect(renderer, app.byref(clip))
        for index, (text, start) in enumerate(self.prompt_lines[self.prompt_scroll:self.prompt_scroll + 6]):
            y = EDITOR_RECT[1] + 10 + index * app.LINE_H
            cache.draw(body, text, app.MSG_LEFT, y, app.GREEN if self.select_all else app.TEXT)
            if not self.select_all and cursor_row == self.prompt_scroll + index and int(time.monotonic() * 2) % 2:
                x = app.MSG_LEFT + app.text_width(body, text[:max(0, self.cursor - start)])
                app.line(renderer, x, y, x, y + 14, app.GREEN)
        app.SDL.SDL_RenderSetClipRect(renderer, None)
        cache.draw(small, app.wrap_pixels(small, self.note, app.MSG_RIGHT - app.MSG_LEFT)[0],
                   app.MSG_LEFT, 240, app.GREEN_DIM)
        app.draw_keyboard(renderer, fonts, cache, self.keyboard_row, self.keyboard_col,
                          background, action_row=PROMPT_ACTIONS)
        controls = "A TYPE  B DEL  X APPLY  Y ALL  L1 NEWLINE  R2 CLOSE"
        cache.draw(small, controls, (app.W - app.text_width(small, controls)) // 2, 450, app.MUTED)

    def draw(self, renderer, fonts, cache, background):
        if self.editing:
            self.draw_prompt_editor(renderer, fonts, cache, background)
            return
        title, body, small, _ = fonts
        app.fill_panel(renderer, 0, app.HEADER_H, app.W, app.H - app.HEADER_H,
                       (2, 5, 3, 255), background)
        app.fill_panel(renderer, app.CONTENT_X, app.FRAME_TOP, app.CONTENT_W, 406, app.PANEL, background)
        app.box(renderer, app.CONTENT_X, app.FRAME_TOP, app.CONTENT_W, 406, app.EDGE)
        cache.draw(title, "SYSTEM SETTINGS", 26, 78, app.GREEN)
        badge = "PC PREVIEW / NOT SAVED" if self.preview else "LOCAL / OFFLINE"
        cache.draw(small, badge, 612 - app.text_width(small, badge), 82, app.MUTED)
        app.line(renderer, 26, 106, 614, 106, app.BORDER)
        app.line(renderer, 195, 118, 195, 424, app.BORDER)
        for index, name in enumerate(PAGES):
            selected = index == self.page
            y = NAV_Y + index * NAV_STEP
            if selected:
                app.fill_panel(renderer, NAV_X, y, NAV_W, 40, (11, 42, 26, 255), background)
                app.box(renderer, NAV_X, y, NAV_W, 40, app.GREEN)
            cache.draw(small, f"{index + 1:02d}", NAV_X + 7, y + 6, app.GREEN_DIM if selected else app.MUTED)
            cache.draw(body, name, NAV_X + 7, y + 20, app.GREEN if selected else app.TEXT)
        cache.draw(small, "SESSION ONLY" if self.preview else "SAVED LOCALLY", 31, 414, app.MUTED)
        cache.draw(title, PAGES[self.page], DETAIL_X, 122, app.GREEN)

        if self.page == 1:
            value = self.values["system_prompt"]
            app.fill_panel(renderer, *PROMPT_RECT, app.PANEL_2, background)
            app.box(renderer, *PROMPT_RECT, app.BORDER)
            lines = app.wrap_pixels_indexed(body, value or " ", DETAIL_W - 20)
            self.prompt_scroll = min(self.prompt_scroll, max(0, len(lines) - 11))
            for i, (text, start) in enumerate(lines[self.prompt_scroll:self.prompt_scroll + 11]):
                y = PROMPT_RECT[1] + 10 + i * app.LINE_H
                cache.draw(body, text, DETAIL_X + 10, y, app.TEXT)
            for rect, label in ((PROMPT_APPLY, "EDIT"), (PROMPT_RESET, "RESET")):
                app.fill_panel(renderer, *rect, app.PANEL_3, background)
                app.box(renderer, *rect, app.GREEN_DIM)
                cache.draw(body, label, rect[0] + (rect[2] - app.text_width(body, label)) // 2,
                           rect[1] + 6, app.GREEN)
        elif self.page == 5:
            facts = (("TARGET PROFILE", "R36S / RK3326 / 640x480"),
                     ("PROCESSOR / MEMORY", "4 x Cortex-A35 / ~895 MiB"),
                     ("INFERENCE", "llama.cpp / CPU / 3 threads"),
                     ("LOCAL ENDPOINT", "127.0.0.1:39227"),
                     ("CONTEXT / REPLY BUDGET", f"{self.values['context']} / {self.values['max_tokens']} tokens"),
                     ("DATA DIRECTORY", "/roms2/tools/project_ai"))
            for index, (label, value) in enumerate(facts):
                y = ROW_Y + index * 42
                cache.draw(small, label, DETAIL_X, y, app.MUTED)
                cache.draw(body, value, DETAIL_X, y + 16, app.TEXT)
        else:
            rows = self.choices if self.page == 0 else PAGE_ROWS[self.page]
            for index, (key, label) in enumerate(rows):
                rect = self.row_rect(index)
                focused = self.detail_focus and self.row == index
                active = self.page == 0 and key == self.values["model"]
                app.fill_panel(renderer, *rect, (11, 42, 26, 255) if active or focused else app.PANEL_2, background)
                app.box(renderer, *rect, app.GREEN if focused or active else app.BORDER)
                if self.page == 0:
                    cache.draw(body, label["label"], DETAIL_X + 10, rect[1] + 7, app.GREEN if active else app.TEXT)
                    cache.draw(small, label["note"], DETAIL_X + 10, rect[1] + 29, app.MUTED)
                    if active:
                        cache.draw(small, "ACTIVE", DETAIL_X + DETAIL_W - 52, rect[1] + 10, app.GREEN)
                else:
                    value = self.values[key]
                    if isinstance(value, bool):
                        value = "ON" if value else "OFF"
                    elif key in ("context", "max_tokens"):
                        value = f"{value} tokens"
                    elif key == "background":
                        value = f"{value}%"
                    elif key == "speech_rate":
                        value = f"{value:g}x"
                    cache.draw(small, label, DETAIL_X + 10, rect[1] + 6, app.MUTED)
                    cache.draw(body, str(value), DETAIL_X + 10, rect[1] + 23, app.GREEN)
                    cache.draw(body, "<  >", DETAIL_X + DETAIL_W - 48, rect[1] + 23, app.GREEN_DIM)
            hints = {
                0: ("Select another model without closing the app.", "Chat and draft are retained."),
                2: ("TOP-K 40   TOP-P 0.9   REPEAT 1.08", "Lower temperature means less variation."),
                3: ("Offline speech: espeak-ng",
                    "F6 previews highlighting and reading speed." if self.preview else "Highlight and speed apply to spoken replies.",
                    "No audio is played in this preview." if self.preview else "Output: speaker / USB audio when available."),
                4: ("Static last-frame image; no video loop.",
                    "F9 replays the intro when enabled." if self.preview else "Opening animation setting applies next launch."),
            }
            y = 341 if self.page == 0 else 327
            for hint in hints.get(self.page, ()):
                cache.draw(small, hint, DETAIL_X, y, app.MUTED)
                y += 20
            if self.page == 0:
                profile = next((profile for name, profile in self.choices if name == self.values["model"]), None)
                if profile:
                    cache.draw(small, profile["file"], DETAIL_X, 391, app.GREEN_DIM)
        note = app.wrap_pixels(small, self.note, DETAIL_W)[0]
        cache.draw(small, note, DETAIL_X, 418, app.GREEN_DIM)
        app.line(renderer, 26, 436, 614, 436, app.BORDER)
        controls = "ARROWS NAVIGATE   ENTER SELECT   TAB FOCUS   ESC BACK"
        cache.draw(small, controls, (app.W - app.text_width(small, controls)) // 2, 450, app.MUTED)
