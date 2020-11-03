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


## Output description


## Interactive commands
* q - quit
* LEFT/h - select the block to the left
* RIGHT/l - select the block to the right
* UP/k - select the block above
* DOWN/j - select the block below
* f - focus. Zoom into selected frame to see it's children
* r - reset focus. 
* single mouse click - select the block
* double mouse click - zoom into the block


## Known limitations
* No support for deep stack traces which do not fit on the screen
* Requires python
* No search functionality
* No color-coding for kernel/userspace/vm parts of stack traces
