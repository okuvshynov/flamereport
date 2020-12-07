import curses
import os
import sys
from itertools import groupby, tee, chain
from operator import attrgetter, itemgetter
from random import randint
####
# next things to do:

# -- support ? and show help window
# -- shortcuts like 'perf report'
# -- navigation within selection ('n' - next selection)
#   -- how is it supposed to work for multiselect views?
#   -- do we have 'selection' on frame level or view level?
#   -- selection can refer to both:
#    -- single frame, single view
#    -- single frame, part of view
#    -- multiple frame
# -- shall invert keep selection? exclusion? focus?
# -- handle long frame titles better
#   -- make sure % are visible
#   -- 

# color initialization
class Colors256:
    color_count = 0

    @staticmethod
    def init():
        colors = [214, 208, 202, 196, 166, 172]
        selection_color = 46
        selection_match_color = 156
        for (i, c) in enumerate(colors):
            curses.init_pair(i + 1, curses.COLOR_BLACK, c)
        Colors256.color_count = len(colors)
        curses.init_pair(Colors256.color_count + 1, curses.COLOR_BLACK, selection_color)
        curses.init_pair(Colors256.color_count + 2, curses.COLOR_BLACK, selection_match_color)

    @staticmethod
    def pick_color():
        return randint(1, Colors256.color_count) if Colors256.color_count > 1 else 0

    @staticmethod
    def selection_color():
        return Colors256.color_count + 1

    @staticmethod
    def highlight_color():
        return Colors256.color_count + 2

# Frame represents stack frame itself, not its representation on the scren
class Frame:
    def __init__(self, title, samples, children):
        self.title = title
        self.samples = samples
        self.children = children
        self.parent = None

    # returns number of samples which belong to frame (or its children)
    # which match the title (strict equality).
    # to avoid counting same samples twice, we do not go deeper if parent
    # already matches
    def samples_with_title(self, title):
        if self.title == title:
            return self.samples
        return sum([f.samples_with_title(title) for f in self.children])

    def search_with_title(self, title):
        if title in self.title:
            return self.samples
        return sum([f.search_with_title(title) for f in self.children])

    # returns all topline frames matching title
    # once we encounter a match we do not go deeper
    def all_by_title(self, title):
        if self.title == title:
            return [self]
        return list(chain.from_iterable([f.all_by_title(title) for f in self.children]))

# representation of a frame on a screen, with specific location/size
class FrameView(object):
    def __init__(self, x, y, w, frames, truncated = False):
        self.x = x
        self.y = y
        self.w = w
        # truncated indicates that this frame might have children which are
        # hidden due to small size on the screen
        self.truncated = truncated
        # sort by samples desc.
        self.frames = sorted(frames, key=lambda f: - f.samples)
        self.samples = sum([f.samples for f in frames])
        self.color = Colors256.pick_color()

    def draw(self, scr, selected, highlight):
        style = curses.color_pair(self.color)
        if selected:
            style = curses.color_pair(Colors256.selection_color())
        elif highlight:
            style = curses.color_pair(Colors256.highlight_color())
        scr.addstr(self.y, self.x, self.txt, style)

    def frameset(self):
        return self.frames

    def frame_count(self):
        return len(self.frames)

    def matches_title(self, title):
        if self.truncated:
            return sum([f.samples_with_title(title) for f in self.frames]) > 0
        return any(f.title == title for f in self.frames)

    def search_title(self, title):
        if self.truncated:
            return sum([f.search_with_title(title) for f in self.frames]) > 0
        return any(title in f.title for f in self.frames)

# compressed multiframe view for presenting multiple frames in a single cell
# we need that in TUI version as some stacks would be < 1 character otherwise
class MultiFrameView(FrameView):
    def __init__(self, x, y, w, frames):
        assert(w > 0)
        super(MultiFrameView, self).__init__(x, y, w, frames, truncated=True)
        self.txt = "+" if w == 1 else "[{}]".format("+" * (w - 2))

    # render summary of the multiframe
    # multiselect samples is ignored for now
    def status(self, total, height, multiselect_samples = None):
        assert(self.frame_count() > 1)
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
class SingleFrameView(FrameView):
    def __init__(self, x, y, w, frame, truncated=False):
        super(SingleFrameView, self).__init__(x, y, w, [frame], truncated)
        if w == 1:
            self.txt = '-'
        else:
            w = w - 2
            self.txt = '[{}]'.format(frame.title[:w].ljust(w, '-'))

    # single frame view might get multiselection
    # status here is also 'immutable' until we rebuild the views
    # except, we don't know the height until we prepare all the views
    def status(self, total, height, multiselect_samples = None):
        assert(len(self.frames) == 1)
        if height < 1:
            return []

        frame = self.frames[0]
        s = frame.samples
        ms = multiselect_samples
        if multiselect_samples is None or ms == s:
            return ["{} ({} samples, {:.2f}%)".format(frame.title, s, 100.0 * s / total)]
        return ["{} ({} samples, {:.2f}% | {} samples, {:.2f}% in selection)".format(frame.title, s, 100.0 * s / total, ms, 100.0 * ms / total)]

    def matches(self, frames):
        return self.frames[0] in frames

def view_contains(view, x, y):
    return view.y == y and x >= view.x and x < view.x + view.w

# reading stacks from stdin
def read_stdin():
    # to read both piped stdin and use tty in curses
    os.dup2(0, 4)
    os.close(0)
    sys.stdin = open('/dev/tty', 'r')

    data = []
    with os.fdopen(4, 'r') as stdin_piped:
        for l in stdin_piped.readlines():
            (stacks, _, cnt) = l.strip().rpartition(' ')
            data.append((stacks.split(';'), int(cnt))) 

    return data

# set of all frames.
class FrameSet:
    def __init__(self, data, inverted=False):
        # this is a list of top-level frames
        if inverted:
            data = [(list(reversed(f)), s) for (f, s) in data]
        self.frames = self._build_frames(data)
        self.total_samples = sum([a for (_, a) in data])
        self.total_excluded = 0

    def samples_with_title(self, title):
        return sum([f.samples_with_title(title) for f in self.frames])

    # is used to remove empty parents
    def _exclude_frame(self, frame):
        if frame in self.frames:
            self.frames.remove(frame)
        if frame.parent != None:
            frame.parent.children.remove(frame)

    def exclude_frames(self, frames):
        for frame in frames:
            self._exclude_frame(frame)
            samples = frame.samples
            frame.samples = 0
            self.total_excluded += samples
            self.total_samples -= samples
            while frame.parent != None:
                frame.parent.samples -= samples
                if frame.parent.samples == 0:
                    assert(len(frame.parent.children) == 0)
                    # remove the parent as well
                    self._exclude_frame(frame.parent)
                frame = frame.parent

    def _merge_frames(self, frames):
        title = lambda f: f.title
        res = []
        for k, g in groupby(sorted(frames, key=title), title):
            # TODO no need to make a list here
            to_merge = list(g)
            all_children = list(chain.from_iterable([ff.children for ff in to_merge]))
            f = Frame(k, sum([ff.samples for ff in to_merge]), self._merge_frames(all_children))
            for ff in f.children:
                ff.parent = f
            res.append(f)
        return res

    # pick all frames by title (e.g. malloc) and show all their children
    # pin them to the top regardless of where are they in the original 
    # frame set. useful to see 'who calls function X'
    def hard_focus(self, title):
        frames = list(chain.from_iterable([f.all_by_title(title) for f in self.frames]))
        # we have single root, and need to merge children
        roots = self._merge_frames(frames)
        assert(len(roots) == 1)
        self.frames = roots
        self.total_excluded += (self.total_samples - roots[0].samples)
        self.total_samples = roots[0].samples

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
        assert isinstance(s, int)
        res = []
        # these are 'small' frames
        leftovers = []
        for f in frames:
            w = int(width * f.samples / s)
            assert isinstance(w, int)
            if w < 4:
                leftovers.append(f)
                continue
            res.append(SingleFrameView(x, y, w, f))
            res += self._get_views_rec(f.children, w, f.samples, x, y + 1)
            x = x + w
        
        # for now just append as a single frame view
        # this will work bad if ALL frames are small in current view
        # in this case, we'll never be able to dive into it
        # maybe a better way would be to split into several 'multiframes'
        if leftovers:
            samples = sum([f.samples for f in leftovers])
            w = max(1, int(width * samples / s))
            if len(leftovers) > 1:
                res.append(MultiFrameView(x, y, w, leftovers))
            else:
                res.append(SingleFrameView(x, y, w, leftovers[0], truncated=True))
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

        res = [SingleFrameView(0, i, width, f) for (i, f) in enumerate(root_path)]
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
        Colors256.init()
        self.data = read_stdin()
        self.build()
        self.render()

    def selected_view(self):
        return self.frame_views[self.highlight[self.selection]]

    def selected_frames(self):
        if self.highlight:
            return self.frame_views[self.highlight[self.selection]].frameset()
        return None

    # rebuilding all views, while keeping selected frames selected
    def rebuild_views(self, selected_frames = None):
        if not self.frames:
            return
        if selected_frames is None:
            selected_frames = self.selected_frames()
        self.highlight = []
        self.frame_views = self.frames.get_frame_views(self.stdscr.getmaxyx()[1], self.focus, self.pinned)
        self.fit_into_vertical_space()
        selection = 0
        for (i, view) in enumerate(self.frame_views):
            if view.matches(selected_frames):
                selection = i
                break
        self.build_screen_index()
        self.do_highlight(selection)

    def clear_focus(self):
        self.focus = None
        self.pinned = None
        self.rebuild_views()
        self.render()

    def build(self, inverted=False):
        # list of frames on the same level with the same parent
        # this list would be expanded to 100% width, their parents would too
        self.focus = None
        # list of frames on the same level with the same parent whom we consider
        # new 'root level'
        self.pinned = None

        self.inverted = inverted

        self.frames = FrameSet(self.data, inverted=inverted)
        self.frame_views = self.frames.get_frame_views(self.stdscr.getmaxyx()[1])        
        self.fit_into_vertical_space()
        self.build_screen_index()
        self.do_highlight(0)
        self.render()

    def set_focus(self):
        self.focus = self.selected_frames()
        self.rebuild_views()
        self.render()

    def set_pin(self):
        self.focus = self.selected_frames()
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
            view = self.selected_view()
            status = view.status(samples + excluded, self.status_height, self.multiselect_samples)
        warning = None
        w = []
        if excluded > 0:
            pe = 100.0 * excluded / (samples + excluded)
            w.append("{:.2f}% samples excluded".format(pe))
        if self.inverted:
            w.append("Inverted") 
        warning = "|".join(w)
        self.status_area.draw(status, warning)

    def render(self): 
        self.stdscr.clear()
        for (i, _) in enumerate(self.frame_views):
            is_selected = (i == self.highlight[self.selection])
            is_highlighted = (i in self.highlight)
            self.frame_views[i].draw(self.stdscr, is_selected, is_highlighted)
        self.print_status_bar()

    # selects a frame view.
    # Automatically deselects and dehighlights old selection
    def change_selection(self, s):
        if s is None:
            return False
        for i in self.highlight:
            self.frame_views[i].draw(self.stdscr, False, False)
        self.do_highlight(s)
        self.print_status_bar()
        return True

    def move_selection(self, d):
        if not self.frame_views:
            return
        m = len(self.frame_views)
        selection = self.highlight[self.selection]
        if self.change_selection(((selection + d) % m + m) % m):
            self.stdscr.refresh()

    def select_up(self):
        if self.change_selection(self.selected_view().parent_index):
            self.stdscr.refresh()

    def select_down(self):
        if self.change_selection(self.selected_view().first_child_index):
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
        if not self.frame_views:
            return
        to_exclude = self.selected_frames()
        self.frames.exclude_frames(to_exclude)
        
        # everything is removed
        if self.frames.total_samples == 0:
            self.selection = 0
            self.highlight = []
            self.focus = None
            self.pinned = None
            self.rebuild_views()
            self.render()
            return

        # pick focus 
        self.focus = self._find_nonempty_parent(self.focus)

        # pick selection
        selected_frames = self._find_nonempty_parent([to_exclude[0].parent])

        # pick pinned 
        self.pinned = self._find_nonempty_parent(self.pinned)

        self.rebuild_views(selected_frames)

        self.render()

    def do_highlight(self, selection):
        self.highlight = []
        if len(self.frame_views) == 0:
            return
        view = self.frame_views[selection]
        frames = view.frameset()
        if len(frames) != 1:
            self.highlight = [selection]
            self.selection = 0
            self.multiselect_samples = None
        else:
            title = frames[0].title
            for (i, v) in enumerate(self.frame_views):
                if v.matches_title(title):
                    self.highlight.append(i)
                if v == view:
                    self.selection = len(self.highlight) - 1
            self.multiselect_samples = self.frames.samples_with_title(title)
        self.render()

    # '/'
    def search(self):
        rows, cols = self.stdscr.getmaxyx()
        for i in range(self.status_area.old_lines):
            self.stdscr.addstr(rows - 1 - i, 0, " " * (cols - 1))
        self.stdscr.addstr(rows - 1, 0, "/")
        curses.echo()
        term = self.stdscr.getstr(rows - 1, 1)
        curses.noecho()

        # search looks for partial match? 
        # TODO: what if the 'matched' part is part of multiselect?
        # we can make 'highlight by name'
        # do we search within visible part only?
        for (i, v) in enumerate(self.frame_views):
            if v.search_title(term):
                if self.change_selection(i):
                    self.stdscr.refresh()
                break

    # 'F'
    def hard_focus(self):
        if not self.frame_views:
            return
        frames = self.selected_frames()
        if len(frames) != 1:
            return
        self.frames.hard_focus(frames[0].title)
        self.focus = None
        self.pin = None
        self.rebuild_views(frames)
        self.render()

    # 'n'
    def next_highlight(self):
        if self.highlight:
            self.selection = (self.selection + 1) % len(self.highlight)
            self.render()

    # 'N'
    def prev_highlight(self):
        if self.highlight:
            n = len(self.highlight)
            self.selection = (self.selection + n - 1) % n
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
            if c == ord('F'):
                self.hard_focus()
                continue
            if c == ord('p'):
                self.set_pin()
                continue
            if c == ord('x'):
                self.exclude_frame()
                continue
            if c == ord('I'):
                self.build(inverted=not self.inverted)
                continue
            if c == ord('q'):
                break
            if c == ord('/'):
                self.search()
                continue
            if c == ord('n'):
                self.next_highlight()
                continue
            if c == ord('N'):
                self.prev_highlight()
                continue
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

curses.wrapper(main)
