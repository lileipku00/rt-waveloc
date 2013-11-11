import unittest

def suite():
    suite = unittest.TestSuite()
    suite.addTest(SetupTests('test_setup'))
    return suite

def setUpModule():

    pass


class SetupTests(unittest.TestCase):

    def test_setup(self):
        self.assertTrue(True)

if __name__ == '__main__':

  import test_io
  import test_processing
  import test_nllstuff
  import test_hdf5
  import test_migration
  import test_rtwl 

  suite_list=[suite(),
    test_io.suite(),
    test_processing.suite(),
    test_nllstuff.suite(),
    test_hdf5.suite(),
    test_migration.suite(),    
    test_rtwl.suite(),
    ]

  alltests=unittest.TestSuite(suite_list)

  unittest.TextTestRunner(verbosity=2).run(alltests)
 
