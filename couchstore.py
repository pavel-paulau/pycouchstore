import ctypes
import errno
import sys
import traceback


for lib in ('libcouchstore.so',      # Linux
            'libcouchstore.dylib',   # Mac OS
            'couchstore.dll',        # Windows
            'libcouchstore-1.dll'):  # Windows (pre-CMake)
    try:
        _lib = ctypes.CDLL(lib)
        break
    except OSError, err:
        continue
else:
    traceback.print_exc()
    sys.exit(1)


_lib.couchstore_strerror.restype = ctypes.c_char_p


class CouchStoreException(Exception):
    """Exceptions raised by CouchStore APIs."""

    def __init__(self, errcode):
        Exception.__init__(self, _lib.couchstore_strerror(errcode))
        self.code = errcode


### INTERNAL FUNCTIONS:

def _check(err):
    if err == 0:
        return
    elif err == -3:
        raise MemoryError()
    elif err == -5:
        raise KeyError()
    elif err == -11:
        raise OSError(errno.ENOENT)
    else:
        raise CouchStoreException(err)


def _to_string(key):
    if not isinstance(key, basestring):
        raise TypeError(key)
    return str(key)


### INTERNAL STRUCTS:

class SizedBuf(ctypes.Structure):

    _fields_ = [
        ("buf", ctypes.POINTER(ctypes.c_char)),
        ("size", ctypes.c_size_t)
    ]

    def __init__(self, string):
        if string is not None:
            string = _to_string(string)
            length = len(string)
            buf = ctypes.create_string_buffer(string, length)
            ctypes.Structure.__init__(self, buf, length)
        else:
            ctypes.Structure.__init__(self, None, 0)

    def __str__(self):
        return ctypes.string_at(self.buf, self.size)


class DocStruct(ctypes.Structure):

    _fields_ = [
        ("id", SizedBuf),
        ("data", SizedBuf)
    ]


class DocInfoStruct(ctypes.Structure):

    _fields_ = [
        ("id", SizedBuf),
        ("db_seq", ctypes.c_ulonglong),
        ("rev_seq", ctypes.c_ulonglong),
        ("rev_meta", SizedBuf),
        ("deleted", ctypes.c_int),
        ("content_meta", ctypes.c_ubyte),
        ("bp", ctypes.c_ulonglong),
        ("size", ctypes.c_size_t)
    ]


class LocalDocStruct(ctypes.Structure):

    _fields_ = [
        ("id", SizedBuf),
        ("json", SizedBuf),
        ("deleted", ctypes.c_int)
    ]


class DbInfoStruct(ctypes.Structure):

    _fields_ = [
        ("filename", ctypes.c_char_p),
        ("last_sequence", ctypes.c_ulonglong),
        ("doc_count", ctypes.c_ulonglong),
        ("deleted_count", ctypes.c_ulonglong),
        ("space_used", ctypes.c_ulonglong),
        ("header_position", ctypes.c_ulonglong)
    ]


class CounterStruct(ctypes.Structure):

    _fields_ = [("count", ctypes.c_ulonglong)]


class DocumentInfo(object):
    """Metadata of a document in a CouchStore database."""

    # Values for contentType:
    IS_JSON = 0
    INVALID_JSON = 1
    INVALID_JSON_KEY = 2
    NON_JSON = 3

    def __init__(self, _id):
        self.id = _id
        self.deleted = False
        self.content_type = DocumentInfo.NON_JSON
        self.rev_sequence = 0

    @staticmethod
    def from_struct(info, store=None):
        self = DocumentInfo(str(info.id))
        self.store = store
        self.sequence = info.db_seq
        self.rev_sequence = info.rev_seq
        self.rev_meta = str(info.rev_meta)
        self.deleted = (info.deleted != 0)
        self.content_type = info.content_meta & 0x0F
        self.compressed = (info.content_meta & 0x80) != 0
        self._bp = info.bp
        self.phys_size = info.size
        return self

    def as_struct(self):
        struct = DocInfoStruct(SizedBuf(self.id))
        if hasattr(self, "sequence"):
            struct.db_seq = self.sequence
        if hasattr(self, "rev_meta"):
            struct.rev_meta = SizedBuf(self.rev_meta)
        struct.rev_seq = self.rev_sequence
        struct.deleted = self.deleted
        struct.content_meta = self.content_type & 0x0F
        if hasattr(self, "compressed") and self.compressed:
            struct.content_meta |= 0x80
        if hasattr(self, "_bp"):
            struct.bp = self._bp
        if hasattr(self, "phys_size"):
            struct.size = self.phys_size
        return struct

    def __str__(self):
        return "DocumentInfo('%s', %d bytes)" % (self.id, self.phys_size)

    def __repr__(self):
        return "DocumentInfo('%s', %d bytes)" % (self.id, self.phys_size)

    def dump(self):
        return "DocumentInfo('%s', %d bytes, seq=%d, revSeq=%d, deleted=%s, " \
               "contentType=%d, compressed=%d, bp=%d)" % \
            (self.id, self.phys_size, self.sequence, self.rev_sequence,
             self.deleted, self.content_type, self.compressed, self._bp)

    def get_contents(self, options=0):
        """Fetches and returns the contents of a DocumentInfo returned from
        CouchStore's get_doc_info_by_id or get_doc_info_by_sequence methods."""
        if not hasattr(self, "store") or not hasattr(self, "_bp"):
            raise Exception("Contents unknown")
        info = self.as_struct()
        docptr = ctypes.pointer(DocStruct())
        _lib.couchstore_open_doc_with_docinfo(self.store,
                                              ctypes.byref(info),
                                              ctypes.byref(docptr),
                                              ctypes.c_uint64(options))
        contents = str(docptr.contents.data)
        _lib.couchstore_free_document(docptr)
        return contents


class LocalDocs(object):
    """Collection that represents the local documents of a CouchStore."""

    def __init__(self, couchstore):
        self.couchstore = couchstore

    def __getitem__(self, key):
        """Returns the contents of a local document (as a string) given its ID.
        """
        _id = _to_string(key)
        docptr = ctypes.pointer(LocalDocStruct())
        err = _lib.couchstore_open_local_document(self.couchstore,
                                                  _id,
                                                  ctypes.c_size_t(len(_id)),
                                                  ctypes.byref(docptr))
        if err == -5 or (err == 0 and docptr.contents.deleted):
            raise KeyError(_id)
        _check(err)
        value = str(docptr.contents.json)
        _lib.couchstore_free_document(docptr)
        return value

    def __setitem__(self, key, value):
        """Saves a local document with the given ID, or deletes it if the value
        is None."""
        idbuf = SizedBuf(key)
        doc = LocalDocStruct(idbuf)
        if value is not None:
            doc.json = SizedBuf(value)
        else:
            doc.deleted = True
        _check(_lib.couchstore_save_local_document(self.couchstore,
                                                   ctypes.byref(doc)))

    def __delitem__(self, key):
        self.__setitem__(key, None)


class CouchStore(object):
    """Interface to a CouchStore database."""

    ITERATORFUNC = ctypes.CFUNCTYPE(ctypes.c_int,
                                    ctypes.c_void_p,
                                    ctypes.POINTER(DocInfoStruct),
                                    ctypes.c_void_p)

    COMPRESS = 1

    DECOMPRESS = 1

    def __init__(self, path, mode=None):
        """Creates a CouchStore at a given path. The option mode parameter can
        be 'r' for read-only access, or 'c' to create the file if it doesn't
        already exist."""
        if mode == 'r':
            flags = 2  # RDONLY
        elif mode == 'c':
            flags = 1  # CREATE
        else:
            flags = 0

        db = ctypes.c_void_p()
        _check(_lib.couchstore_open_db(path,
                                       ctypes.c_uint64(flags),
                                       ctypes.byref(db)))
        self._as_parameter_ = db
        self.path = path

    def __del__(self):
        self.close()

    def close(self):
        """Closes the CouchStore."""
        if hasattr(self, "_as_parameter_"):
            _lib.couchstore_close_db(self)
            del self._as_parameter_

    def __str__(self):
        return "CouchStore(%s)" % self.path

    @property
    def db_info(self):
        """Returns an object with information about the database. Its
        properties are filename, last_sequence, doc_count, deleted_count,
        space_used, header_position."""
        info = DbInfoStruct()
        _check(_lib.couchstore_db_info(self, ctypes.byref(info)))
        return info

    def rewind_header(self):
        """Rewinds the database handle to the next-oldest committed header.
        Closes the handle if none can be found"""
        if hasattr(self, "_as_parameter_"):
            err = _lib.couchstore_rewind_db_header(self)
            if err != 0:
                del self._as_parameter_
            _check(err)

    def save(self, _id, data, options=0):
        """Saves a document with the given ID. Returns the sequence number."""
        if isinstance(_id, DocumentInfo):
            info_struct = _id.as_struct()
            idbuf = info_struct.id
        else:
            idbuf = SizedBuf(_id)
            info_struct = DocInfoStruct(idbuf)
        if data is not None:
            doc = DocStruct(idbuf, SizedBuf(data))
            docref = ctypes.byref(doc)
            if options & CouchStore.COMPRESS:
                info_struct.content_meta |= 0x80
        else:
            docref = None
        _check(_lib.couchstore_save_document(self, docref,
                                             ctypes.byref(info_struct),
                                             ctypes.c_uint64(options)))
        if isinstance(_id, DocumentInfo):
            _id.sequence = info_struct.db_seq
        return info_struct.db_seq

    def save_multi(self, ids, datas, options=0):
        """Saves multiple documents. 'ids' is an array of either strings or
        DocumentInfo objects. 'datas' is a parallel array of value strings (or
        None, in which case the documents will be deleted.) Returns an array of
        new sequence numbers."""
        n = len(ids)
        doc_structs = (ctypes.POINTER(DocStruct) * n)()
        info_structs = (ctypes.POINTER(DocInfoStruct) * n)()
        for i in xrange(0, n):
            _id = ids[i]
            if isinstance(_id, DocumentInfo):
                info = _id.as_struct()
            else:
                info = DocInfoStruct(SizedBuf(_id))
            doc = DocStruct(info.id)
            if datas and datas[i]:
                doc.data = SizedBuf(datas[i])
            else:

                info.deleted = True
            info_structs[i] = ctypes.pointer(info)
            doc_structs[i] = ctypes.pointer(doc)
        _check(_lib.couchstore_save_documents(self,
                                              ctypes.byref(doc_structs),
                                              ctypes.byref(info_structs),
                                              ctypes.c_uint(n),
                                              ctypes.c_uint64(options)))
        return [info.contents.db_seq for info in info_structs]

    def commit(self):
        """Ensures all saved data is flushed to disk."""
        _check(_lib.couchstore_commit(self))

    def get(self, _id, options=0):
        """Returns the contents of a document (as a string) given its ID."""
        _id = _to_string(_id)
        docptr = ctypes.pointer(DocStruct())
        err = _lib.couchstore_open_document(self,
                                            _id,
                                            ctypes.c_size_t(len(_id)),
                                            ctypes.byref(docptr),
                                            options)
        if err == -5:
            raise KeyError(_id)
        _check(err)
        data = str(docptr.contents.data)
        _lib.couchstore_free_document(docptr)
        return data

    def __getitem__(self, key):
        return self.get(key)

    def __setitem__(self, key, value):
        self.save(key, value)

    def __delitem__(self, key):
        self.save(key, None)

    def _info_ptr_to_doc(self, key, infoptr, err):
        """Getting document info"""
        if err == -5:
            raise KeyError(key)
        _check(err)
        info = infoptr.contents
        if info is None:
            return None
        doc = DocumentInfo.from_struct(info, self)
        _lib.couchstore_free_docinfo(infoptr)
        return doc

    def get_doc_info_by_id(self, _id):
        """Returns the DocumentInfo object with the given ID."""
        _id = _to_string(_id)
        infoptr = ctypes.pointer(DocInfoStruct())
        err = _lib.couchstore_docinfo_by_id(self,
                                            _id,
                                            ctypes.c_size_t(len(_id)),
                                            ctypes.byref(infoptr))
        return self._info_ptr_to_doc(_id, infoptr, err)

    def get_doc_info_by_sequence(self, sequence):
        """Returns the DocumentInfo object with the given sequence number."""
        infoptr = ctypes.pointer(DocInfoStruct())
        err = _lib.couchstore_docinfo_by_sequence(self,
                                                  ctypes.c_ulonglong(sequence),
                                                  ctypes.byref(infoptr))
        return self._info_ptr_to_doc(sequence, infoptr, err)

    def for_each_change(self, since, fn):
        """Calls the function "fn" once for every document sequence since the
        "since" parameter. The single parameter to "fn" will be a DocumentInfo
        object. You can call get_contents() on it to get the document contents.
        """
        def callback(db_ptr, doc_info_ptr, context):
            fn(DocumentInfo.from_struct(doc_info_ptr.contents, self))
            return 0
        _check(_lib.couchstore_changes_since(self,
                                             ctypes.c_uint64(since),
                                             ctypes.c_uint64(0),
                                             CouchStore.ITERATORFUNC(callback),
                                             ctypes.c_void_p(0)))

    def changes_since(self, since):
        """Returns an array of DocumentInfo objects, for every document that's
        changed since the sequence number "since"."""
        changes = []
        self.for_each_change(since, lambda doc_info: changes.append(doc_info))
        return changes

    def for_each_doc(self, start_key, end_key, fn):
        def callback(db_ptr, doc_info_ptr, context):
            fn(DocumentInfo.from_struct(doc_info_ptr.contents, self))
            return 0

        ids = (SizedBuf * 2)()
        num_ids = 1
        if start_key:
            ids[0] = SizedBuf(start_key)
        if end_key:
            ids[1] = SizedBuf(end_key)
            num_ids = 2
        _check(_lib.couchstore_docinfos_by_id(self,
                                              ids,
                                              ctypes.c_uint(num_ids),
                                              ctypes.c_uint64(1),
                                              CouchStore.ITERATORFUNC(callback),
                                              ctypes.c_void_p(0)))

    def changes_count(self, minimum, maximum):
        cstruct = CounterStruct()
        err = _lib.couchstore_changes_count(self,
                                            ctypes.c_uint64(minimum),
                                            ctypes.c_uint64(maximum),
                                            ctypes.pointer(cstruct))
        _check(err)
        return cstruct.count

    @property
    def local_docs(self):
        """A simple dictionary-like object that accesses the CouchStore's local
        documents."""
        return LocalDocs(self)
