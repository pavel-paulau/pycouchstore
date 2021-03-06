import os
import struct
import unittest

from couchstore import CouchStore, DocumentInfo, SizedBuf


def removeIfExists(path):
    try:
        os.remove(path)
    except OSError:
        pass


class NonexistentCouchStoreTest(unittest.TestCase):

    def testNonexistent(self):
        removeIfExists("/tmp/nonexistent.couch")
        self.assertRaises(OSError, CouchStore, "/tmp/nonexistent.couch")


class CouchStoreTest (unittest.TestCase):

    def setUp(self):
        removeIfExists("/tmp/test.couch")
        self.store = CouchStore("/tmp/test.couch", 'c')

    def tearDown(self):
        self.store.close()
        os.remove("/tmp/test.couch")

    def testBasicSave(self):
        sequence = self.store.save("foo", "value of foo")
        self.assertEqual(sequence, 1)
        self.store.commit()
        value = self.store.get("foo")
        self.assertEqual(value, "value of foo")

    def testMissingKey(self):
        self.assertRaises(KeyError, self.store.get, "nonexistent")

    def testBadKey(self):
        self.assertRaises(TypeError, self.store.get, 0)
        self.assertRaises(TypeError, self.store.get, None)
        self.assertRaises(TypeError, self.store.get, [123])

    def testInfo(self):
        value = "value"
        sequence = self.store.save("foo", value)
        self.assertEqual(sequence, 1)
        info = self.store.get_doc_info_by_id("foo")
        self.assertEqual(info.id, "foo")
        self.assertEqual(info.sequence, sequence)
        self.assertFalse(info.deleted)
        #self.assertEqual(info.size, len(value))   #FIXME: Not currently equal, due to bug in CouchStore itself
        self.assertEqual(info.get_contents(), value)

    def testInfoBySequence(self):
        value = "value"
        sequence = self.store.save("foo", value)
        self.assertEqual(sequence, 1)
        info = self.store.get_doc_info_by_sequence(sequence)
        self.assertEqual(info.id, "foo")
        self.assertEqual(info.sequence, sequence)
        self.assertFalse(info.deleted)
        #self.assertEqual(info.size, len(value))   #FIXME: Not currently equal, due to bug in CouchStore itself
        self.assertEqual(info.get_contents(), value)

    def testMissingSequence(self):
        self.store.save("foo", "value")
        self.assertRaises(KeyError, self.store.get_doc_info_by_sequence, 99999)
        self.assertRaises(TypeError, self.store.get_doc_info_by_sequence, "huh")

    def testNoContents(self):
        info = DocumentInfo("howdy")
        self.assertRaises(Exception, info.get_contents)

    def testMetadata(self):
        info = DocumentInfo("meta")
        info.rev_sequence = 23
        info.rev_meta = "fancy metadata here"
        info.content_type = DocumentInfo.INVALID_JSON
        self.store[info] = "the regular non-meta data"

        gotInfo = self.store.get_doc_info_by_id("meta")
        self.assertEquals(gotInfo.id, "meta")
        self.assertEquals(gotInfo.rev_sequence, info.rev_sequence)
        self.assertEquals(gotInfo.rev_meta, info.rev_meta)
        self.assertEquals(gotInfo.content_type, info.content_type)
        self.assertFalse(gotInfo.compressed)

    def testMetadataSave(self):
        info = DocumentInfo("meta")
        info.rev_sequence = 23
        info.rev_meta = "fancy metadata here"
        info.content_type = DocumentInfo.INVALID_JSON
        self.store[info] = "the regular non-meta data"

        self.store.commit()
        self.store.close()
        self.store = CouchStore("/tmp/test.couch", 'r')

        gotInfo = self.store.get_doc_info_by_id("meta")
        self.assertEquals(gotInfo.id, "meta")
        self.assertEquals(gotInfo.rev_sequence, info.rev_sequence)
        self.assertEquals(gotInfo.rev_meta, info.rev_meta)
        self.assertEquals(gotInfo.content_type, info.content_type)
        self.assertFalse(gotInfo.compressed)

    def testCompression(self):
        value = "this value is text and text is valued"
        self.store.save("key", value, CouchStore.COMPRESS)
        self.assertEqual(self.store.get("key", CouchStore.DECOMPRESS), value)
        info = self.store.get_doc_info_by_id("key")
        self.assertTrue(info.compressed)

    def expectedKey(self, i):
        return "key_%2d" % (i + 1)

    def expectedValue(self, i):
        return "Hi there! I'm value #%d!" % (i + 1)

    def addDocs(self, n):
        for i in xrange(n):
            self.store.save(self.expectedKey(i), self.expectedValue(i))

    def addBulkDocs(self, n):
        ids = [self.expectedKey(i) for i in xrange(n)]
        datas = [self.expectedValue(i) for i in xrange(n)]
        self.store.save_multi(ids, datas)

    def testMultipleDocs(self):
        self.addDocs(1000)
        for i in xrange(1000):
            self.assertEqual(self.store[self.expectedKey(i)], self.expectedValue(i))

        info = self.store.db_info
        self.assertEquals(info.filename, "/tmp/test.couch")
        self.assertEquals(info.last_sequence, 1000)
        self.assertEquals(info.doc_count, 1000)
        self.assertEquals(info.deleted_count, 0)

    def testBulkDocs(self):
        self.addBulkDocs(1000)
        for i in xrange(1000):
            self.assertEqual(self.store[self.expectedKey(i)], self.expectedValue(i))

    def testDelete(self):
        self.store["key"] = "value"
        del self.store["key"]
        self.assertRaises(KeyError, self.store.get, "key")
        info = self.store.get_doc_info_by_id("key")
        self.assertTrue(info.deleted)
        self.assertEqual(info.id, "key")

        info = self.store.db_info
        self.assertEquals(info.last_sequence, 2)
        self.assertEquals(info.doc_count, 0)
        self.assertEquals(info.deleted_count, 1)

    def testChangesSince(self):
        self.addDocs(50)
        changes = self.store.changes_since(0)
        self.assertEqual(len(changes), 50)
        for i in xrange(50):
            self.assertEqual(changes[i].id, self.expectedKey(i))

    def testForAllDocs(self):
        self.addDocs(50)
        docCount = [0]

        def checkDoc(docInfo):
            self.assertEquals(docInfo.id, self.expectedKey(docCount[0]))
            docCount[0] += 1

        self.store.for_each_doc(None, None, checkDoc)
        self.assertEqual(docCount[0], 50)

    def testDocumetInfoRepr(self):
        self.addDocs(1)

        def checkDoc(docInfo):
            expected = "DocumentInfo('%s', %d bytes)" % (docInfo.id,
                                                         docInfo.physSize)
            self.assertEquals(str(docInfo), expected)
            self.assertEquals(repr(docInfo), expected)

        self.store.for_each_doc(None, None, checkDoc)

    def testForSomeDocs(self):
        self.addDocs(50)
        docCount = [0]

        def checkDoc(docInfo):
            self.assertEquals(docInfo.id, self.expectedKey(docCount[0]))
            docCount[0] += 1

        self.store.for_each_doc(None, self.expectedKey(10), checkDoc)
        self.assertEqual(docCount[0], 11)

        docCount = [10]
        self.store.for_each_doc(self.expectedKey(10), None, checkDoc)
        self.assertEqual(docCount[0], 50)

        docCount = [10]
        self.store.for_each_doc(self.expectedKey(10), self.expectedKey(20), checkDoc)
        self.assertEqual(docCount[0], 21)

    def testLocalDocs(self):
        locals = self.store.local_docs
        self.assertRaises(KeyError, locals.__getitem__, "hello")
        locals["hello"] = "goodbye"
        self.assertEqual(locals["hello"], "goodbye")
        locals["hello"] = "bonjour"
        self.assertEqual(locals["hello"], "bonjour")
        del locals["hello"]
        self.assertRaises(KeyError, locals.__getitem__, "hello")

    def testSizedBuf(self):
        # Converting Python strings to/from SizedBufs is tricky enough (when
        # the strings might contain null bytes) that it's worth a unit test of
        # its own.
        data = "foooooobarrrr"
        buf = SizedBuf(data)
        self.assertEqual(buf.size, len(data))
        self.assertEqual(str(buf), data)
        # Now try some binary data with nul bytes in it:
        data = "foo\000bar"
        buf = SizedBuf(data)
        self.assertEqual(buf.size, len(data))
        self.assertEqual(str(buf), data)

    def testBinaryMeta(self):
        # Make sure binary data, as produced by Python's struct module, works
        # in revMeta.
        packed = struct.pack(">QII", 0, 1, 2)
        d = DocumentInfo("bin")
        d.rev_meta = packed
        self.store[d] = "value"

        doc_info = self.store.get_doc_info_by_id("bin")
        self.assertEqual(doc_info.rev_meta, packed)
        i1, i2, i3 = struct.unpack(">QII", doc_info.rev_meta)
        self.assertEqual(i1, 0)
        self.assertEqual(i2, 1)
        self.assertEqual(i3, 2)

    def testMultipleMeta(self):
        k = []
        v = []
        for i in range(1000):
            d = DocumentInfo(str(i))
            d.rev_meta = "hello-%s" % i
            k.append(d)
            v.append("world-%s" % i)
        self.store.save_multi(k, v)
        self.store.commit()
        self.store.close()
        self.store = CouchStore("/tmp/test.couch", 'r')
        for doc_info in self.store.changes_since(0):
            i = int(doc_info.id)
            self.assertEqual(doc_info.rev_meta, "hello-%s" % i)
            doc_contents = doc_info.get_contents()
            self.assertEqual(doc_contents, "world-%s" % i)

    def testMultipleMetaStruct(self):
        k = []
        v = []
        for i in range(1000):
            d = DocumentInfo(str(i))
            d.rev_meta = struct.pack(">QII", i * 3, i * 2, i)
            k.append(d)
            v.append("world-%s" % i)
        self.store.save_multi(k, v)
        self.store.commit()
        self.store.close()
        self.store = CouchStore("/tmp/test.couch", 'r')
        for doc_info in self.store.changes_since(0):
            i = int(doc_info.id)
            i3, i2, i1 = struct.unpack(">QII", doc_info.rev_meta)
            self.assertEqual(i3, i * 3)
            self.assertEqual(i2, i * 2)
            self.assertEqual(i1, i * 1)
            doc_contents = doc_info.get_contents()
            self.assertEqual(doc_contents, "world-%s" % doc_info.id)


if __name__ == '__main__':
    unittest.main()
