from filescleaner import FileListOrdered, FileStat


class MockStat(object):
    creation_counter = 0

    st_mode = 16877
    st_ino = 1969397
    st_dev = 64768L
    st_nlink = 7
    st_uid = 1000
    st_gid = 1000
    st_size = 1024
    st_atime = 1427599568
    st_mtime = 1427639517
    st_ctime = 1000000000

    def __init__(self):
        self.st_ctime = self.__class__.st_ctime + self.creation_counter
        self.st_size = self.__class__.st_size + (self.creation_counter ** 2)
        self.__class__.creation_counter += 1

    def __str__(self):
        return '<MockStat: st_ctime: %s, st_size: %s>' % (self.st_ctime, self.st_size)

    __repr__ = __str__


moc_list = [FileStat('hello%s' % i, MockStat()) for i in range(0, 5)]

fl = FileListOrdered(moc_list)
