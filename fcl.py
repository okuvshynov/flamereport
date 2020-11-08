import curses
import os
import sys
from curses import wrapper
from itertools import groupby, tee
from math import floor
from operator import attrgetter, itemgetter
from random import randint

####
# next things to do:
# -- consolidate visual representation
# -- indicate truncated frames (above and below)

# selection - can be single view at any moment of time
# focus/pin - can be set of frames

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
        # sort by samples desc.
        self.frames = sorted(frames, key=lambda f: - f.samples)
        self.samples = sum([f.samples for f in frames])
        self.color = pick_color()

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

        style = curses.color_pair(self.color)
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
        self.color = pick_color()

    def frameset(self):
        return [self.frame]

    def frame_count(self):
        return 1

    def draw(self, scr, highlight):
        # this should never happen?
        if self.w == 0:
            return
        if self.w == 1:
            txt = "-"
        else:
            txt = "[{}]".format(self.frame.title[:self.w - 2].ljust(self.w - 2, '-'))
        style = curses.color_pair(self.color)
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

# reading stacks from stdin
def read_stdin():
    # to read both piped stdin and use tty in curses
    os.dup2(0, 3)
    os.close(0)
    sys.stdin = open('/dev/tty', 'r')

    data = []
    with os.fdopen(3, 'r') as stdin_piped:
        for l in stdin_piped.readlines():
            (stacks, _, cnt) = l.strip().rpartition(' ')
            data.append((stacks.split(';'), int(cnt))) 

    return data

# set of all frames.
class FrameSet:
    def __init__(self, data):
        # this is a list of top-level frames
        self.frames = self._build_frames(data)
        self.total_samples = sum([a for (_, a) in data])
        self.total_excluded = 0

    # is used to remove empty parents
    def _exclude_empty_frame(self, frame):
        assert(frame.samples == 0)
        if frame in self.frames:
            self.frames.remove(frame)
        if frame.parent != None:
            frame.parent.children.remove(frame)

    def exclude_frames(self, frames):
        for frame in frames:
            if frame in self.frames:
                self.frames.remove(frame)
            if frame.parent != None:
                frame.parent.children.remove(frame)
            samples = frame.samples
            self.total_excluded += samples
            self.total_samples -= samples
            while frame.parent != None:
                frame.parent.samples -= samples
                if frame.parent.samples == 0:
                    assert(len(frame.parent.children) == 0)
                    # remove the parent as well
                    self._exclude_empty_frame(frame.parent)

                frame = frame.parent

    def _build_frames(self, data):
        if not data:
            return None
        res = []
        data = sorted([(s[0], s[1:], n) for (s, n) in data if s])
        for f, it in groupby(data, itemgetter(0)):
            it1, it2 = tee(it)
            samples = sum(cnt for (_, _, cnt) in it1)
            children = ((s, cc) for (_, s, cc) in it2)
            frame = Frame(f, samples, self._build_frames(children))
            for ff in frame.children:
                ff.parent = frame
            res.append(frame)

        return res

    # prepare views at current level of granularity and position
    # frames - group of frames with common parent, which we need to generate
    #          view for
    # width  - size in characters of the area available
    # s      - total number of samples to fill width
    # x, y   - coordinates on the screen
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
        # maybe a better way would be to split into several 'multiframes'
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
    # as well as path to the root. If pin is not None though, 
    # we'll only show path to the pin
    def get_frame_views(self, width, focus = None, pin = None):
        root_path = []
        # pin becomes new root selection instead of self.frames
        root_level = pin if pin is not None else self.frames
        
        if focus and (focus[0] not in root_level):
            frame = focus[0].parent
            while frame is not None:
                root_path.insert(0, frame)
                if frame in root_level:
                    break
                frame = frame.parent

        if focus is None:
            focus = root_level

        res = [FrameView(0, i, width, f) for (i, f) in enumerate(root_path)]
        samples = sum([f.samples for f in focus])
        res = res + self._get_views_rec(focus, width, samples, 0, len(res)) 
        res.sort(key = attrgetter("y", "x"))

        return res

class StatusArea:
    def __init__(self, stdscr):
        self.scr = stdscr
        self.old_lines = 0

    def draw(self, lines, warn = None):
        rows, cols = self.scr.getmaxyx()
        if len(lines) < self.old_lines:
            for i in range(self.old_lines):
                self.scr.addstr(rows - 1 - i, 0, " " * (cols - 1))
        y = rows - len(lines)
        for (i, l) in enumerate(lines):
            self.scr.addstr(y + i, 0, l.ljust(cols - 1)[:(cols - 1)])
        self.old_lines = len(lines)

        if warn == None:
            return

        # warning should fit on the screen
        warn = warn[1 - cols:]
        self.scr.addstr(rows - 1, cols - len(warn) - 1, warn)

class FlameCLI:
    def __init__(self, stdscr):
        self.stdscr = stdscr
        curses.curs_set(0)
        curses.mousemask(curses.BUTTON1_CLICKED | curses.BUTTON1_DOUBLE_CLICKED)
        stdscr.clear()
        self.status_area = StatusArea(self.stdscr)
        init_colors()

        self.data = read_stdin()
        self.build()
        self.render()
    

    # rebuilding all views, while keeping selection
    def rebuild_views(self):
        selected_frames = self.frame_views[self.selection].frameset()
        self.frame_views = self.frames.get_frame_views(self.stdscr.getmaxyx()[1], self.focus, self.pinned)
        self.fit_into_vertical_space()
        self.selection = 0
        for (i, view) in enumerate(self.frame_views):
            if view.matches(selected_frames):
                self.selection = i
                break
        self.build_screen_index()

    def clear_focus(self):
        self.focus = None
        self.pinned = None
        self.rebuild_views()
        self.render()

    def build(self):
        # index of a view currently under cursor
        # TODO: store view itself, not index
        self.selection = 0
        # list of frames on the same level with the same parent
        # this list would be expanded to 100% width, their parents would too
        self.focus = None
        # list of frames on the same level with the same parent whom we consider
        # new 'root level'
        self.pinned = None

        self.frames = FrameSet(self.data)
        self.frame_views = self.frames.get_frame_views(self.stdscr.getmaxyx()[1])        
        self.fit_into_vertical_space()
        self.build_screen_index()
        self.render()

    def set_focus(self):
        self.focus = self.frame_views[self.selection].frameset()
        self.rebuild_views()
        self.render()

    def set_pin(self):
        self.focus = self.frame_views[self.selection].frameset()
        self.pinned = self.focus
        self.rebuild_views()
        self.render()

    # index of 'coords' -> view
    # if used for navigation and mouse events
    def build_screen_index(self):
        self.screen_index = [[] for _ in range(self.stdscr.getmaxyx()[0])]
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
        for (i, v) in enumerate(self.frame_views):
            v.self_index = i
            v.parent_index = self.lookup_view_index(v.x, v.y - 1)
            v.first_child_index = self.lookup_view_index(v.x, v.y + 1)

    # output summary area
    def print_status_bar(self):
        samples = self.frames.total_samples
        excluded = self.frames.total_excluded
        if not self.frame_views:
            status = []
        else:
            view = self.frame_views[self.selection]
            status = view.status(samples, self.status_height)
        warning = None
        if excluded > 0:
            pe = 100.0 * excluded / (samples + excluded)
            warning = "{:.2f}% samples excluded".format(pe)
        self.status_area.draw(status, warning)

    def render(self): 
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
    def _allocate_vertical_space(self, frame_views):
        height = self.stdscr.getmaxyx()[0]
        if height <= 0:
            return (0, 0)
        if height == 1:
            return (1, 0)
        if not frame_views:
            return (height - 1, 1)
        
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
        (graph, status) = self._allocate_vertical_space(self.frame_views)
        self.status_height = status
        self.frame_views = [v for v in self.frame_views if v.y < graph]
        # TODO: also modify last row if there were stacks below it?

    # returns first non-empty frameset in a hierarchy
    def _find_nonempty_parent(self, frameset):
        if frameset is None:
            return None
        samples = lambda frames: sum([f.samples for f in frames])
        f = [f for f in frameset if f]
        while f:
            if samples(f) > 0:
                return f
            f = [f[0].parent]
        return None

    def exclude_frame(self):
        to_exclude = self.frame_views[self.selection].frameset()
        self.frames.exclude_frames(to_exclude)
        self.selection = 0
        # everything is removed
        if self.frames.total_samples == 0:
            self.focus = None
            self.pinned = None
            self.rebuild_views()
            self.render()
            return

        # pick focus 
        self.focus = self._find_nonempty_parent(self.focus)

        # pick selection
        new_selection = self._find_nonempty_parent([to_exclude[0].parent])

        # pick pinned 
        self.pinned = self._find_nonempty_parent(self.pinned)

        self.rebuild_views()

        if new_selection is not None:
            for (i, v) in enumerate(self.frame_views):
                if v.matches(new_selection):
                    self.selection = i
                    break

        self.render()
        

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
                self.clear_focus()
                continue
            if c == ord('R'):
                self.build()
                continue
            if c == ord('f'):
                self.set_focus()
                continue
            if c == ord('p'):
                self.set_pin()
                continue
            if c == ord('x'):
                self.exclude_frame()
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
                        self.set_focus()
                        continue
            if c == curses.KEY_RESIZE:
                self.rebuild_views()
                self.render()

def main(stdscr):
    h = FlameCLI(stdscr)
    h.loop()

wrapper(main)
