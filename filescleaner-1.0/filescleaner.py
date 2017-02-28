#!/usr/bin/python


import argparse
import copy
import json
import os
import errno
import signal

import math
import logging
import sys
import time
from logging import handlers
from functools import total_ordering


PY3 = sys.version_info[0] == 3
PY2 = sys.version_info[0] == 2

DEFAULT_CONFIG_PATH = '/etc/filescleaner.json'
DEFAULT_PIDFILE_PATH = './run/filescleaner.pid'

DEBUG = False
VERBOSITY = 2
DEFAULT_LOG_FILE = '/var/log/filescleaner/cleaner.log'

logger = logging.getLogger('filescleaner')

UNITS = ['b', 'k', 'm', 'g']

##################################################################################################
#   VARIABLE SETTINGS

# unit to used in logs
UNIT = 'b'

# size with unit suffix as str, default bytes
# eg. '8G', '8192M'
# maximum size to allow on disk, lower bound
DEFAULT_MAX_SIZE = '100G'
# size of disk, this is when the disk would be full. upper limit
DEFAULT_DISK_SIZE = '200G'

# time in seconds to sleep between checks
SLEEP = 1800


def setup_logging(args):
    fmt = logging.Formatter('[%(asctime)s %(levelname)s - %(process)d] %(message)s')
    if args.interactive:
        handler = logging.StreamHandler(sys.stderr)
    else:
        handler = logging.handlers.RotatingFileHandler(
            args.logfile, mode='a', maxBytes=10240000,
            backupCount=0, encoding=None, delay=0
        )
    handler.setFormatter(fmt)
    logger.addHandler(handler)

    logger.setLevel(logging.INFO)


def exit_error(msg, code=255):
    logger.error(msg)
    sys.stderr.write(msg + '\n')
    exit(code)


def get_directories(settings):
    """Return a list of `Directory`s"""
    directories = []

    try:
        if not isinstance(settings.DIRECTORIES, dict):
            exit_error('config_error: DIRECTORIES should be a dict.')
    except AttributeError:
        msg = 'config_error: Missing "DIRECTORIES" config setting'
        exit_error(msg)

    for path, config in settings.DIRECTORIES.items():
        if not isinstance(config, dict):
            exit_error('config_error: Value for "%s" within DIRECTORIES should be a dict.' % (path,))

        try:
            directory = Directory(path, config)
        except TypeError as e:
            exit_error('config_error: Invalid config keys for directory "%s". [%r]' % (path, e.args))
        else:
            directories.append(directory)

    if not directories:
        logger.warning('config_error: empty DIRECTORIES config.')

    return directories


def set_default_values(config):
    for key in ('SLEEP', 'DEFAULT_MAX_SIZE', 'DEFAULT_DISK_SIZE'):
        try:
            globals()[key] = config[key]
        except KeyError:
            pass


class Directory(object):
    def __init__(self, path, config):
        path = os.path.abspath(path)
        if path == '/':
            exit_error('You added the root directory to be cleaned! ARE YOU WHACKED?!')

        self.path = path

        self.max_size = config.pop('max_size', DEFAULT_MAX_SIZE)
        self.disk_size = config.pop('disk_size', DEFAULT_DISK_SIZE)

        if config:
            raise TypeError(config.keys())

        self.max_size_unit, self.max_bytes = unit_byte_size_tuple(self.max_size)
        self.disk_size_unit, self.disk_bytes = unit_byte_size_tuple(self.disk_size)

        self.usage_calculated_at = 0

    def _calculate_disk_usage(self):
        self.usage_calculated_at = time.time()
        self.fl = get_dir_size(self.path)
        logger.info('Disk space used: "%s" %s%s', self.path,
                    bytes2unit(self.fl.total_size, self.max_size_unit),
                    self.max_size_unit)

    def run_cleanup(self):
        """Returns a boolean value whether deletetion was run"""
        if self.fl.total_size > self.max_bytes:
            logger.info('Disk space used: "%s" %s%s exceeds MAX_SIZE', self.path,
                        bytes2unit(self.fl.total_size, self.max_size_unit),
                        self.max_size_unit)
            self.fl.delete_files_to_max_size(self.max_bytes)
            return True
        return False

    def check_disk_usage(self):
        """Returns True if we should not sleep before next run"""
        if time.time() >= self.usage_calculated_at + SLEEP:
            self._calculate_disk_usage()

            if self.fl.total_size > (self.max_bytes + ((self.disk_bytes - self.max_bytes) / 2)):
                logger.warning('"%s": Consider a lower bound for MAX_SIZE. '
                               'Disk usage increased by more than [%d] '
                               'since last check', self.path,
                               (self.disk_bytes - self.max_bytes) / 2)
                return True

        return False


###################################################################################################
if DEBUG:
    SLEEP = 45
    DEFAULT_MAX_SIZE = '1G'
    DEFAULT_DISK_SIZE = '3G'


def get_unit(m):
    return UNITS.index(m.lower())


def bytes2unit(b, u=None):
    """

    :param b: size in bytes
    :param u: index to UNITS[unit] to convert to
    :return: converted to unit
    """
    u = u or UNIT
    if isinstance(u, str):
        u = get_unit(u)
    return b / math.pow(1024, u)


def unit2bytes(n, u=None):
    """
    :param n: number in U units
    :param u: index to UNITS[unit] to convert from
    :return: converted to bytes
    """
    u = u or UNIT
    if isinstance(u, str):
        u = get_unit(u)
    return n * math.pow(1024, u)


def unit_byte_size_tuple(s):
    s = str(s)
    if s[-1].lower() in UNITS:
        unit = s[-1]
        s = s[:-1]
    else:
        unit = 'b'
    n = float(s)
    value_in_bytes = unit2bytes(n, unit)
    return unit, value_in_bytes


@total_ordering
class FileStat(object):
    def __init__(self, path, stat):
        self.path = path
        self.stat = stat

    def __lt__(self, other):
        return self.stat.st_ctime < other.stat.st_ctime

    def __eq__(self, other):
        return self.stat.st_ctime == other.stat.st_ctime

    def __repr__(self):
        return '%s: s:%s, t:%s' % (self.path, self.stat.st_size, self.stat.st_ctime)


class FileListOrdered(object):
    """
        maintains a list of files in the order they've been created
    """

    def __init__(self, file_stats):
        """

        :type file_stats: list::FileStat
        :return:
        """
        self.d = {f.path: f for f in file_stats}
        self.l = sorted(file_stats)
        self.total_size = sum(f.stat.st_size for f in self.l)

    def __setitem__(self, k, v):
        if k in self.d:
            old_v = self.d[k]
            if old_v.stat.st_ctime != v.stat.st_ctime:
                old_idx = self.l_idx(old_v)
                del self.l[old_idx]
        idx = self.idx(v)
        self.l.insert(idx, v)
        self.d[k] = v

    def __getitem__(self, k):
        return self.d[k]

    def __delitem__(self, k):
        idx = self.l_idx(self.d[k])
        del self.l[idx]
        del self.d[k]

    def __iter__(self):
        return iter(self.l)

    def __contains__(self, k):
        return k in self.d

    def iteritems(self):
        for k in self:
            yield (k.path, k)

    def l_idx(self, x):
        idx = self.idx(x)
        while idx < len(self.l):
            if self.l[idx].path == x.path:
                return idx
            idx += 1
        raise KeyError(x)

    def idx(self, x):
        lo, hi = 0, len(self.l)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.l[mid] < x:
                lo = mid + 1
            else:
                hi = mid
        return lo

    def delete_files_to_max_size(self, max_size):
        """
        delete files in fifo order until
        :return:
        """
        idx, s = 0, 0
        while self.total_size - s >= max_size:
            s += self.l[idx].stat.st_size
            idx += 1

        delete_errors = []
        bytes_freed = 0
        for i in range(0, idx):
            st = self.l[i]
            try:
                os.remove(st.path)
                if VERBOSITY > 1:
                    logger.info('deleted "%s"' % st.path)
            except OSError as e:
                if e.errno != errno.ENOENT:
                    logger.error(e)
                    delete_errors.append(st)
                else:
                    logger.warning('file: %s already deleted...', st.path)
                    bytes_freed += st.stat.st_size
            else:
                bytes_freed += st.stat.st_size

        self.total_size -= bytes_freed
        if bytes_freed < s / 2:
            logger.critical('more than 1/2 of %s bytes of disk space could not be freed. the disk will be filling up.')


def get_dir_size(path):
    dir_cache = []
    total_size = 0
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        # if dirpath != current_dir and dirpath in dirs:
        #     continue
        for f in filenames:
            fp = os.path.join(dirpath, f)
            try:
                stat = os.stat(fp)
            except OSError:
                pass
            else:
                dir_cache.append(FileStat(fp, stat))
    sorted_fl = FileListOrdered(dir_cache)
    # if DEBUG:
    #     for f, st in sorted_fl.iteritems():
    #         print('%s:\t\t\t%s' % (path, st.stat.st_ctime))
    #         total_size += st.stat.st_size
    #     print('Disk space used: %s%s' % (bytes2unit(total_size), UNIT))
    return sorted_fl


class Settings(object):
    _config_keys = ('START_DAEMON', 'MAX_SIZE', 'DISK_SIZE', 'DIRECTORIES', 'SLEEP')

    def __init__(self, path, config):
        self.path = path
        self._orig_settings = config
        self.config = copy.deepcopy(config)

        set_default_values(config)

    @classmethod
    def load_settings(cls, path):
        with open(path) as f:
            settings = json.load(f)

        return cls(path, settings)

    def dump_settings(self):
        config = {k: v for k, v in self.config.items() if k in self._config_keys}
        with open(self.path, 'w') as f:
            json.dump(config, f, sort_keys=True, indent=4, separators=(',', ': '))

    def __getattr__(self, item):
        if item in self._config_keys:
            try:
                return self.config[item]
            except KeyError:
                pass
        raise AttributeError(item)

    def __setattr__(self, key, value):
        if key in self._config_keys:
            self.config[key] = value
        else:
            super(Settings, self).__setattr__(key, value)


def add_directory_func(settings, args):
    path = os.path.abspath(args.path)
    config = settings.DIRECTORIES[path] = {}
    if args.max_size:
        config['max_size'] = args.max_size
    if args.disk_size:
        config['disk_size'] = args.disk_size

    logger.info('Adding directory "%s": %s', path, config)
    # Just to validate the settings
    # We must pass a copy
    Directory(path, config.copy())

    settings.dump_settings()


def remove_directory_func(settings, args):
    path = os.path.abspath(args.path)
    logger.info('Removing directory "%s"', path)
    if path not in settings.DIRECTORIES:
        exit_error('"%s" was not found in the settings.' % (path,))
    del settings.DIRECTORIES[path]
    settings.dump_settings()


def enable_daemon_func(settings, args):
    logger.info('Enabling daemon.')
    settings.START_DAEMON = True
    settings.dump_settings()


def disable_daemon_func(settings, args):
    logger.info('Disabling daemon.')
    settings.START_DAEMON = False
    settings.dump_settings()


def monitor_func(settings, args):
    if not getattr(settings, 'START_DAEMON', True):
        logger.info('START_DAEMON is False. Exiting...')
        exit(0)

    directories = get_directories(settings)
    if not directories:
        logger.info('No directories to monitor. Exiting...')
        exit(0)

    if not args.interactive:
        try:
            pid = os.fork()
            if pid > 0:
                try:
                    with open(args.pidfile, 'w') as f:
                        f.write(str(pid))
                except Exception:
                    logger.exception('Failed to write to PIDFILE %s', args.pidfile)
                    os.kill(pid, signal.SIGTERM)
                    os.waitpid(pid, 0)
                    sys.exit(3)

                sys.exit(0)

        except OSError as e:
            exit_error("Failed to fork: %d (%s)" % (e.errno, e.strerror), code=1)

    while True:
        should_sleep = True
        start = time.time()
        logger.info("Checking directories sizes.")
        for directory in directories:
            if directory.check_disk_usage():
                pass  # TODO we should check wheather we should sleep a bit?

        for directory in directories:
            if directory.run_cleanup():
                pass  # TODO we should check whether we need to sleep a bit?

        for directory in directories:
            if directory.check_disk_usage():
                should_sleep = False
                pass  # TODO we should check wheather we should sleep a bit?

        end = time.time()
        logger.info("End run.")
        elapsed = end - start
        if should_sleep:
            if elapsed > SLEEP:
                logger.error("Took too long to run a cycle.")
            sleep_sec = SLEEP - elapsed
            logger.info("Sleeping for %d seconds", sleep_sec)
            time.sleep(sleep_sec)


def main():
    import sys
    if DEBUG:
        print(sys.argv)

    parser = argparse.ArgumentParser()

    parser.add_argument('-c', '--config', metavar='CONFIG-FILE', default=DEFAULT_CONFIG_PATH,
                        help='Config file path')
    parser.add_argument('-i', '--interactive', help='Log output to stdout rather than to a file.', action='store_true')
    parser.add_argument('-l', '--logfile', help='Log file path', default=DEFAULT_LOG_FILE)

    subparsers = parser.add_subparsers(title='Options', dest='subcommand')

    monitor = subparsers.add_parser('monitor', help='Run monitoring daemon.')
    monitor.set_defaults(func=monitor_func)
    monitor.add_argument('--pidfile', help='Save pid to pid file specified.', default=DEFAULT_PIDFILE_PATH)

    add_directory = subparsers.add_parser('add', help='Add directory to be monitored.')
    add_directory.set_defaults(func=add_directory_func)
    add_directory.add_argument('path')
    add_directory.add_argument('-m', '--max-size', dest='max_size', help='Max size to allow for the given directory.')
    add_directory.add_argument('-d', '--disk-size', dest='disk_size', help='Disk size.')

    remove_directory = subparsers.add_parser('remove', help='Remove directory from being monitored.')
    remove_directory.set_defaults(func=remove_directory_func)
    remove_directory.add_argument('path')

    enable_disable = subparsers.add_parser('enable', help='Enable daemon.')
    enable_disable.set_defaults(func=enable_daemon_func)
    disable_daemon = subparsers.add_parser('disable', help='Disable daemon.')
    disable_daemon.set_defaults(func=disable_daemon_func)

    args = parser.parse_args()

    setup_logging(args)

    config_path = args.config
    if not os.path.exists(config_path):
        exit_error('Config file "%s" does not exist.' % (config_path,))
    logger.info('CONFIG File path = %s', config_path)
    settings = Settings.load_settings(config_path)

    if args.subcommand:
        args.func(settings, args)


if __name__ == '__main__':
    main()
