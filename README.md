# filescleaner
Monitor directories and clean files when exceeding configured size.

```
usage: filescleaner [-h] [-c CONFIG-FILE] [-i] [-l LOGFILE]
                    {monitor,add,remove,enable,disable} ...

optional arguments:
  -h, --help            show this help message and exit
  -c CONFIG-FILE, --config CONFIG-FILE
                        Config file path
  -i, --interactive     Log output to stdout rather than to a file.
  -l LOGFILE, --logfile LOGFILE
                        Log file path

Options:
  {monitor,add,remove,enable,disable}
    monitor             Run monitoring daemon.
    add                 Add directory to be monitored.
    remove              Remove directory from being monitored.
    enable              Enable daemon.
    disable             Disable daemon.
```
