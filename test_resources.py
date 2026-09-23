import os
import tempfile
from pathlib import Path
import unittest
from resource_monitor import parse_gpu, process_tree

class ResourceTests(unittest.TestCase):
    def test_gpu_filters_other_apps_and_handles_missing_metrics(self):
        data = '# gpu pid type sm mem enc dec fb command\n0 12 C 42 3 0 0 900 python\n0 99 C 90 5 0 0 800 other\n'
        self.assertEqual(parse_gpu(data, {12}), (42,900))
        self.assertEqual(parse_gpu(data, {88}), (0,0))
        self.assertEqual(parse_gpu(data.replace('42 3 0 0 900','- - - - -'), {12}), (None,None))
        self.assertEqual(parse_gpu('unsupported', {12}), (None,None))

    def test_process_tree_includes_grandchildren_not_unrelated(self):
        with tempfile.TemporaryDirectory() as d:
            for pid, parent in [(10,1),(11,10),(12,11),(99,1)]:
                path=Path(d)/str(pid);path.mkdir()
                fields=['0']*22
                fields[0]='S';fields[1]=str(parent);fields[11]='20';fields[12]='5';fields[19]='123';fields[21]='10'
                (path/'stat').write_text(f'{pid} (name with ) spaces) '+ ' '.join(fields))
            tree=process_tree(10,Path(d))
            self.assertEqual(set(tree),{10,11,12})
            self.assertEqual(tree[12][2],25)
            self.assertEqual(tree[12][3],10*os.sysconf('SC_PAGE_SIZE'))
