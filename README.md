# Flame Graphs for terminal

<Image Here>

This is an implementation of [flame graphs](http://www.brendangregg.com/flamegraphs.html) that shows the chart right in the terminal.

While not as powerful as GUI tools, it's more convenient in case of iterative process, like:
1. run a profile on remote machine
2. visualize and study the profile
3. make changes in code/workload/app-level settings/profiling settings/...
4. repeat

## Flame graphs overview
Please check http://www.brendangregg.com/flamegraphs.html

## Usage example

1. Get https://github.com/brendangregg/FlameGraph

```$ git clone https://github.com/brendangregg/FlameGraph.git```

2. Get https://github.com/okuvshynov/flametui

```$ git clone https://github.com/okuvshynov/flametui.git```

3. Do system-wide profile:

```$ perf record -F 999 -a -g -- sleep 10```

4. Collapse stacks:

```$ perf script | ./FlameGraph/stackcollapse-perf.pl > stacks```

5. View visualization

```$ python ./flametui/fcl.py < ./stacks```

## Output description
Note - this is likely to change.

Graph element examples
* '[foo---]' -- individual frame
* '+' - aggregated frame view which contains several (likely small) frames. Selecting it will show the details in the status area. Might have hidden descendents. 
* '-' - terminal

## Interactive commands
* LEFT/h - select the block to the left
* RIGHT/l - select the block to the right
* UP/k - select the block above
* DOWN/j - select the block below
* f - focus. Zoom into selected frame; makes it fit whole width, hides the siblings.
* p - pin to top. This zooms into selected frame (like 'f') and hides parent frames. This is useful for deep traces, as we don't have scrolling functionality.
* x - eXclude frame. Removes the selected frame, its children and shrinks parents accordingly. This is useful when the graph is dominated by few large but not particularly interesting frames; It provides a more convenient view compared to focusing on 'smaller but more interesting' frames individually.
* r - reset focus. Resets the focus but keeps exculded frames excluded.
* R - hard reset, brings everything to default view; Useful after exclusions.
* single mouse click - select the block
* double mouse click - zoom into the block. Equivalent to 'f'
* q - quit


## Known limitations
* No search functionality
* No color-coding for kernel/userspace/vm parts of stack traces
