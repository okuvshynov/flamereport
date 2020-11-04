import curses
import os
import sys
from curses import wrapper
from math import floor
from operator import attrgetter
from random import randint

####
# next things to do:
# -- inverted view
# -- focus and bring up
# -- consolidate visual representation
# -- handle window resize

# reading stacks from stdin
def read_data():
    # to read both piped stdin and use tty in curses
    os.dup2(0, 3)
    os.close(0)
    sys.stdin = open('/dev/tty', 'r')

    data = []
    with os.fdopen(3, 'r') as stdin_piped:
        for l in stdin_piped.readlines():
            (stacks, cnt) = tuple(l.strip().split(' '))
            data.append((stacks.split(';'), int(cnt))) 

    return data

# color initialization
color_count = 0
def init_colors():
    global color_count
    # monochrome, 16 colors, 256 colors modes
    colors = []
    if curses.COLORS >= 256:
        colors = [214, 202, 208, 196, 166, 172, 178]
    elif curses.COLORS >= 16:
        colors = [curses.COLOR_RED, curses.COLOR_YELLOW, curses.COLOR_GREEN]

    for (i, c) in enumerate(colors):
        curses.init_pair(i + 1, curses.COLOR_BLACK, c)
    color_count = len(colors)

def pick_color():
    global color_count
    return randint(1, color_count) if color_count > 1 else 0

# Frame represents stack frame itself, not the 'view'
class Frame:
    def __init__(self, title, samples, children):
        self.title = title
        self.samples = samples
        self.children = children
        self.parent = None

# compressed multiframe view for presenting multiple frames in a single cell
# we need that in TUI version as some stacks would be < 1 character otherwise
class MultiFrameView:
    def __init__(self, x, y, w, frames):
        self.x = x
        self.y = y
        self.w = w
        self.frames = frames
        self.samples = sum([f.samples for f in frames])

    def frameset(self):
        return self.frames

    def frame_count(self):
        return len(self.frames)

    def draw(self, scr, highlight):
        if self.w == 0:
            return
        if self.w == 1:
            txt = "+"
        else:
            txt = "[{}]".format("+" * (self.w - 2))

        style = curses.color_pair(pick_color())
        if highlight:
            style = style | curses.A_REVERSE

        scr.addstr(self.y, self.x, txt, style)
    
    # render summary of the multiframe
    def status(self, total, height):
        if height < 1:
            return []
        summary = ["Aggregated {} frames (total {} samples, {:.2f}%)".format(self.frame_count(), self.samples, 100.0 * self.samples / total)]
        if height == 1:
            return summary
        s = ["  {} ({} samples, {:.2f}%)".format(f.title, f.samples, 100.0 * f.samples / total) for f in self.frames]
        if len(s) + 1 <= height:
            return summary + s
        fits = height - 2
        return summary + s[:fits] + ["and {} more".format(len(s) - fits)]

    def matches(self, frames):
        return False

# representation of a frame on a screen, with specific location/size
class FrameView:
    def __init__(self, x, y, w, frame):
        self.x = x
        self.y = y
        self.w = w
        self.frame = frame

    def frameset(self):
        return [self.frame]

    def frame_count(self):
        return 1

    def draw(self, scr, highlight):
        # this should never happen?
        if self.w == 0:
            return
        if self.w == 1:
            txt = "#"
        else:
            txt = "[{}]".format(self.frame.title[:self.w - 2].ljust(self.w - 2, '-'))
        style = curses.color_pair(pick_color())
        if highlight:
            style = style | curses.A_REVERSE
        scr.addstr(self.y, self.x, txt, style)

    def status(self, total, height):
        if height < 1:
            return []
        return ["{} ({} samples, {:.2f}%)".format(self.frame.title, self.frame.samples, 100.0 * self.frame.samples / total)]

    def matches(self, frames):
        return self.frame in frames

def view_contains(view, x, y):
    return view.y == y and x >= view.x and x < view.x + view.w

# set of all frames.
class FrameSet:
    def __init__(self, data):
        # this is a list of top-level frames
        self.frames = self._build_frames(data)
        # we are not settings parent to root by design.
        # root is introduced just for convenience and is never
        # visible in UI
        self.root = Frame("root", sum([a for (_, a) in data]), self.frames)

    def _build_frames(self, data):
        if not data:
            return None
        agg = {}
        for (s, n) in data:
            if not s:
                continue
            if s[0] not in agg:
                agg[s[0]] = (n, [(s[1:], n)])
            else:
                (old_cnt, old_children) = agg[s[0]]
                agg[s[0]] = (old_cnt + n, old_children + [(s[1:], n)])

        # now recursively call for each
        res = []
        for k in agg.keys():
            frame = Frame(k, agg[k][0], self._build_frames(agg[k][1]))
            for c in frame.children:
                c.parent = frame
            res.append(frame)
        return res

    # prepare views at current level of granularity and position
    def _get_views_rec(self, frames, width, s = 0, x = 0, y = 0):
        if s == 0:
            s = sum([f.samples for f in frames])
        res = []
        # these are 'small' frames
        leftovers = []
        for f in frames:
            if width * f.samples < s:
                # this means, this stack takes less than 1 character
                leftovers.append(f)
                continue
            w = int(floor(1.0 * width * f.samples / s))
            res.append(FrameView(x, y, w, f))
            res += self._get_views_rec(f.children, w, f.samples, x, y + 1)
            x = x + w
        
        # for now just append as a single frame view
        # this will work bad if ALL frames are small in current view
        # in this case, we'll never be able to dive into it
        if len(leftovers) > 1:
            samples = sum([f.samples for f in leftovers])
            w = max(1, int(floor(1.0 * width * samples / s)))
            res.append(MultiFrameView(x, y, w, leftovers))
        elif len(leftovers) == 1:
            res.append(FrameView(x, y, 1, leftovers[0]))
        return res

    # This method prepares blocks from a subset of frame set,
    # optionally focusing on a specific frame view.
    # All descendants of that frame will be shown,
    # as well as path to the root.
    def get_frame_views(self, width, focus = None):
        root_path = []
        if focus:
            frame = focus[0].parent
            while frame is not None:
                root_path.insert(0, frame)
                frame = frame.parent

        if focus is None:
            focus = self.root.children

        res = [FrameView(0, i, width, f) for (i, f) in enumerate(root_path)]
        samples = sum([f.samples for f in focus])
        res = res + self._get_views_rec(focus, width, samples, 0, len(res)) 
        res.sort(key = attrgetter("y", "x"))

        return res

class StatusArea:
    def __init__(self, stdscr):
        self.scr = stdscr
        self.old_lines = 0

    def draw(self, lines):
        if len(lines) < self.old_lines:
            for i in range(self.old_lines):
                self.scr.addstr(curses.LINES - 1 - i, 0, " " * (curses.COLS - 1))
        y = curses.LINES - len(lines)
        for (i, l) in enumerate(lines):
            self.scr.addstr(y + i, 0, l.ljust(curses.COLS - 1))
        self.old_lines = len(lines)


class FlameCLI:
    def __init__(self, stdscr):
        self.stdscr = stdscr
        curses.curs_set(0)
        curses.mousemask(curses.BUTTON1_CLICKED | curses.BUTTON1_DOUBLE_CLICKED)
        stdscr.clear()
        init_colors()

        data = read_data()
        self.frames = FrameSet(data)
        self.total_samples = sum([a for (_, a) in data])

        self.frame_views = self.frames.get_frame_views(curses.COLS)
        self.fit_into_vertical_space()
        self.build_screen_index()
        self.status_area = StatusArea(self.stdscr)
        self.selection = 0

    # rebuilding all blocks, while keeping selection
    def rebuild_views(self):
        selected_frames = self.frame_views[self.selection].frameset()
        self.frame_views = self.frames.get_frame_views(curses.COLS)
        self.fit_into_vertical_space()
        self.selection = 0
        for (i, view) in enumerate(self.frame_views):
            if view.matches(selected_frames):
                self.selection = i
                break
        self.build_screen_index()

    def build_views_focused(self):
        frames = self.frame_views[self.selection].frameset()
        self.frame_views = self.frames.get_frame_views(curses.COLS, frames)
        self.fit_into_vertical_space()
        self.selection = 0
        for (i, view) in enumerate(self.frame_views):
            if view.matches(frames):
                self.selection = i
                break
        self.build_screen_index()

    # index of 'coords' -> view
    def build_screen_index(self):
        self.screen_index = [[] for _ in range(curses.LINES)]
        for (i, v) in enumerate(self.frame_views):
            self.screen_index[v.y].append((v.x, v.x + v.w, i))
        self.assign_parents()

    def lookup_view_index(self, x, y):
        if y >= 0 and y < len(self.screen_index):
            for (a, b, vi) in self.screen_index[y]:
                if x >= a and x < b:
                    return vi
        return None

    # TODO better way would be to use frame hierarchy rather than view hierarchy
    def assign_parents(self):
        for v in self.frame_views:
            v.parent_index = self.lookup_view_index(v.x, v.y - 1)
            v.first_child_index = self.lookup_view_index(v.x, v.y + 1)

    # output status line
    def print_status_bar(self):
        status = self.frame_views[self.selection].status(self.total_samples, self.status_height)
        self.status_area.draw(status)

    # output debug line
    def ppp(self, s):
        self.stdscr.addstr(curses.LINES - 2, 0, "{}".format(s).ljust(curses.COLS))

    def pall(self): 
        self.stdscr.clear()
        for (i, _) in enumerate(self.frame_views):
            self.frame_views[i].draw(self.stdscr, i == self.selection)
        self.print_status_bar()

    def change_selection(self, s):
        if s is None:
            return False
        self.frame_views[self.selection].draw(self.stdscr, False)
        self.selection = s
        self.frame_views[self.selection].draw(self.stdscr, True)
        self.print_status_bar()
        return True

    def move_selection(self, d):
        m = len(self.frame_views)
        if self.change_selection(((self.selection + d) % m + m) % m):
            self.stdscr.refresh()

    def select_up(self):
        if self.change_selection(self.frame_views[self.selection].parent_index):
            self.stdscr.refresh()

    def select_down(self):
        if self.change_selection(self.frame_views[self.selection].first_child_index):
            self.stdscr.refresh()

    # returns a tuple (characters for chart, characters for status)
    def allocate_vertical_space(self, frame_views):
        height = curses.LINES
        if height <= 0:
            return (0, 0)
        if height == 1:
            return (1, 0)
        
        chart_area_height = 1 + max([v.y for v in frame_views])
        status_area_height = 1 + max([v.frame_count() for v in frame_views])
        
        # if everything fits, we don't need to do anything
        if chart_area_height + status_area_height <= height:
            return (chart_area_height, status_area_height)
        
        # if it doesn't fit, we'd prefer to fit the main area if we have 
        # at least one character for status
        if 1 + chart_area_height <= height:
            return (chart_area_height, height - chart_area_height)

        # otherwise, allocate 1 line for status, everything else for chart
        return (height - 1, 1)

    def fit_into_vertical_space(self):
        (graph, status) = self.allocate_vertical_space(self.frame_views)
        self.status_height = status
        self.frame_views = [v for v in self.frame_views if v.y < graph]
        # TODO: also modify last row if there were stacks below it?

    def loop(self):
        while True:
            c = self.stdscr.getch()
            if c == ord('h') or c == curses.KEY_LEFT:
                self.move_selection(-1)
                continue
            if c == ord('l') or c == curses.KEY_RIGHT:
                self.move_selection(1)
                continue
            if c == ord('k') or c == curses.KEY_UP:
                self.select_up()
                continue
            if c == ord('j') or c == curses.KEY_DOWN:
                self.select_down()
                continue
            if c == ord('r'):
                self.rebuild_views()
                self.pall()
                continue
            if c == ord('f'):
                self.build_views_focused()
                self.pall()
                continue
            if c == ord('q'):
                break
            if c == curses.KEY_MOUSE:
                (_, mx, my, _, m) = curses.getmouse()
                if m & curses.BUTTON1_CLICKED:
                    i = self.lookup_view_index(mx, my)
                    if i is not None:
                        self.change_selection(i)
                        self.stdscr.refresh()
                        continue
                if m & curses.BUTTON1_DOUBLE_CLICKED:
                    i = self.lookup_view_index(mx, my)
                    if i is not None:
                        self.change_selection(i)
                        self.build_views_focused()
                        self.pall()
                        continue

def main(stdscr):
    h = FlameCLI(stdscr)
    h.pall()
    h.loop()

wrapper(main)
